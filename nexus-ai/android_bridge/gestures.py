"""Small user-trainable gesture matcher, using 6 motion features; not full 3D tracking."""
import json, math, sqlite3, time
from agent import DB, LOCK, execute

SAFE_GESTURE_ACTIONS=('battery','notes_list','reminders_list')
with sqlite3.connect(DB) as db:
    db.execute('CREATE TABLE IF NOT EXISTS gesture_samples (id INTEGER PRIMARY KEY, label TEXT NOT NULL, features TEXT NOT NULL, created INTEGER NOT NULL)')
    db.execute('CREATE TABLE IF NOT EXISTS gesture_actions (label TEXT PRIMARY KEY, action TEXT NOT NULL)')

def vector(v):
    if not isinstance(v,list) or len(v)!=6:return None
    try:f=[float(x) for x in v]
    except (ValueError,TypeError):return None
    return f if all(math.isfinite(x) and abs(x)<100 for x in f) else None

def train(label,features):
    f=vector(features)
    if not f or label not in ('circle','left','right','up','down','custom'):return False
    with LOCK,sqlite3.connect(DB) as db:
        db.execute('INSERT INTO gesture_samples(label,features,created) VALUES(?,?,?)',(label,json.dumps(f),int(time.time())))
        db.execute('DELETE FROM gesture_samples WHERE id NOT IN (SELECT id FROM gesture_samples ORDER BY id DESC LIMIT 90)')
    return True

def configure(label,action):
    if label not in ('circle','left','right','up','down','custom') or action not in SAFE_GESTURE_ACTIONS:return False
    with LOCK,sqlite3.connect(DB) as db:db.execute('INSERT OR REPLACE INTO gesture_actions(label,action) VALUES(?,?)',(label,action))
    return True

def match(features):
    f=vector(features)
    if not f:return None
    with LOCK,sqlite3.connect(DB) as db:
        samples=db.execute('SELECT label,features FROM gesture_samples').fetchall()
        acts=dict(db.execute('SELECT label,action FROM gesture_actions'))
    if not samples:return None
    # Normalized absolute difference; basic heuristic, calibrate on hardware.
    by_label={}
    for label,raw in samples:
        ref=json.loads(raw)
        err=math.sqrt(sum(((x-y)/max(0.2,abs(y)))**2 for x,y in zip(f,ref))/6)
        by_label.setdefault(label,[]).append(err)
    candidates=sorted((sum(sorted(errors)[:3])/min(3,len(errors)),label,len(errors)) for label,errors in by_label.items())
    score,label,count=candidates[0]
    if count<2 or score>0.6:return None
    return {'label':label,'action':acts.get(label),'score':round(score,3)}
