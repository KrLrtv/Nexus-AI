"""NEXUS 2.1: explicit "научись" capability learning with bounded Android adapters.

No model-generated code is ever executed. Real outgoing SMS requires the
owner's explicit final approval of BOTH the contact and the message text.
Skills remain local, permissions are granted only by Android, and requests
are not sent to Gemini during the local SMS workflow.
"""
from __future__ import annotations
import json
import os
from contextlib import contextmanager
import re
import secrets
import shutil
import sqlite3
import subprocess
import threading
import time
from pathlib import Path

DB_PATH = Path(os.environ.get("NEXUS_SKILLS_DB", str(Path.home()/".nexus_ai"/"skills_v2.sqlite3")))
LOCK = threading.RLock()
TTL = 15*60
PENDING_TTL = 60*60
MAX_SMS = 420
PREFIX = re.compile(r"^(?:нексус|nexus)[,!: -]+\s*", re.I)
MESSAGE_CMD = re.compile(
    r"^(?:напиши|отправь|пошли|передай)\s+(?:(?:смс|sms|сообщение)\s+)?"
    r"(?:(?:контакту|для)\s+)?([а-яёa-z][а-яёa-z -]{1,42})[?.!]*$",
    re.I)
SKIP_RECIPIENTS = {"текст", "код", "стих", "историю", "рассказ", "книгу", "заметку",
                   "письмо", "мне", "тебе", "сообщение", "смс", "sms"}
FAMILY = {"папе":"папа","маме":"мама","брату":"брат","сестре":"сестра",
          "другу":"друг","подруге":"подруга","жене":"жена","мужу":"муж",
          "дедушке":"дедушка","бабушке":"бабушка"}
REFUSAL = ("не могу", "не умею", "нет возможности", "нет доступа",
           "не поддержива", "недоступн", "пока не могу", "cannot", "unable")
PHONE = re.compile(r"^\+?[0-9]{7,15}$")
LEARN = {"научись", "научи себя", "освой это", "научись этому"}
CANCEL = {"отмена", "отмени", "стоп", "не отправляй", "не надо"}
CONFIRM = {"подтверждаю отправку", "подтверждаю смс"}
SENSITIVE_MSG = re.compile(r"(?i)\b(?:пароль|токен|api[ -]?key)\b")

def norm(s):
    return " ".join(re.findall(r"[а-яa-z0-9]+",str(s).casefold().replace("ё","е")))

def recognize_intent(text):
    s = PREFIX.sub("",str(text).strip()).strip()
    m = MESSAGE_CMD.fullmatch(s)
    if not m:
        return None
    recipient = m.group(1).strip(" -")
    if len(recipient.split()) > 3 or norm(recipient) in SKIP_RECIPIENTS:
        return None
    # SMS is a specific transport; never pretend it sends Telegram/WhatsApp.
    if re.search(r"\b(телеграм|telegram|whatsapp|ватсап)\b",s,re.I):
        return None
    return {"kind":"sms", "recipient":recipient, "intent":s[:200]}

def _phone(value):
    stripped = re.sub(r"[ ()\-]", "", str(value))
    return stripped if PHONE.fullmatch(stripped) else None

def _redact(value):
    value=str(value)
    return ("••••"+value[-4:]) if len(value)>4 else "••••"

def _name_alias(value):
    v=norm(value)
    return FAMILY.get(v,v)

def _run(cmd, timeout=12):
    try:
        p=subprocess.run(cmd, text=True, capture_output=True,
                         timeout=timeout,check=False)
    except subprocess.TimeoutExpired:
        return False,"Истекло время ответа Termux:API. Проверь разрешения приложения Termux:API."
    except OSError:
        return False,"Ошибка запуска Termux:API."
    if p.returncode!=0:
        return False,"Termux:API не выполнил операцию. Проверь разрешения Android."
    return True,p.stdout

def status():
    return {"contacts_cli":bool(shutil.which("termux-contact-list")),
            "sms_cli":bool(shutil.which("termux-sms-send")),
            "configured":bool(load_skill("sms"))}

@contextmanager
def _connect():
    DB_PATH.parent.mkdir(mode=0o700,parents=True,exist_ok=True)
    if DB_PATH.is_symlink():
        raise RuntimeError("База навыков не должна быть символической ссылкой.")
    if not DB_PATH.exists():
        try:
            fd=os.open(DB_PATH,os.O_CREAT|os.O_EXCL|os.O_WRONLY,0o600)
            os.close(fd)
        except FileExistsError:
            if DB_PATH.is_symlink():
                raise RuntimeError("Небезопасная ссылка на базу навыков.")
    os.chmod(DB_PATH,0o600)
    c=sqlite3.connect(DB_PATH,timeout=10)
    try:
        c.execute("PRAGMA busy_timeout=10000")
        c.execute("""CREATE TABLE IF NOT EXISTS skill_library(
          kind TEXT PRIMARY KEY, status TEXT NOT NULL, metadata TEXT NOT NULL DEFAULT '{}',
          updated INTEGER NOT NULL)""")
        c.execute("""CREATE TABLE IF NOT EXISTS skill_goals(
          id INTEGER PRIMARY KEY, kind TEXT NOT NULL, intent TEXT NOT NULL,
          recipient TEXT NOT NULL DEFAULT '', status TEXT NOT NULL,
          created INTEGER NOT NULL)""")
        c.execute("""CREATE TABLE IF NOT EXISTS skill_sessions(
          id INTEGER PRIMARY KEY, goal_id INTEGER NOT NULL, stage TEXT NOT NULL,
          recipient TEXT NOT NULL, contact TEXT NOT NULL DEFAULT '',
          number TEXT NOT NULL DEFAULT '', message TEXT NOT NULL DEFAULT '',
          candidates TEXT NOT NULL DEFAULT '[]', created INTEGER NOT NULL,
          updated INTEGER NOT NULL)""")
        yield c
        c.commit()
    except BaseException:
        c.rollback()
        raise
    finally:
        c.close()

def load_skill(kind):
    with LOCK:
        with _connect() as c:
            row=c.execute("SELECT status,metadata FROM skill_library WHERE kind=?",(kind,)).fetchone()
    if not row:
        return None
    try:
        return {"status":row[0],"metadata":json.loads(row[1])}
    except (ValueError,TypeError):
        return None

def _set_skill(kind,state,metadata=None):
    with LOCK:
        with _connect() as c:
            c.execute("INSERT OR REPLACE INTO skill_library VALUES(?,?,?,?)",
                      (kind,state,json.dumps(metadata or {},ensure_ascii=False),int(time.time())))

def skills():
    with LOCK:
        with _connect() as c:
            rows=c.execute("SELECT kind,status,updated FROM skill_library ORDER BY updated DESC LIMIT 15").fetchall()
    return [{"kind":kind,"status":state,"updated":stamp} for kind,state,stamp in rows]

def pending_goal():
    with LOCK:
        with _connect() as c:
            row=c.execute("SELECT id,kind,intent,recipient FROM skill_goals "
                          "WHERE status='not_learned' AND created>? ORDER BY id DESC LIMIT 1",
                          (int(time.time())-PENDING_TTL,)).fetchone()
    return dict(zip(("id","kind","intent","recipient"),row)) if row else None

def add_goal(kind,intent,recipient=""):
    with LOCK:
        with _connect() as c:
            c.execute("DELETE FROM skill_goals WHERE id NOT IN "
                      "(SELECT id FROM skill_goals ORDER BY id DESC LIMIT 20)")
            c.execute("DELETE FROM skill_sessions WHERE updated<?",(int(time.time())-TTL,))
            cur=c.execute("INSERT INTO skill_goals(kind,intent,recipient,status,created) VALUES (?,?,?,?,?)",
                          (kind,intent[:350],recipient[:65],"not_learned",int(time.time())))
            return cur.lastrowid

def note_refusal(question,answer):
    """Called after a real provider refusal, never for arbitrary chat replies."""
    if not any(phrase in norm(answer) for phrase in REFUSAL):
        return False
    intent=recognize_intent(question)
    if intent:
        add_goal("sms",question,intent["recipient"])
    else:
        add_goal("unknown",str(question).strip()[:350])
    return True

def _active():
    with LOCK:
        with _connect() as c:
            c.execute("DELETE FROM skill_sessions WHERE updated<?",(int(time.time())-TTL,))
            row=c.execute("SELECT id,stage,goal_id,recipient,contact,number,message,candidates "
                          "FROM skill_sessions ORDER BY id DESC LIMIT 1").fetchone()
    return dict(zip(("id","stage","goal_id","recipient","contact",
                     "number","message","candidates"),row)) if row else None

def _start(goal_id,recipient):
    with LOCK:
        with _connect() as c:
            c.execute("DELETE FROM skill_sessions")
            cur=c.execute("INSERT INTO skill_sessions(goal_id,stage,recipient,created,updated) "
                          "VALUES(?,?,?,?,?)",(goal_id,"resolve",recipient,int(time.time()),int(time.time())))
            return cur.lastrowid

def _update(session, stage, **fields):
    allowed={"stage","contact","number","message","candidates"}
    fields=dict(fields)
    fields["stage"]=stage
    if set(fields)-allowed:
        raise ValueError("Unexpected workflow field")
    data=", ".join(x+"=?" for x in fields)
    with LOCK:
        with _connect() as c:
            cur=c.execute(f"UPDATE skill_sessions SET {data},updated=? WHERE id=?",
                          [*fields.values(),int(time.time()),session["id"]])
            return cur.rowcount==1

def _delete_session(session):
    with LOCK:
        with _connect() as c:
            c.execute("DELETE FROM skill_sessions WHERE id=?",(session["id"],))

def _resolve(session):
    if not shutil.which("termux-contact-list") or not shutil.which("termux-sms-send"):
        _update(session,"setup")
        return ("Чтобы освоить SMS, не хватает инструментов Termux:API. "
                "Открой меню NEXUS → Самообучение → Новые навыки → Подготовить SMS. "
                "Если Android попросит разрешения, предоставь их там.")
    ok,content=_run(["termux-contact-list"],timeout=16)
    if not ok:
        _update(session,"setup")
        return content+" После разрешения доступа скажи «научись» ещё раз."
    try:
        contacts=json.loads(content)
    except (ValueError,TypeError):
        _update(session,"setup")
        return ("Не удалось прочитать список контактов. Проверь Termux:API и разрешение на контакты.")
    if not isinstance(contacts,list):
        _update(session,"setup")
        return "Termux:API вернул неожиданный формат контактов."
    desired=_name_alias(session["recipient"])
    hits=[]
    for item in contacts[:5000]:
        if not isinstance(item,dict):continue
        name=str(item.get("name") or "").strip()[:90]
        number=_phone(item.get("number") or "")
        if number and _name_alias(name)==desired:
            if not any(x["number"]==number for x in hits):
                hits.append({"name":name,"number":number})
    if not hits:
        _update(session,"ask_number")
        return (f"Контакт «{session['recipient']}» не найден точно. "
                "Скажи «номер» и номер телефона или добавь контакт с этим именем в Android.")
    if len(hits)>1:
        hits=hits[:5]
        _update(session,"choose",candidates=json.dumps(hits,ensure_ascii=False))
        labels="; ".join(f'{idx}. {r["name"]} {_redact(r["number"])}'
                         for idx,r in enumerate(hits,1))
        return "Нашёл несколько номеров: "+labels+". Скажи «выбери» и номер варианта."
    match=hits[0]
    _update(session,"ask_text",contact=match["name"],number=match["number"])
    _set_skill("sms","prepared",{"method":"Termux:API"})
    return (f"Нашёл контакт «{match['name']}» ({_redact(match['number'])}). "
            "Инструмент отправки SMS доступен. Какой текст отправить?")

def _learn(goal):
    # NEXUS_AUTO_ANSWER_V205
    if goal["kind"] == "sms":
        _start(goal["id"], goal["recipient"])
        return _resolve(_active())
    try:
        from skill_autolearn import learn_and_answer
        result = learn_and_answer(goal["intent"])
    except Exception:
        return ("Сохранил задачу, но сейчас не могу подготовить проверенный модуль. "
                "Проверь Gemini и соединение, затем скажи «научись» ещё раз.")
    if result.active:
        with LOCK:
            with _connect() as c:
                c.execute("UPDATE skill_goals SET status='learned' WHERE id=?",
                          (goal["id"],))
        return result.reply
    return "Сохранил задачу. " + result.reply

def _process_session(session,txt):
    normed=norm(txt)
    stage=session["stage"]
    if stage=="setup":
        if normed in LEARN or normed in ("продолжи","попробуй снова","готово"):
            return _resolve(session)
        return "Настройка SMS ожидает разрешений Android. Скажи «научись», чтобы повторить проверку."
    if stage=="choose":
        m=re.fullmatch(r"(?:выбери\s*)?([1-5])",normed)
        if not m:
            return "Скажи «выбери 1», «выбери 2» и так далее."
        options=json.loads(session["candidates"])
        idx=int(m.group(1))-1
        if idx>=len(options):
            return "Такого варианта нет."
        row=options[idx]
        _update(session,"ask_text",contact=row["name"],number=row["number"],candidates="[]")
        return f"Выбран {row['name']} ({_redact(row['number'])}). Какой текст отправить?"
    if stage=="ask_number":
        m=re.fullmatch(r"номер\s+(.+)",txt,flags=re.I)
        number=_phone(m.group(1)) if m else None
        if number is None:
            return "Назови номер в формате «номер +79990000000» либо скажи «отмена»."
        _update(session,"ask_text",number=number,contact="Номер, названный владельцем")
        return "Номер принят ("+_redact(number)+"). Какой текст отправить?"
    if stage=="ask_text":
        body=txt.strip()
        if not body or len(body)>MAX_SMS:
            return "Текст SMS должен содержать от 1 до 420 символов."
        # Never mix a command with the content of the SMS; it remains data.
        if SENSITIVE_MSG.search(body):
            return "В сообщении похоже есть пароль или токен. Такие данные не отправляю через обучение."
        _update(session,"confirm",message=body)
        return (f'Проверка: SMS контакту «{session["contact"]}» '
                f'({_redact(session["number"])}): «{body}». '
                'Если всё верно, скажи «подтверждаю отправку». Иначе «отмена».')
    if stage=="confirm":
        if normed not in CONFIRM:
            return 'Сообщение не отправлено. Для отправки скажи «подтверждаю отправку», либо «отмена».'
        # One-shot transition before external effect: prevents double sends.
        with LOCK:
            with _connect() as c:
                changed=c.execute("UPDATE skill_sessions SET stage='sending',updated=? "
                      "WHERE id=? AND stage='confirm'",
                      (int(time.time()),session["id"])).rowcount
        if not changed:
            return "Отправка уже выполняется или была завершена."
        if not shutil.which("termux-sms-send"):
            _update(session,"uncertain")
            return "Инструмент отправки SMS недоступен. Ничего не отправлено."
        ok,out=_run(["termux-sms-send","-n",session["number"],session["message"]],timeout=20)
        _update(session,"done" if ok else "uncertain",message="")
        if ok:
            _set_skill("sms","learned",{"method":"Termux:API"})
            with LOCK:
                with _connect() as c:
                    c.execute("UPDATE skill_goals SET status='learned' WHERE id=?",(session["goal_id"],))
            return ("Команда отправки SMS принята Android. Доставку проверить не могу. "
                    "Навык сохранён: в следующий раз можно сразу попросить отправить SMS.")
        return ("Отправка не подтверждена: "+out+" Проверь сообщения на телефоне, "
                "прежде чем повторять, чтобы не отправить дважды.")
    if stage=="sending":
        return "Отправка уже выполняется. Повторная команда заблокирована."
    if stage in ("done","uncertain"):
        return None
    return None

def handle(text, history=None):
    """Return response text or None. Runs before the ordinary Gemini planner."""
    if not str(text).strip():
        return None
    txt=PREFIX.sub("",str(text).strip()).strip()
    normalized=norm(txt)
    session=_active()
    if session and session["stage"] not in ("done","uncertain"):
        if normalized in CANCEL:
            _delete_session(session)
            return "Действие отменено. Никакое сообщение не отправлено."
        result=_process_session(session,txt)
        if result is not None:
            return result
    # Never pass an orphaned SMS confirmation into Gemini/tool planning.
    if normalized in CONFIRM:
        return "Нет сообщения, ожидающего подтверждения отправки."
    if normalized in LEARN:
        goal=pending_goal()
        if not goal and history:
            # This also recognizes prior Gemini refusal, which did not pass
            # through our own intent matcher (for a truly new task).
            seq=list(history)[-8:]
            if len(seq)>=2:
                assistant=seq[-1] if seq[-1][0]=="assistant" else None
                previous=seq[-2] if assistant and seq[-2][0]=="user" else None
                if previous and any(x in norm(assistant[1]) for x in REFUSAL):
                    note_refusal(previous[1],assistant[1])
                    goal=pending_goal()
        return _learn(goal) if goal else (
            "Сначала попроси выполнить задачу. Если не смогу — скажи «научись»; "
            "я попробую создать и проверить новый навык.")
    intent=recognize_intent(txt)
    if intent is not None:
        exists=load_skill("sms")
        if exists and exists["status"] in ("prepared","learned"):
            goal_id=add_goal("sms",txt,intent["recipient"])
            _start(goal_id,intent["recipient"])
            return _resolve(_active())
        add_goal("sms",txt,intent["recipient"])
        return ("Сейчас не умею отправлять сообщения этому контакту. "
                "Скажи «научись» — проверю инструменты Android и попробую настроить SMS.")
    # Reuse an explicitly approved Skill Forge module on repeated requests.
    # No generated code execution: safe interpreter or trusted read-only tools.
    try:
        from skill_forge import run_intent
        result=run_intent(txt)
        if result is not None:
            return result
    except Exception:
        return "Освоенный навык временно недоступен; изменения не выполнялись."
    learned=load_skill("recipe:"+normalized[:120])
    if learned and learned["status"]=="learned":
        try:
            from extensions import run
            outcome,code=run(learned["metadata"]["extension_id"])
            return json.dumps(outcome,ensure_ascii=False)[:700] if code==200 else None
        except Exception:
            return None
    return None

def prepare_sms(install=False):
    """Menu-only setup. Package install requires explicit owner approval in TUI."""
    if install and (not shutil.which("termux-contact-list") or not shutil.which("termux-sms-send")):
        if not shutil.which("pkg"):
            return "Команда pkg не найдена: нужен обычный Termux из F-Droid."
        ok,result=_run(["pkg","install","-y","termux-api"],timeout=150)
        if not ok:
            return "Не получилось установить termux-api. Проверь интернет и репозитории Termux."
    if not shutil.which("termux-contact-list") or not shutil.which("termux-sms-send"):
        return ("Нужен пакет termux-api и Android-приложение Termux:API из F-Droid.")
    ok,out=_run(["termux-contact-list"],timeout=16)
    if not ok:
        return (out+" Разреши контакты приложению Termux:API в настройках Android "
                "и повтори проверку.")
    try:
        items=json.loads(out)
        if not isinstance(items,list):
            raise ValueError("invalid contact result")
    except (ValueError,TypeError):
        return ("Termux:API не вернул корректный список контактов. "
                "Проверь приложение и Android-разрешения.")
    _set_skill("sms","prepared",{"method":"Termux:API"})
    return ("Контакты доступны, SMS-инструмент установлен. "
            "Android попросит разрешение на отправку SMS при первом использовании. "
            "Никаких сообщений без подтверждения NEXUS не отправлял.")

def clear_pending():
    with LOCK:
        with _connect() as c:
            c.execute("DELETE FROM skill_goals WHERE status='not_learned'")
            c.execute("DELETE FROM skill_sessions")
