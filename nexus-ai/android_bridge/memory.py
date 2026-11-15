"""Explicitly saved local memories. Not a vector database; basic keyword retrieval."""
import time, sqlite3
from agent import connect, LOCK

def init():
    with LOCK, connect() as db:
        db.execute('CREATE TABLE IF NOT EXISTS memory (id INTEGER PRIMARY KEY, text TEXT NOT NULL, created INTEGER NOT NULL)')

def remember(value):
    value=str(value).strip()[:800]
    if not value: return 'Нечего сохранять.'
    with LOCK, connect() as db:
        db.execute('INSERT INTO memory(text,created) VALUES (?,?)',(value,int(time.time())))
    return 'Сохранил в долговременную память.'

def recall(query=''):
    with LOCK, connect() as db:
        rows=db.execute('SELECT id,text FROM memory ORDER BY id DESC LIMIT 100').fetchall()
    tokens=[w for w in str(query).lower().split() if len(w)>2]
    relevant=[(i,t) for i,t in rows if not tokens or any(w in t.lower() for w in tokens)]
    return '\n'.join(f'{i}: {t}' for i,t in relevant[:8]) or 'Подходящих воспоминаний нет.'

def erase(item_id):
    try: item_id=int(item_id)
    except (TypeError,ValueError):return 'Нужен числовой ID.'
    with LOCK,connect() as db:
        cur=db.execute('DELETE FROM memory WHERE id=?',(item_id,))
        return 'Запись удалена.' if cur.rowcount else 'Запись не найдена.'
init()
