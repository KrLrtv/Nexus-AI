"""NEXUS 1.0 durable storage: all inputs bounded; no arbitrary SQL from model."""
import os,sqlite3,time,json,threading
from pathlib import Path
DB=Path(os.getenv('NEXUS_DB_PATH',str(Path.home()/'.nexus_ai'/'agent.sqlite3')))
DB.parent.mkdir(parents=True,exist_ok=True)
LOCK=threading.RLock()
def connect():
    c=sqlite3.connect(DB,timeout=10)
    c.execute('PRAGMA busy_timeout=10000')
    c.execute('CREATE TABLE IF NOT EXISTS graph_nodes(id INTEGER PRIMARY KEY, kind TEXT NOT NULL, label TEXT NOT NULL, detail TEXT NOT NULL DEFAULT "", created INTEGER NOT NULL)')
    c.execute('CREATE TABLE IF NOT EXISTS graph_edges(src INTEGER NOT NULL,dst INTEGER NOT NULL,relation TEXT NOT NULL,PRIMARY KEY(src,dst,relation))')
    c.execute('CREATE TABLE IF NOT EXISTS goals(id INTEGER PRIMARY KEY,title TEXT NOT NULL,status TEXT NOT NULL DEFAULT "active",created INTEGER NOT NULL,updated INTEGER NOT NULL)')
    c.execute('CREATE TABLE IF NOT EXISTS tasks(id INTEGER PRIMARY KEY,goal_id INTEGER NOT NULL,title TEXT NOT NULL,status TEXT NOT NULL DEFAULT "todo",created INTEGER NOT NULL)')
    c.execute('CREATE TABLE IF NOT EXISTS runs(id INTEGER PRIMARY KEY,kind TEXT NOT NULL,status TEXT NOT NULL,message TEXT NOT NULL,ts INTEGER NOT NULL)')
    c.execute('CREATE TABLE IF NOT EXISTS grants(action TEXT PRIMARY KEY,allowed INTEGER NOT NULL,updated INTEGER NOT NULL)')
    c.execute('CREATE TABLE IF NOT EXISTS feed_seen(key TEXT PRIMARY KEY,seen INTEGER NOT NULL)')
    c.execute('CREATE TABLE IF NOT EXISTS device_events(id INTEGER PRIMARY KEY,topic TEXT NOT NULL,payload TEXT NOT NULL,created INTEGER NOT NULL,delivered INTEGER NOT NULL DEFAULT 0)')
    return c
def node(kind,label,detail=''):
    if kind not in ('person','project','device','fact','topic'):raise ValueError('invalid kind')
    label=str(label).strip()[:120]; detail=str(detail)[:1000]
    if not label:raise ValueError('empty label')
    with LOCK,connect() as c:
        cur=c.execute('INSERT INTO graph_nodes(kind,label,detail,created) VALUES(?,?,?,?)',(kind,label,detail,int(time.time())))
        return cur.lastrowid
def edge(src,dst,relation):
    src,dst=int(src),int(dst);relation=str(relation).strip()[:50]
    if not relation:raise ValueError('empty relation')
    with LOCK,connect() as c:
        if c.execute('SELECT count(*) FROM graph_nodes WHERE id IN (?,?)',(src,dst)).fetchone()[0]!=2 and src!=dst:raise ValueError('nodes missing')
        if src==dst and not c.execute('SELECT 1 FROM graph_nodes WHERE id=?',(src,)).fetchone():raise ValueError('node missing')
        c.execute('INSERT OR IGNORE INTO graph_edges VALUES(?,?,?)',(src,dst,relation))
def graph_search(query):
    query=str(query).strip()[:100]
    with LOCK,connect() as c:
        rows=c.execute('SELECT id,kind,label,detail FROM graph_nodes WHERE label LIKE ? OR detail LIKE ? ORDER BY id DESC LIMIT 15',(f'%{query}%',f'%{query}%')).fetchall()
        result=[]
        for i,k,l,d in rows:
            links=c.execute('SELECT dst,relation FROM graph_edges WHERE src=? LIMIT 8',(i,)).fetchall()
            result.append(dict(id=i,kind=k,label=l,detail=d,links=[{'to':v,'relation':r} for v,r in links]))
        return result
def add_goal(title):
    title=str(title).strip()[:180]
    if not title:raise ValueError('empty goal')
    now=int(time.time())
    with LOCK,connect() as c:return c.execute('INSERT INTO goals(title,created,updated) VALUES(?,?,?)',(title,now,now)).lastrowid
def add_task(goal_id,title):
    title=str(title).strip()[:180]
    with LOCK,connect() as c:
        if not c.execute('SELECT 1 FROM goals WHERE id=?',(int(goal_id),)).fetchone():raise ValueError('goal missing')
        if not title:raise ValueError('empty task')
        return c.execute('INSERT INTO tasks(goal_id,title,created) VALUES(?,?,?)',(int(goal_id),title,int(time.time()))).lastrowid
def goals():
    with LOCK,connect() as c:
        result=[]
        for i,t,s in c.execute('SELECT id,title,status FROM goals ORDER BY updated DESC LIMIT 30').fetchall():
            steps=[dict(id=a,title=b,status=d) for a,b,d in c.execute('SELECT id,title,status FROM tasks WHERE goal_id=? ORDER BY id',(i,)).fetchall()]
            result.append(dict(id=i,title=t,status=s,tasks=steps))
        return result
def task_done(task_id):
    with LOCK,connect() as c:
        cur=c.execute("UPDATE tasks SET status='done' WHERE id=? AND status='todo'",(int(task_id),))
        return bool(cur.rowcount)
def log(kind,status,message):
    with LOCK,connect() as c:c.execute('INSERT INTO runs(kind,status,message,ts) VALUES(?,?,?,?)',(str(kind)[:40],str(status)[:40],str(message)[:350],int(time.time())))
def logs():
    with LOCK,connect() as c:return [dict(kind=k,status=s,message=m,ts=t) for k,s,m,t in c.execute('SELECT kind,status,message,ts FROM runs ORDER BY id DESC LIMIT 40')]
def permission(action):
    with LOCK,connect() as c:
        row=c.execute('SELECT allowed FROM grants WHERE action=?',(action,)).fetchone()
        return bool(row and row[0])
def set_permission(action,allow):
    if action not in ('torch_on','torch_off','notify','speak','gesture_automation'):raise ValueError('non-grantable action')
    if not isinstance(allow,bool):raise ValueError('allow must be boolean')
    with LOCK,connect() as c:c.execute('INSERT OR REPLACE INTO grants VALUES(?,?,?)',(action,int(allow),int(time.time())))
def enqueue(topic,payload):
    with LOCK,connect() as c:c.execute('INSERT INTO device_events(topic,payload,created) VALUES(?,?,?)',(topic,json.dumps(payload,ensure_ascii=False)[:1000],int(time.time())))
def get_events():
    with LOCK,connect() as c:
        items=c.execute('SELECT id,topic,payload FROM device_events WHERE delivered=0 ORDER BY id LIMIT 8').fetchall()
        return [dict(id=i,topic=t,payload=json.loads(p)) for i,t,p in items]
def ack_events(ids):
    ids=[int(x) for x in ids][:8]
    with LOCK,connect() as c:
        for i in ids:c.execute('UPDATE device_events SET delivered=1 WHERE id=?',(i,))
