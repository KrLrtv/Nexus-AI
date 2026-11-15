"""Declarative, approval-gated self-extension. No generated Python is executed."""
import os, re, json, sqlite3, threading, time
from pathlib import Path
from ai_provider import post_json, ProviderError
from agent import execute

DB=Path(os.environ.get('NEXUS_DB_PATH',str(Path.home()/'.nexus_ai'/'agent.sqlite3')))
DB.parent.mkdir(parents=True,exist_ok=True)
LOCK=threading.RLock()
# Only read-only, explicitly implemented capabilities may be composed by AI.
SAFE_STEPS={'battery','notes_list','reminders_list','memory_search','reminder_timed_list'}
MAX_STEPS=4

def db_connect():
    db=sqlite3.connect(DB,timeout=10)
    db.execute('CREATE TABLE IF NOT EXISTS extension_proposals (id INTEGER PRIMARY KEY, name TEXT, intent TEXT, description TEXT, recipe TEXT, status TEXT NOT NULL, reason TEXT, created INTEGER NOT NULL)')
    return db
with db_connect():pass

def validate(data):
    if not isinstance(data,dict):raise ValueError('JSON object required')
    name=data.get('name','')
    if not isinstance(name,str) or not re.fullmatch(r'[a-z][a-z0-9_]{2,31}',name):raise ValueError('Invalid extension name')
    description=data.get('description','')
    if not isinstance(description,str) or len(description)>240:raise ValueError('Invalid description')
    steps=data.get('steps')
    if not isinstance(steps,list) or not 1<=len(steps)<=MAX_STEPS:raise ValueError('Invalid steps count')
    cleaned=[]
    for step in steps:
        if not isinstance(step,dict) or set(step)-{'tool','argument'}:raise ValueError('Invalid step')
        tool=step.get('tool')
        if tool not in SAFE_STEPS:raise ValueError('Tool not allowed: '+str(tool))
        arg=step.get('argument','')
        if not isinstance(arg,str) or len(arg)>120:raise ValueError('Invalid argument')
        # No templating, interpolation, dynamic calls or network endpoints.
        cleaned.append({'tool':tool,'argument':arg})
    return {'name':name,'description':description,'steps':cleaned}

def propose(intent):
    intent=str(intent).strip()[:500]
    if not intent:raise ValueError('Empty request')
    key=os.environ.get('GEMINI_API_KEY','').strip()
    if not key:raise ProviderError('Gemini key required to design extension')
    model=os.environ.get('GEMINI_MODEL','gemini-2.5-flash-lite')
    prompt=('Ты проектировщик безопасных расширений для NEXUS. Верни ТОЛЬКО JSON с полями '
            'name (латиница snake_case), description, steps (массив объектов tool, argument). '
            'Разрешены ТОЛЬКО инструменты: battery, notes_list, reminders_list, memory_search, reminder_timed_list. '
            'Максимум 4 шага. Никакого Python, shell, сети, файлов, циклов, новых разрешений. '
            'Если задача невозможна, верни JSON {"unsupported":"причина и требуемые ресурсы"}. '
            'Никогда не утверждай, что получил отсутствующий доступ. Запрос: '+intent)
    result=post_json(f'https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent',
       {'contents':[{'role':'user','parts':[{'text':prompt}]}],
        'generationConfig':{'responseMimeType':'application/json','maxOutputTokens':500}}, {'x-goog-api-key':key})
    try:
        raw=''.join(p.get('text','') for p in result['candidates'][0]['content']['parts'])
        data=json.loads(raw)
    except (KeyError,TypeError,IndexError,ValueError) as exc:raise ValueError('Invalid model proposal') from exc
    if isinstance(data,dict) and 'unsupported' in data:
        reason=str(data['unsupported'])[:300]
        with LOCK,db_connect() as db:
            cur=db.execute('INSERT INTO extension_proposals (name,intent,description,recipe,status,reason,created) VALUES (?,?,?,?,?,?,?)',
                           ('',intent,'','{}','unsupported',reason,int(time.time())))
            return {'id':cur.lastrowid,'status':'unsupported','reason':reason}
    recipe=validate(data)
    with LOCK,db_connect() as db:
        cur=db.execute('INSERT INTO extension_proposals (name,intent,description,recipe,status,reason,created) VALUES (?,?,?,?,?,?,?)',
                       (recipe['name'],intent,recipe['description'],json.dumps(recipe,ensure_ascii=False),'pending','',int(time.time())))
        return {'id':cur.lastrowid,'status':'pending','recipe':recipe}

def listing():
    with LOCK,db_connect() as db:
        rows=db.execute('SELECT id,name,intent,description,recipe,status,reason FROM extension_proposals ORDER BY id DESC LIMIT 30').fetchall()
    return [{'id':i,'name':n,'intent':intent,'description':desc,'recipe':json.loads(recipe),'status':status,'reason':reason} for i,n,intent,desc,recipe,status,reason in rows]

def decide(identifier,approve):
    with LOCK,db_connect() as db:
        row=db.execute('SELECT recipe,status FROM extension_proposals WHERE id=?',(identifier,)).fetchone()
        if not row or row[1]!='pending':return {'error':'Not a pending proposal'},404
        if approve:
            # Re-check stored recipe, do not trust persisted model output.
            validate(json.loads(row[0]))
        db.execute('UPDATE extension_proposals SET status=? WHERE id=?',('active' if approve else 'rejected',identifier))
    return {'status':'active' if approve else 'rejected'},200

def run(identifier):
    with LOCK,db_connect() as db:
        row=db.execute('SELECT recipe,status FROM extension_proposals WHERE id=?',(identifier,)).fetchone()
    if not row or row[1]!='active':return {'error':'Not an active extension'},404
    recipe=validate(json.loads(row[0])); outputs=[]
    for step in recipe['steps']:
        outputs.append({'tool':step['tool'],'result':execute(step['tool'],step['argument'])[:1200]})
    return {'name':recipe['name'],'outputs':outputs},200
