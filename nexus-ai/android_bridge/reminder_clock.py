"""Persistent due-time reminders. Process runs only while Termux service is alive."""
import time, datetime, threading
from agent import connect, LOCK, api

def init():
    with LOCK, connect() as db:
        db.execute('CREATE TABLE IF NOT EXISTS timed_reminders (id INTEGER PRIMARY KEY, body TEXT NOT NULL, due INTEGER NOT NULL, fired INTEGER NOT NULL DEFAULT 0, created INTEGER NOT NULL)')
        db.execute('CREATE INDEX IF NOT EXISTS timed_due_idx ON timed_reminders(fired,due)')

def add(text,when):
    text=str(text).strip()[:300]
    if not text:return 'Текст напоминания пуст.'
    # ISO 8601 with mandatory UTC offset; avoid timezone ambiguity.
    try:
        dt=datetime.datetime.fromisoformat(str(when).replace('Z','+00:00'))
        if dt.tzinfo is None: return 'Нужна дата со смещением часового пояса, например 2026-11-12T09:00:00+03:00.'
        ts=int(dt.timestamp())
    except (ValueError,OverflowError):return 'Некорректная дата ISO 8601.'
    if ts <= time.time()+5:return 'Дата напоминания уже прошла или слишком близка.'
    with LOCK, connect() as db:
        db.execute('INSERT INTO timed_reminders(body,due,created) VALUES(?,?,?)',(text,ts,int(time.time())))
    return 'Напоминание назначено на '+dt.isoformat()+'. Сработает, пока Termux работает.'

def listing():
    with LOCK,connect() as db:
        rows=db.execute('SELECT id,body,due FROM timed_reminders WHERE fired=0 ORDER BY due LIMIT 12').fetchall()
    return '\n'.join(f'{i}: {datetime.datetime.fromtimestamp(ts).astimezone().isoformat(timespec="minutes")} — {body}' for i,body,ts in rows) or 'Запланированных напоминаний нет.'

def cancel(item_id):
    try:item_id=int(item_id)
    except (ValueError,TypeError):return 'Нужен числовой ID.'
    with LOCK,connect() as db:
        c=db.execute('DELETE FROM timed_reminders WHERE id=? AND fired=0',(item_id,))
        return 'Напоминание отменено.' if c.rowcount else 'Не найдено.'

def tick():
    with LOCK,connect() as db:
        rows=db.execute('SELECT id,body FROM timed_reminders WHERE fired=0 AND due<=? ORDER BY due LIMIT 8',(int(time.time()),)).fetchall()
        # Claim due records before notification to avoid duplicate delivery on restart.
        for i,_ in rows:db.execute('UPDATE timed_reminders SET fired=1 WHERE id=? AND fired=0',(i,))
    for i,body in rows:
        result=api('termux-notification','--title','NEXUS AI · Напоминание','--content',body,'--id',str(20000+i),timeout=7)
        if result.startswith('Termux:API недоступен') or result.startswith('Ошибка'):
            # Notification may be unavailable; keep it pending for retry.
            with LOCK,connect() as db:db.execute('UPDATE timed_reminders SET fired=0 WHERE id=?',(i,))

def worker(stop):
    while not stop.wait(12):
        try:tick()
        except Exception as exc:print('Reminder worker:',type(exc).__name__,str(exc)[:150],flush=True)
init()
