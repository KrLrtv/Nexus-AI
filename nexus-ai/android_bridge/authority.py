"""Voice approvals: single-use, expiring, bound to a specific proposed action.
Voice recognition is NOT identity authentication. Hardware confirmation is required for
activating new abilities, deletion and other highly sensitive operations.
"""
import os, secrets, time, sqlite3, threading, re
from pathlib import Path

DB=Path(os.environ.get('NEXUS_DB_PATH',str(Path.home()/'.nexus_ai'/'agent.sqlite3')))
DB.parent.mkdir(parents=True,exist_ok=True)
LOCK=threading.RLock()
TTL=90
VOICE_ACTIONS={'torch_on','torch_off','notify','speak'}
BUTTON_ACTIONS={'memory_delete','reminder_timed_cancel','extension_activate','permission_grant','permission_revoke'}

def db():
    c=sqlite3.connect(DB,timeout=10)
    c.execute('CREATE TABLE IF NOT EXISTS approvals (id TEXT PRIMARY KEY, action TEXT NOT NULL, argument TEXT NOT NULL, code TEXT NOT NULL, level TEXT NOT NULL, created REAL NOT NULL, expires REAL NOT NULL, state TEXT NOT NULL, voice_ok INTEGER NOT NULL DEFAULT 0)')
    c.execute('CREATE TABLE IF NOT EXISTS approval_log (id INTEGER PRIMARY KEY, action TEXT, decision TEXT, created REAL)')
    return c
with db():pass

def request(action,argument=''):
    if action not in VOICE_ACTIONS|BUTTON_ACTIONS:raise ValueError('Action not eligible for voice approval')
    with LOCK,db() as c:
        c.execute("UPDATE approvals SET state='superseded' WHERE state IN ('pending','voice_ok')")
        code=str(secrets.randbelow(9000)+1000);ident=secrets.token_urlsafe(16)
        level='voice_button' if action in BUTTON_ACTIONS else 'voice'
        t=time.time()
        c.execute('INSERT INTO approvals (id,action,argument,code,level,created,expires,state) VALUES (?,?,?,?,?,?,?,?)', (ident,action,str(argument)[:500],code,level,t,t+TTL,'pending'))
    return current()

def current():
    with LOCK,db() as c:
        c.execute("UPDATE approvals SET state='expired' WHERE state IN ('pending','voice_ok') AND expires < ?",(time.time(),))
        r=c.execute("SELECT id,action,argument,code,level,expires,state,voice_ok FROM approvals WHERE state IN ('pending','voice_ok') ORDER BY created DESC LIMIT 1").fetchone()
    if not r:return None
    return dict(zip(('id','action','argument','code','level','expires','state','voice_ok'),r))

def consume(text,source='device'):
    # Authorizations must come from authenticated device audio, not a generic text prompt.
    if source!='device':return {'state':'ignored'}
    p=current()
    if not p:return {'state':'none'}
    normalized=re.sub(r'[^\w\s]',' ',text.lower(),flags=re.UNICODE)
    if any(word in normalized.split() for word in ('отмена','отменяю','запрещаю')):
        with LOCK,db() as c:c.execute("UPDATE approvals SET state='denied' WHERE id=? AND state IN ('pending','voice_ok')",(p['id'],))
        return {'state':'denied'}
    # Don't parse free-form yes: demand a fresh challenge and the exact action scope.
    if not re.search(r'\b(подтверждаю|разрешаю)\b',normalized) or not re.search(r'(?<!\d)'+re.escape(p['code'])+r'(?!\d)',normalized):
        return {'state':'wrong_phrase'}
    if p['level']=='voice_button':
        with LOCK,db() as c:c.execute("UPDATE approvals SET voice_ok=1,state='voice_ok' WHERE id=? AND state='pending'",(p['id'],))
        return {'state':'await_button','action':p['action']}
    return commit(p['id'])

def button_confirm():
    p=current()
    if not p or p['level']!='voice_button' or not p['voice_ok']:return {'state':'nothing_to_confirm'}
    return commit(p['id'])

def commit(ident):
    with LOCK,db() as c:
        p=c.execute('SELECT action,argument,level,state,voice_ok,expires FROM approvals WHERE id=?',(ident,)).fetchone()
        if not p or p[5]<time.time() or p[3] not in ('pending','voice_ok'):return {'state':'expired'}
        if p[2]=='voice_button' and not p[4]:return {'state':'await_button'}
        # Claim before executing. Repeated audio/button cannot double-execute.
        c.execute("UPDATE approvals SET state='consumed' WHERE id=? AND state IN ('pending','voice_ok')",(ident,))
        c.execute('INSERT INTO approval_log(action,decision,created) VALUES (?,?,?)',(p[0],'approved',time.time()))
    if p[0] in ('permission_grant','permission_revoke'):
        from core10 import set_permission
        set_permission(p[1],p[0]=='permission_grant')
        if p[1]=='torch_on':set_permission('torch_off',p[0]=='permission_grant')
        return {'state':'executed','result':'Permanent permission changed: '+p[1]}
    if p[0]=='extension_activate':
        from extensions import decide
        result,status=decide(int(p[1]),True)
        return {'state':'executed' if status==200 else 'failed','result':result}
    from agent import execute
    result=execute(p[0],p[1])
    return {'state':'executed','result':result}

def cancel_all():
    with LOCK,db() as c:c.execute("UPDATE approvals SET state='denied' WHERE state IN ('pending','voice_ok')")

# NEXUS_R893_DIRECT_COMMANDS -- retire internal NEXUS code/B approval entry points.
# Android OS permissions and authenticated device tokens remain untouched.
def request(action, argument=''):
    raise RuntimeError('Внутренние подтверждения NEXUS отключены')

def current():
    return None

def consume(text, source='device'):
    return {'state': 'disabled'}

def button_confirm():
    return {'state': 'disabled'}

def commit(ident):
    return {'state': 'disabled'}
