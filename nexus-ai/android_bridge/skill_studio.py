"""NEXUS 2.2 Skill Studio: generate, test and propose tiny *safe* new modules.

A model may design a declarative text transformer and test vectors. NEXUS
validates and tests the declarative program with a bounded interpreter, then
renders reviewable Python source. The Python source is NEVER imported, eval'ed
or exec'ed. Owner approval is required before any learned skill can run.
Device/firmware/system actions require a separately implemented adapter.
"""
from __future__ import annotations

import ast
import hashlib
import json
import os
import re
import sqlite3
import threading
import time
from contextlib import contextmanager
from pathlib import Path

from ai_provider import post_json, ProviderError

BASE = Path(os.getenv("NEXUS_STUDIO_HOME", str(Path.home()/".nexus_ai"/"skill_studio")))
DATABASE = BASE/"studio.sqlite3"
LOCK = threading.RLock()
MAX_INPUT = 600
MAX_OUTPUT = 800
MAX_STEPS = 7
MAX_TESTS = 6
MAX_DRAFTS = 40
NAME_RE = re.compile(r"^[a-z][a-z0-9_]{2,39}$")
VOICE_RE = re.compile(r"^(?:нексус[,!: ]+)?(?:примени\s+)?навык\s+(\d{1,5})\s*[:：]\s*(.{1,600})$",re.I|re.S)
OPERATIONS = {
    "strip","lower","upper","title","casefold","collapse_spaces",
    "replace","prefix","suffix","remove_chars",
}
NEEDS_ADAPTER = (
    "отправ", "напиши", "сообщен", "sms", "смс", "телеграм", "telegram",
    "whatsapp", "ватсап", "доступ", "разрешен", "позвон", "звонок",
    "прошив", "микроконтроллер", "m5stick", "стик", "firmware", "android",
    "root", "термукс", "termux", "файл", "каталог", "скачай", "установ",
    "камера", "снимок", "фото", "запусти приложение", "перезагруз", "удали",
    "покуп", "оплат", "сети", "систем", "shell", "выполни код",
)
SECRET = re.compile(
    r"(?i)(?:AIza[A-Za-z0-9_-]{15,}|sk-(?:proj-)?[A-Za-z0-9_-]{15,}|"
    r"github_pat_[A-Za-z0-9_]+|\b(?:пароль|токен|api[ -_]?key|"
    r"password|private key)\b)"
)

def _safe_text(value, max_chars=MAX_INPUT):
    if not isinstance(value,str) or not value or len(value)>max_chars:
        raise ValueError("Текст отсутствует или превышает лимит.")
    if any(ord(c)<32 and c not in "\n\t" for c in value) or SECRET.search(value):
        raise ValueError("Текст содержит управляющие символы или потенциальный секрет.")
    return value

def _adapter_required(goal: str) -> bool:
    text=goal.casefold().replace("ё","е")
    return any(word in text for word in NEEDS_ADAPTER)

def _verify_step(step):
    if not isinstance(step,dict):
        raise ValueError("Неверный шаг программы.")
    operation=step.get("op")
    if operation not in OPERATIONS:
        raise ValueError("Недопустимая операция: "+str(operation)[:45])
    needs = ({"from","to"} if operation=="replace" else
             {"value"} if operation in ("prefix","suffix","remove_chars") else set())
    if set(step)!={"op",*needs}:
        raise ValueError("У операции недопустимые параметры.")
    for key in needs:
        value=step[key]
        if not isinstance(value,str) or len(value)>100:
            raise ValueError("Параметр слишком длинный.")
        if SECRET.search(value) or any(ord(c)<32 for c in value):
            raise ValueError("Опасное содержимое параметра.")
    if operation=="replace" and not step["from"]:
        raise ValueError("Замена пустой строки запрещена.")
    return {k:step[k] for k in (["op"]+sorted(needs))}

def validate(spec: dict) -> dict:
    if not isinstance(spec,dict) or set(spec)!={"name","description","steps","tests"}:
        raise ValueError("Неверная схема модуля.")
    name=spec["name"]
    if not isinstance(name,str) or not NAME_RE.fullmatch(name):
        raise ValueError("Недопустимое имя модуля.")
    description=_safe_text(spec["description"],180)
    if not isinstance(spec["steps"],list) or not 1<=len(spec["steps"])<=MAX_STEPS:
        raise ValueError("Модуль должен содержать 1–7 шагов.")
    if not isinstance(spec["tests"],list) or not 2<=len(spec["tests"])<=MAX_TESTS:
        raise ValueError("Модуль должен иметь 2–6 тестовых примеров.")
    steps=[_verify_step(step) for step in spec["steps"]]
    tests=[]
    for item in spec["tests"]:
        if not isinstance(item,dict) or set(item)!={"input","expected"}:
            raise ValueError("Некорректный тестовый пример.")
        test_input=_safe_text(item["input"],MAX_INPUT)
        expected=item["expected"]
        if not isinstance(expected,str) or len(expected)>MAX_OUTPUT or SECRET.search(expected):
            raise ValueError("Некорректный ожидаемый результат.")
        tests.append({"input":test_input,"expected":expected})
    if len({r["input"] for r in tests})<2:
        raise ValueError("Два теста должны иметь разные исходные строки.")
    return {"name":name,"description":description,"steps":steps,"tests":tests}

def run_steps(steps, text: str) -> str:
    """The only execution engine; no Python eval, imports, shell or filesystem."""
    if not isinstance(text,str) or len(text)>MAX_INPUT:
        raise ValueError("Длина входного текста превышена.")
    result=text
    for step in steps:
        step=_verify_step(step)
        op=step["op"]
        if op=="strip":result=result.strip()
        elif op=="lower":result=result.lower()
        elif op=="upper":result=result.upper()
        elif op=="title":result=result.title()
        elif op=="casefold":result=result.casefold()
        elif op=="collapse_spaces":result=" ".join(result.split())
        elif op=="replace":result=result.replace(step["from"],step["to"])
        elif op=="prefix":result=step["value"]+result
        elif op=="suffix":result+=step["value"]
        elif op=="remove_chars":result=result.translate(str.maketrans("","",step["value"]))
        if len(result)>MAX_OUTPUT:
            raise ValueError("Результат слишком длинный.")
    return result

def test_spec(spec: dict) -> list[dict]:
    result=[]
    for test in spec["tests"]:
        try:
            received=run_steps(spec["steps"],test["input"])
            result.append({"input":test["input"],"expected":test["expected"],
                           "passed":received==test["expected"],
                           "actual":received})
        except (ValueError,TypeError) as exc:
            result.append({"input":test["input"],"expected":test["expected"],
                           "passed":False,"actual":str(exc)[:100]})
    return result

def render_source(spec: dict) -> str:
    """Source is a *review artifact*. Runtime never imports/executes it."""
    lines=[
        '# NEXUS Skill Studio · generated from validated declarative steps',
        '# REVIEW ONLY: this file is NOT imported or executed by NEXUS.',
        'def transform(text: str) -> str:',
        '    text = str(text)',
        '    if len(text) > 600: raise ValueError("input too long")',
    ]
    for st in spec["steps"]:
        op=st["op"]
        if op in ("strip","lower","upper","title","casefold"):
            lines.append("    text = text."+op+"()")
        elif op=="collapse_spaces":
            lines.append('    text = " ".join(text.split())')
        elif op=="replace":
            lines.append("    text = text.replace("+repr(st["from"])+", "+repr(st["to"])+")")
        elif op=="prefix":
            lines.append("    text = "+repr(st["value"])+" + text")
        elif op=="suffix":
            lines.append("    text = text + "+repr(st["value"]))
        elif op=="remove_chars":
            lines.append("    text = text.translate(str.maketrans('', '', "+repr(st["value"])+"))")
        lines.append('    if len(text) > 800: raise ValueError("output too long")')
    lines.append("    return text")
    source="\n".join(lines)+"\n"
    ast.parse(source)
    return source

def _initialize():
    BASE.mkdir(parents=True,exist_ok=True,mode=0o700)
    if BASE.is_symlink() or DATABASE.is_symlink():
        raise RuntimeError("Хранилище модулей — небезопасная ссылка.")
    BASE.chmod(0o700)
    if not DATABASE.exists():
        try:
            fd=os.open(DATABASE,os.O_CREAT|os.O_EXCL|os.O_WRONLY,0o600)
            os.close(fd)
        except FileExistsError:
            if DATABASE.is_symlink():raise RuntimeError("База модулей является ссылкой.")
    DATABASE.chmod(0o600)

@contextmanager
def db():
    with LOCK:
        _initialize()
        c=sqlite3.connect(DATABASE,timeout=10)
        try:
            c.execute("PRAGMA busy_timeout=10000")
            c.execute("""CREATE TABLE IF NOT EXISTS modules(
                id INTEGER PRIMARY KEY, task TEXT NOT NULL, name TEXT NOT NULL,
                status TEXT NOT NULL, spec TEXT NOT NULL, source_sha256 TEXT NOT NULL,
                report TEXT NOT NULL, reason TEXT NOT NULL, created INTEGER NOT NULL
            )""")
            yield c
            c.commit()
        except BaseException:
            c.rollback()
            raise
        finally:
            c.close()

def _save(task, spec=None, report=None, status="needs_adapter", reason=""):
    spec = spec or {}
    report = report or []
    with db() as c:
        c.execute("DELETE FROM modules WHERE id NOT IN "
                  "(SELECT id FROM modules ORDER BY id DESC LIMIT ?)",(MAX_DRAFTS-1,))
        filetext=render_source(spec) if spec else ""
        digest=hashlib.sha256(filetext.encode("utf-8")).hexdigest()
        cur=c.execute("INSERT INTO modules(task,name,status,spec,source_sha256,report,reason,created) "
                      "VALUES(?,?,?,?,?,?,?,?)",
                      (task[:350],spec.get("name",""),status,
                       json.dumps(spec,ensure_ascii=False),digest,
                       json.dumps(report,ensure_ascii=False),reason[:300],int(time.time())))
        ident=cur.lastrowid
        if spec:
            folder=BASE/str(ident)
            folder.mkdir(mode=0o700,exist_ok=False)
            for filename, body in (("module.py",filetext),
                                   ("tests.json",json.dumps(spec["tests"],ensure_ascii=False,indent=2)),
                                   ("report.json",json.dumps(report,ensure_ascii=False,indent=2))):
                target=folder/filename
                with target.open("x",encoding="utf-8") as out:
                    out.write(body)
                target.chmod(0o600)
    return get(ident)

def get(ident):
    with db() as c:
        row=c.execute("SELECT id,task,name,status,spec,source_sha256,report,reason,created "
                      "FROM modules WHERE id=?",(int(ident),)).fetchone()
    if not row:return None
    ident,task,name,status,spec,digest,report,reason,created=row
    return {"id":ident,"task":task,"name":name,"status":status,
            "spec":json.loads(spec),"sha256":digest,"report":json.loads(report),
            "reason":reason,"created":created}

def listing(limit=12):
    with db() as c:
        rows=c.execute("SELECT id,task,name,status,reason FROM modules "
                       "ORDER BY id DESC LIMIT ?",(min(max(int(limit),1),MAX_DRAFTS),)).fetchall()
    return [dict(zip(("id","task","name","status","reason"),row)) for row in rows]

def _model_spec(task):
    key=os.environ.get("GEMINI_API_KEY","").strip()
    if not key:raise ProviderError("Для создания модуля нужен ключ Gemini.")
    model=os.environ.get("GEMINI_MODEL","gemini-2.5-flash-lite")
    if not re.fullmatch(r"[A-Za-z0-9._-]{3,90}",model):
        raise ValueError("Недопустимая модель Gemini.")
    instruction=(
      "Ты проектировщик NEXUS Skill Studio. Сформируй строго JSON объект: "
      "name (латинский snake_case 3-40), description (до 180 символов), "
      "steps (массив 1-7 объектов), tests (2-6 объектов input/expected). "
      "Доступные операции: strip, lower, upper, title, casefold, "
      "collapse_spaces, replace(from,to), prefix(value), suffix(value), remove_chars(value). "
      "Каждый шаг только {op и нужные поля}, тексты до 100 символов. "
      "Модуль должен решать запрос пользователя без доступа к файлам, телефону, сети, "
      "Android, системным командам, Python и личным данным. "
      "Не включай код Python в JSON. Укажи два разных реалистичных теста и их точные результаты. "
      'Если не можешь реализовать задачу этими операциями, верни {"unsupported":"причина"}. '
      "Никогда не выдавай ложные способности. Техническое задание: "+task
    )
    result=post_json(
      "https://generativelanguage.googleapis.com/v1beta/models/"+model+":generateContent",
      {"contents":[{"role":"user","parts":[{"text":instruction}]}],
       "generationConfig":{"responseMimeType":"application/json","maxOutputTokens":1500}},
      {"x-goog-api-key":key},timeout=25)
    try:
        raw="".join(str(p.get("text","")) for p in result["candidates"][0]["content"]["parts"])
        return json.loads(raw)
    except (KeyError,TypeError,IndexError,ValueError) as exc:
        raise ValueError("Gemini вернул некорректный JSON модуля.") from exc

def propose(task):
    task=_safe_text(str(task).strip(),350)
    # Do not let private secrets or device actions reach the generator.
    if _adapter_required(task):
        return _save(task,status="needs_adapter",
                     reason="Для действий на устройстве нужен проверенный Android/M5-адаптер. "
                            "Генерировать и запускать произвольный код на телефоне нельзя.")
    # Reuse an already staged proposal instead of consuming API quota every time.
    for candidate in listing(12):
        if candidate["task"].casefold()==task.casefold() and candidate["status"] in ("tested","active"):
            return get(candidate["id"])
    try:
        payload=_model_spec(task)
        if isinstance(payload,dict) and "unsupported" in payload:
            return _save(task,status="needs_adapter",
                         reason=str(payload["unsupported"])[:300])
        spec=validate(payload)
        report=test_spec(spec)
        good=sum(item["passed"] for item in report)
        outcome="tested" if good==len(report) else "failed"
        reason="" if outcome=="tested" else "Тесты не прошли: "+str(good)+"/"+str(len(report))
        return _save(task,spec,report,outcome,reason)
    except (ValueError,TypeError,ProviderError,OSError) as exc:
        return _save(task,status="failed",reason=str(exc)[:280])

def approve(ident):
    """Explicit menu-only action, revalidates tests and saved source digest."""
    proposal=get(ident)
    if not proposal or proposal["status"] not in ("tested","active"):
        return False,"Модуль не прошёл тесты или не найден."
    if proposal["status"]=="active":
        return True,"Навык уже включён."
    try:
        spec=validate(proposal["spec"])
        report=test_spec(spec)
        source=render_source(spec)
        original=BASE/str(proposal["id"])/"module.py"
        if original.is_symlink() or original.read_bytes()!=source.encode("utf-8"):
            return False,"Файл черновика изменён; активация запрещена."
        if hashlib.sha256(source.encode("utf-8")).hexdigest()!=proposal["sha256"]:
            return False,"Контрольная сумма не совпадает."
        if not all(item["passed"] for item in report):
            return False,"Повторная проверка не прошла."
        with db() as c:
            c.execute("UPDATE modules SET status='active' WHERE id=? AND status='tested'",
                      (proposal["id"],))
        return True,("Навык "+str(proposal["id"])+" активирован. "
                     "Скажи «навык "+str(proposal["id"])+": текст», чтобы его применить.")
    except (OSError,TypeError,ValueError) as exc:
        return False,"Проверка активации завершилась ошибкой: "+str(exc)[:100]

def disable(ident):
    with db() as c:
        row=c.execute("UPDATE modules SET status='disabled' WHERE id=? AND status='active'",
                      (int(ident),))
        return row.rowcount==1

def run(ident,content):
    module=get(ident)
    if not module or module["status"]!="active":
        raise ValueError("Навык ещё не включён.")
    return run_steps(validate(module["spec"])["steps"],content)

def handle_voice(text):
    matched=VOICE_RE.fullmatch(str(text).strip())
    if not matched:return None
    try:
        answer=run(int(matched.group(1)),matched.group(2))
        return "Результат навыка "+matched.group(1)+": "+answer
    except (ValueError,TypeError,OSError) as exc:
        return "Навык недоступен: "+str(exc)[:150]
