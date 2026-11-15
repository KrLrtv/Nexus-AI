"""NEXUS Skill Forge: AI drafts a *bounded* skill, runs safe dry-run tests,
and asks the owner to approve it through the Termux menu.

Never eval/exec/import/compile generated Python or C++ source. "pure" modules
are run ONLY by a small, trusted expression interpreter. "recipe" modules
can call ONLY existing, read-only Android bridge functions. Arbitrary source
and firmware patch proposals are stored as review-only TEXT.
"""
from __future__ import annotations

import ast
from contextlib import contextmanager
import json
import math
import os
from pathlib import Path
import re
import sqlite3
import threading
import time

from ai_provider import post_json, ProviderError

ROOT = Path(os.environ.get("NEXUS_FORGE_DB", str(Path.home()/".nexus_ai"/"skill_forge.sqlite3")))
LOCK = threading.RLock()
MAX_DRAFTS = 30
MAX_SOURCE = 3500
MAX_RESULT = 1400
MAX_AST_NODES = 70
ALLOWED_TOOLS = frozenset({"battery","notes_list","reminders_list",
                           "memory_search","reminder_timed_list"})
TOKEN_PATTERN = re.compile(
    r"(?i)(AIza[A-Za-z0-9_-]{15,}|sk-[a-z0-9_-]{15,}|"
    r"(?<![a-z])password\s*[:=]|(?<![a-z])api[_ -]?key\s*[:=]|"
    r"\bпароль\b|\bтокен\b)"
)
VALID_NAME = re.compile(r"[a-z][a-z0-9_]{2,35}")
KINDS = {"pure","recipe","proposal"}

def _norm(text: str) -> str:
    return " ".join(re.findall(r"[а-яa-z0-9]+",str(text).casefold().replace("ё","е")))[:200]

@contextmanager
def _db():
    ROOT.parent.mkdir(parents=True,exist_ok=True,mode=0o700)
    if ROOT.is_symlink():
        raise RuntimeError("Небезопасная ссылка на базу Skill Forge.")
    if not ROOT.exists():
        try:
            fd=os.open(ROOT,os.O_CREAT|os.O_EXCL|os.O_WRONLY,0o600)
            os.close(fd)
        except FileExistsError:
            if ROOT.is_symlink():
                raise RuntimeError("Небезопасная ссылка на базу Skill Forge.")
    os.chmod(ROOT,0o600)
    connection=sqlite3.connect(ROOT,timeout=10)
    try:
        connection.execute("PRAGMA busy_timeout=10000")
        connection.execute("""CREATE TABLE IF NOT EXISTS drafts(
            id INTEGER PRIMARY KEY, intent TEXT NOT NULL, intent_norm TEXT NOT NULL,
            name TEXT NOT NULL, kind TEXT NOT NULL, summary TEXT NOT NULL,
            payload TEXT NOT NULL, state TEXT NOT NULL, report TEXT NOT NULL,
            created INTEGER NOT NULL, activated INTEGER NOT NULL DEFAULT 0
        )""")
        yield connection
        connection.commit()
    except BaseException:
        connection.rollback()
        raise
    finally:
        connection.close()

def _primitive(value):
    if type(value) is str:
        if len(value)>600:
            raise ValueError("Строка превышает безопасный лимит.")
        return value
    if type(value) in (int,float,bool) or value is None:
        if type(value) is int and abs(value)>1_000_000_000:
            raise ValueError("Целое число превышает безопасный лимит.")
        if type(value) is float and (not math.isfinite(value) or abs(value)>1e9):
            raise ValueError("Число превышает безопасный лимит.")
        return value
    raise ValueError("Поддерживаются только строки, числа и логические значения.")

def _check_value(value):
    value=_primitive(value)
    if type(value) is str and len(value)>MAX_RESULT:
        raise ValueError("Результат превышает лимит.")
    return value

def _expression_tree(code: str):
    """Require exactly: def solve(value): return <single bounded expression>."""
    if not isinstance(code,str) or not code or len(code)>MAX_SOURCE:
        raise ValueError("Слишком большой или пустой исходный код.")
    tree=ast.parse(code)
    if len(tree.body)!=1 or not isinstance(tree.body[0],ast.FunctionDef):
        raise ValueError("Требуется ровно одна чистая функция solve(value).")
    f=tree.body[0]
    if (f.name!="solve" or f.decorator_list or f.returns is not None or
        len(f.args.args)!=1 or f.args.args[0].arg!="value" or
        f.args.args[0].annotation is not None or
        f.args.posonlyargs or f.args.kwonlyargs or f.args.kwarg is not None or
        f.args.vararg is not None or f.args.defaults or f.args.kw_defaults or
        len(f.body)!=1 or not isinstance(f.body[0],ast.Return) or
        not isinstance(f.body[0].value,ast.expr)):
        raise ValueError("Недопустимая сигнатура или операторы функции.")
    expr=f.body[0].value
    nodes=list(ast.walk(expr))
    if len(nodes)>MAX_AST_NODES:
        raise ValueError("Слишком сложное выражение.")
    return expr

def _pure_eval(expr, value):
    """Trusted, non-Python interpreter; model code is AST data, never executed."""
    def calc(node,depth=0):
        if depth>12:
            raise ValueError("Слишком глубокое выражение.")
        if isinstance(node,ast.Constant):
            return _check_value(node.value)
        if isinstance(node,ast.Name) and node.id=="value":
            return _check_value(value)
        if isinstance(node,ast.UnaryOp) and type(node.op) in (ast.UAdd,ast.USub,ast.Not):
            a=calc(node.operand,depth+1)
            if type(node.op) is ast.Not:return not a
            if type(a) not in (int,float):raise ValueError("Ожидается число.")
            return _check_value(-a if type(node.op) is ast.USub else +a)
        if isinstance(node,ast.BinOp) and type(node.op) in (
                ast.Add,ast.Sub,ast.Mult,ast.Div,ast.FloorDiv,ast.Mod):
            a,b=calc(node.left,depth+1),calc(node.right,depth+1)
            if type(node.op) is ast.Add:
                if type(a) is str and type(b) is str: return _check_value(a+b)
                if type(a) in (int,float) and type(b) in (int,float):
                    return _check_value(a+b)
                raise ValueError("Недопустимые типы сложения.")
            if type(a) not in (int,float) or type(b) not in (int,float):
                raise ValueError("Ожидаются два числа.")
            if type(node.op) is ast.Sub:return _check_value(a-b)
            if type(node.op) is ast.Mult:return _check_value(a*b)
            if type(node.op) is ast.Div:return _check_value(a/b)
            if type(node.op) is ast.FloorDiv:return _check_value(a//b)
            return _check_value(a%b)
        if isinstance(node,ast.IfExp):
            return _check_value(calc(node.body if calc(node.test,depth+1) else node.orelse,depth+1))
        if isinstance(node,ast.Compare) and len(node.ops)==1 and len(node.comparators)==1:
            left=calc(node.left,depth+1)
            right=calc(node.comparators[0],depth+1)
            op=node.ops[0]
            if type(op) is ast.Eq:return left==right
            if type(op) is ast.NotEq:return left!=right
            if type(left)!=type(right) or type(left) not in (int,float,str):
                raise ValueError("Недопустимое сравнение.")
            if type(op) is ast.Lt:return left<right
            if type(op) is ast.LtE:return left<=right
            if type(op) is ast.Gt:return left>right
            if type(op) is ast.GtE:return left>=right
            raise ValueError("Сравнение не разрешено.")
        if isinstance(node,ast.Call) and not node.keywords:
            args=[calc(x,depth+1) for x in node.args]
            if isinstance(node.func,ast.Name):
                fn=node.func.id
                if fn=="len" and len(args)==1 and type(args[0]) is str:
                    return len(args[0])
                if fn=="abs" and len(args)==1 and type(args[0]) in (int,float):
                    return _check_value(abs(args[0]))
                if fn=="str" and len(args)==1:
                    return _check_value(str(args[0]))
                if fn=="round" and len(args) in (1,2) and type(args[0]) in (int,float) \
                        and (len(args)==1 or (type(args[1]) is int and 0<=args[1]<=5)):
                    return _check_value(round(*args))
                raise ValueError("Запрещённый вызов функции.")
            if isinstance(node.func,ast.Attribute) and \
               isinstance(node.func.value,ast.Name) and node.func.value.id=="value":
                if type(value) is not str:
                    raise ValueError("Метод применяется только к тексту.")
                method=node.func.attr
                if method in ("upper","lower","strip","title","capitalize") and not args:
                    return _check_value(getattr(value,method)())
                if method in ("startswith","endswith") and len(args)==1 and type(args[0]) is str:
                    return getattr(value,method)(args[0])
                if method=="replace" and len(args)==2 and all(type(x) is str for x in args):
                    return _check_value(value.replace(args[0],args[1]))
            raise ValueError("Метод запрещён.")
        if isinstance(node,ast.Subscript) and isinstance(node.value,ast.Name) and \
           node.value.id=="value" and type(value) is str and isinstance(node.slice,ast.Slice):
            piece=node.slice
            def integer(x):
                if x is None:return None
                if isinstance(x,ast.Constant) and type(x.value) is int and -600<=x.value<=600:
                    return x.value
                if isinstance(x,ast.UnaryOp) and isinstance(x.op,ast.USub) and \
                   isinstance(x.operand,ast.Constant) and type(x.operand.value) is int and 0<=x.operand.value<=600:
                    return -x.operand.value
                raise ValueError("Небезопасный индекс.")
            step=integer(piece.step)
            if step is not None and step not in (-1,1):
                raise ValueError("Недопустимый шаг среза.")
            return _check_value(value[slice(integer(piece.lower),integer(piece.upper),step)])
        raise ValueError("Выражение содержит недопустимую операцию.")
    return _check_value(calc(expr))

def pure_run(code: str,value):
    try:
        return _pure_eval(_expression_tree(code),_primitive(value))
    except ArithmeticError as exc:
        raise ValueError("Недопустимая арифметическая операция.") from exc

def validate(payload):
    if not isinstance(payload,dict):
        raise ValueError("Модуль должен быть JSON-объектом.")
    kind=payload.get("kind")
    if kind not in KINDS:
        raise ValueError("Неизвестный тип модуля.")
    name=payload.get("name")
    if not isinstance(name,str) or not VALID_NAME.fullmatch(name):
        raise ValueError("Неверное имя модуля.")
    summary=payload.get("summary")
    if not isinstance(summary,str) or not summary.strip() or len(summary)>220:
        raise ValueError("Недопустимое описание модуля.")
    code=payload.get("source","")
    steps=payload.get("steps",[])
    tests=payload.get("tests",[])
    if not isinstance(code,str) or len(code)>MAX_SOURCE or not isinstance(steps,list) or not isinstance(tests,list):
        raise ValueError("Недопустимые поля исходников или тестов.")
    if TOKEN_PATTERN.search(code):
        raise ValueError("В исходниках обнаружена строка, похожая на секрет.")
    report={"kind":kind,"errors":[],"checks":[],"tested":False,"deployable":False}
    if kind=="pure":
        expr=_expression_tree(code)
        if steps:
            raise ValueError("Чистая функция не может иметь системных шагов.")
        if not (2<=len(tests)<=5):
            raise ValueError("Для чистого модуля нужны 2–5 тестов.")
        for i,item in enumerate(tests,1):
            if not isinstance(item,dict) or set(item)!={"input","expected"}:
                raise ValueError("Тест должен иметь input и expected.")
            test_input=_primitive(item["input"])
            expected=_primitive(item["expected"])
            try:
                actual=_pure_eval(expr,test_input)
                passed=type(actual) is type(expected) and actual==expected
            except (ValueError,ArithmeticError,OverflowError,TypeError) as exc:
                passed=False
            report["checks"].append({"test":i,"passed":passed})
            if not passed:
                report["errors"].append("Тест "+str(i)+" не прошёл.")
        report["tested"]=True
        report["deployable"]=not report["errors"]
    elif kind=="recipe":
        if code:
            raise ValueError("Системный сценарий не должен включать Python-код.")
        if not 1<=len(steps)<=4:
            raise ValueError("Сценарий должен иметь 1–4 шага.")
        normalized=[]
        for step in steps:
            if not isinstance(step,dict) or set(step)!={"tool","argument"}:
                raise ValueError("Недопустимая структура системного шага.")
            tool,arg=step.get("tool"),step.get("argument")
            if tool not in ALLOWED_TOOLS or not isinstance(arg,str) or len(arg)>120:
                raise ValueError("Новый инструмент или изменение телефона запрещены.")
            normalized.append({"tool":tool,"argument":arg})
        if tests:
            raise ValueError("Тесты сценария создаются доверенным валидатором.")
        # Dry-run tests: no Android operations, no contact reads, no network.
        simulated=[{"tool":x["tool"],"result":"SIMULATED"} for x in normalized]
        report["checks"]=[{"test":"read_only","passed":True},
                           {"test":"dry_run","passed":len(simulated)==len(steps)}]
        report["tested"]=True
        report["deployable"]=True
    elif kind=="proposal":
        if steps or tests or not code:
            raise ValueError("Черновик исходников не должен исполняться как навык.")
        language=payload.get("language","")
        if language not in ("python","cpp"):
            raise ValueError("Черновик может содержать только Python или C++.")
        if language=="python":
            try:
                ast.parse(code)
                report["checks"].append({"test":"syntax_only","passed":True})
            except SyntaxError:
                report["errors"].append("Ошибка синтаксиса Python.")
        else:
            report["checks"].append({"test":"cpp_not_compiled","passed":False})
        report["errors"].append("Исходники не выполнялись. Требуется ревью и изолированное тестирование.")
        report["tested"]=False
        report["deployable"]=False
    return report

def _get(identifier):
    with LOCK,_db() as c:
        row=c.execute("""SELECT id,intent,name,kind,summary,payload,state,report,created
                         FROM drafts WHERE id=?""",(int(identifier),)).fetchone()
    if not row:return None
    names=("id","intent","name","kind","summary","payload","state","report","created")
    item=dict(zip(names,row))
    item["payload"]=json.loads(item["payload"])
    item["report"]=json.loads(item["report"])
    return item

def list_drafts(limit=12):
    limit=max(1,min(int(limit),30))
    with LOCK,_db() as c:
        rows=c.execute("SELECT id,intent,name,kind,state,created FROM drafts "
                       "ORDER BY id DESC LIMIT ?",(limit,)).fetchall()
    return [dict(zip(("id","intent","name","kind","state","created"),x)) for x in rows]

def store(intent,payload):
    intent=str(intent).strip()[:300]
    if not intent or TOKEN_PATTERN.search(intent):
        raise ValueError("Невозможно создать навык по пустому или секретному запросу.")
    report=validate(payload)
    state="tested" if report["deployable"] else "review"
    with LOCK,_db() as c:
        cur=c.execute("""INSERT INTO drafts(intent,intent_norm,name,kind,summary,payload,state,report,created)
            VALUES(?,?,?,?,?,?,?,?,?)""",
            (intent,_norm(intent),payload["name"],payload["kind"],payload["summary"],
             json.dumps(payload,ensure_ascii=False),state,json.dumps(report,ensure_ascii=False),int(time.time())))
        ident=cur.lastrowid
        # Keep owner-approved skills across future draft creation.
        # Only unapproved/rejected old drafts are pruned.
        c.execute("DELETE FROM drafts WHERE state!='active' AND id NOT IN "
                  "(SELECT id FROM drafts ORDER BY id DESC LIMIT ?)",(MAX_DRAFTS,))
    return _get(ident)

def retest(identifier):
    draft=_get(identifier)
    if not draft:return None
    report=validate(draft["payload"])
    state="tested" if report["deployable"] else "review"
    with LOCK,_db() as c:
        c.execute("UPDATE drafts SET state=?,report=? WHERE id=? AND state!='active'",
                  (state,json.dumps(report,ensure_ascii=False),draft["id"]))
    return _get(identifier)

def approve(identifier):
    """Owner-only menu action; no auto approval of model output."""
    draft=_get(identifier)
    if not draft or draft["state"] not in ("tested","active"):
        raise ValueError("Черновик не прошёл проверки или уже отклонён.")
    report=validate(draft["payload"])
    if not report["deployable"]:
        raise ValueError("Этот исходный код нельзя активировать без проверки разработчиком.")
    with LOCK,_db() as c:
        c.execute("UPDATE drafts SET state='active',activated=? WHERE id=?",
                  (int(time.time()),draft["id"]))
    return _get(identifier)

def reject(identifier):
    with LOCK,_db() as c:
        row=c.execute("UPDATE drafts SET state='rejected' WHERE id=? AND state!='rejected'",
                      (int(identifier),))
        return row.rowcount!=0

def run_intent(intent):
    """Return a reply only for a precisely matching activated skill."""
    key=_norm(intent)
    with LOCK,_db() as c:
        row=c.execute("SELECT id FROM drafts WHERE state='active' AND intent_norm=? "
                      "ORDER BY id DESC LIMIT 1",(key,)).fetchone()
    if not row:return None
    draft=_get(row[0])
    payload=draft["payload"]
    # Verify stored code at EVERY invocation; SQLite is not a security boundary.
    report=validate(payload)
    if not report["deployable"]:
        return "Навык не прошёл повторную проверку; запуск заблокирован."
    if payload["kind"]=="pure":
        try:
            value=pure_run(payload["source"],intent[:600])
            return "Освоенный навык: "+str(value)[:1100]
        except (ValueError,OverflowError,TypeError,ArithmeticError):
            return "Параметры нового навыка не подходят для этого запроса."
    if payload["kind"]=="recipe":
        from agent import execute
        output=[]
        for step in payload["steps"]:
            output.append(str(execute(step["tool"],step["argument"]))[:350])
        return "Освоенный сценарий: "+"; ".join(output)[:1200]
    return None

def propose(intent):
    """Generate a proposal. Gemini does not gain access to local executables."""
    intent=str(intent).strip()[:300]
    if not intent or TOKEN_PATTERN.search(intent):
        raise ValueError("Не подходит для проектирования нового навыка.")
    key=os.environ.get("GEMINI_API_KEY","").strip()
    if not key:
        raise ProviderError("Для разработки нового навыка необходим настроенный Gemini.")
    model=os.environ.get("GEMINI_MODEL","gemini-2.5-flash-lite")
    if not re.fullmatch(r"[A-Za-z0-9._-]{3,100}",model):
        raise ValueError("Некорректная модель Gemini.")
    directive=(
        "Ты проектировщик модулей NEXUS для Android Termux и M5StickC Plus2. "
        "Верни ТОЛЬКО JSON, никаких markdown или внешних ссылок. "
        "Поля обязательны: kind (pure|recipe|proposal), name (snake_case), summary (русский до 220), "
        "source (строка), steps (массив), tests (массив). "
        "Тип recipe: source='', tests=[], steps 1–4 из инструментов "
        "battery, notes_list, reminders_list, memory_search, reminder_timed_list, "
        "каждый шаг содержит tool, argument. Только чтение, никакой отправки. "
        "Тип pure: source = код 'def solve(value):\\n    return ...', где выражение только арифметика, "
        "строковые методы upper/lower/strip/title/replace, срезы и len; "
        "steps=[], tests = 2–5 примеров {input,expected}. "
        "Тип proposal: для функций Android/M5, которых нет среди перечисленных, "
        "создай review-only Python/C++ исходник в source (не более 3500 символов), "
        "language='python'|'cpp', steps=[], tests=[]; такой код НЕ будет выполняться. "
        "Если не можешь безопасно реализовать задачу, используй тип proposal "
        "с понятным примером или TODO, но не заявляй об установке/готовом результате. "
        "Не включай пароли, приватные токены, команды прошивки, обход разрешений, "
        "скачивание и запуск чужого кода. Запрос: "+intent
    )
    obj=post_json(
        "https://generativelanguage.googleapis.com/v1beta/models/"+model+":generateContent",
        {"contents":[{"role":"user","parts":[{"text":directive}]}],
         "generationConfig":{"responseMimeType":"application/json","maxOutputTokens":1700}},
        {"x-goog-api-key":key},timeout=30)
    try:
        raw="".join(part.get("text","") for part in
                    obj["candidates"][0]["content"]["parts"] if isinstance(part,dict))
        if len(raw)>14000:
            raise ValueError("Слишком длинный ответ модели.")
        payload=json.loads(raw)
    except (KeyError,IndexError,TypeError,ValueError) as exc:
        raise ValueError("Gemini не подготовил корректную схему навыка.") from exc
    return store(intent,payload)
