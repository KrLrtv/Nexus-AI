"""Constrained local tools. No AI-generated shell commands are executed."""
import os, re, json, sqlite3, subprocess, shutil, time, threading
from pathlib import Path
DB=Path(os.environ.get('NEXUS_DB_PATH',str(Path.home()/'.nexus_ai'/'agent.sqlite3')))
DB.parent.mkdir(parents=True,exist_ok=True)
LOCK=threading.RLock()
def connect():
    db=sqlite3.connect(DB,timeout=10)
    db.execute('CREATE TABLE IF NOT EXISTS notes (id INTEGER PRIMARY KEY, body TEXT NOT NULL, created INTEGER NOT NULL)')
    db.execute('CREATE TABLE IF NOT EXISTS reminders (id INTEGER PRIMARY KEY, body TEXT NOT NULL, created INTEGER NOT NULL)')
    db.execute('CREATE TABLE IF NOT EXISTS dialog (id INTEGER PRIMARY KEY, role TEXT NOT NULL, body TEXT NOT NULL, created INTEGER NOT NULL)')
    return db
with connect() as db:pass

def save_dialog(role,body):
    with LOCK,connect() as db:
        db.execute('INSERT INTO dialog(role,body,created) VALUES(?,?,?)',(role,body[:2500],int(time.time())))
        db.execute('DELETE FROM dialog WHERE id NOT IN (SELECT id FROM dialog ORDER BY id DESC LIMIT 80)')
def recent_dialog(limit=8):
    with LOCK,connect() as db:
        records=db.execute('SELECT role,body FROM dialog ORDER BY id DESC LIMIT ?', (limit,)).fetchall()
    return list(reversed(records))
def api(command,*args,timeout=8):
    if not shutil.which(command):return 'Termux:API недоступен: '+command
    try:
        p=subprocess.run([command,*args],capture_output=True,text=True,timeout=timeout,check=False)
        if p.returncode:return ('Ошибка '+command+': '+p.stderr.strip())[:250]
        return (p.stdout or 'Выполнено').strip()[:1200]
    except (subprocess.TimeoutExpired,OSError) as e:return 'Ошибка: '+str(e)[:180]
def execute(action,argument=''):
    argument=str(argument)[:500]
    if action in ('graph_add','graph_search','goal_add','goal_list','task_add','task_done','research_scan','notification_digest','evolution_draft'):
        try:
            from core10 import node,graph_search,add_goal,add_task,goals,task_done
            if action=='graph_add':
                data=json.loads(argument);return 'Node ID '+str(node(data['kind'],data['label'],data.get('detail','')))
            if action=='graph_search':return json.dumps(graph_search(argument),ensure_ascii=False)[:2000]
            if action=='goal_add':return 'Goal ID '+str(add_goal(argument))
            if action=='goal_list':return json.dumps(goals(),ensure_ascii=False)[:2500]
            if action=='task_add':
                data=json.loads(argument);return 'Task ID '+str(add_task(data['goal_id'],data['title']))
            if action=='task_done':return 'Updated' if task_done(int(argument)) else 'No pending task with this ID'
            if action=='research_scan':
                from research10 import scan
                return json.dumps(scan(argument.strip() or 'technology'),ensure_ascii=False)[:2400]
            if action=='notification_digest':
                from notifications10 import digest
                return json.dumps(digest(),ensure_ascii=False)[:1500]
            if action=='evolution_draft':
                from evolution10 import generate
                return json.dumps(generate(argument),ensure_ascii=False)[:900]
        except Exception as e:return 'Tool failed: '+str(e)[:200]

    if action=='battery':
        out=api('termux-battery-status')
        try:
            b=json.loads(out);return f"Батарея телефона: {b.get('percentage','?')}%, {b.get('status','?')}."
        except ValueError:return out
    if action=='notes_list':
        with LOCK,connect() as db:rows=db.execute('SELECT id,body FROM notes ORDER BY id DESC LIMIT 10').fetchall()
        return '\n'.join(f'{i}. {v}' for i,v in rows) if rows else 'Заметок пока нет.'
    if action=='note_add':
        if not argument.strip():return 'Текст заметки пуст.'
        with LOCK,connect() as db:db.execute('INSERT INTO notes(body,created) VALUES(?,?)',(argument,int(time.time())))
        return 'Заметка сохранена локально.'
    if action=='reminder_add':
        if not argument.strip():return 'Текст напоминания пуст.'
        with LOCK,connect() as db:db.execute('INSERT INTO reminders(body,created) VALUES(?,?)',(argument,int(time.time())))
        return 'Записал в список напоминаний. Автоматическое срабатывание по времени ещё не реализовано.'
    if action=='reminders_list':
        with LOCK,connect() as db:rows=db.execute('SELECT id,body FROM reminders ORDER BY id DESC LIMIT 10').fetchall()
        return '\n'.join(f'{i}. {v}' for i,v in rows) if rows else 'Напоминаний пока нет.'
    if action=='memory_save':
        from memory import remember
        return remember(argument)
    if action=='memory_search':
        from memory import recall
        return recall(argument)
    if action=='memory_delete':
        from memory import erase
        return erase(argument)
    if action=='reminder_timed_list':
        from reminder_clock import listing
        return listing()
    if action=='reminder_timed_add':
        from reminder_clock import add
        try:
            data=json.loads(argument)
            return add(data.get('text',''),data.get('when',''))
        except (ValueError,TypeError):return 'Нужен JSON с text и when (ISO 8601 и часовой пояс).'
    if action=='reminder_timed_cancel':
        from reminder_clock import cancel
        return cancel(argument)
    if action=='notify':return api('termux-notification','--title','NEXUS AI','--content',argument)
    if action=='torch_on':return api('termux-torch','on')
    if action=='torch_off':return api('termux-torch','off')
    if action=='speak':return api('termux-tts-speak',argument)
    return 'Неизвестное действие.'
# Recognize a limited set of commands. This is deterministic, NOT model tool-calling.
def recognize(text):
    s=text.lower().strip()
    for regex,action in [
        (r'^(?:сохрани|запиши|добавь) (?:заметку|заметку:|идею|идею:)\s*(.*)', 'note_add'),
        (r'^(?:напомни|создай напоминание)\s*(.*)','reminder_add'),
        (r'^(?:озвучь|скажи вслух)\s*(.*)','speak'),
        (r'^(?:покажи уведомление|уведомление)\s*(.*)','notify'),
    ]:
        m=re.match(regex,s,re.I|re.S)
        if m and m.group(1).strip():return action,text[m.start(1):].strip()
    if 'заряд' in s and ('телефон' in s or 'батаре' in s):return 'battery',''
    if s in ('мои заметки','покажи заметки','список заметок'):return 'notes_list',''
    if s in ('мои напоминания','покажи напоминания','список напоминаний'):return 'reminders_list',''
    if s in ('включи фонарик','включить фонарик'):return 'torch_on',''
    if s in ('выключи фонарик','выключить фонарик'):return 'torch_off',''
    return None
REQUIRES_APPROVAL = set()  # NEXUS_R893_DIRECT_COMMANDS
