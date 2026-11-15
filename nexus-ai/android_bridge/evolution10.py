"""Generate *drafts* of Python modules, NEVER execute draft code in Termux.
Syntax/AST lint is not sandboxing nor evidence of safety.
"""
import os,ast,json,time,hashlib,urllib.request
from pathlib import Path
from core10 import log
BASE=Path(os.getenv('NEXUS_EVOLUTION_DIR',str(Path.home()/'.nexus_ai'/'evolution')))
BASE.mkdir(parents=True,exist_ok=True)
MAX_SOURCE=12000
ALLOWED_IMPORTS={'math','re','json'}
class ReviewRequired(Exception):pass
def static_review(code):
    if len(code)>MAX_SOURCE:raise ValueError('Source exceeds limit')
    tree=ast.parse(code)
    problems=[]
    for n in ast.walk(tree):
        if isinstance(n,(ast.Import,ast.ImportFrom)):
            for a in (n.names if isinstance(n,ast.Import) else []):
                if a.name.split('.')[0] not in ALLOWED_IMPORTS:problems.append('blocked import '+a.name)
            if isinstance(n,ast.ImportFrom) and (n.module or '').split('.')[0] not in ALLOWED_IMPORTS:problems.append('blocked import '+str(n.module))
        if isinstance(n,ast.Call) and isinstance(n.func,ast.Name) and n.func.id in ('eval','exec','compile','__import__','open','input'):problems.append('blocked call '+n.func.id)
    return problems
def stage(name,task,source):
    name=''.join(c for c in name.lower() if c.isalnum() or c=='_')[:42]
    if not name or not name[0].isalpha():raise ValueError('invalid name')
    problems=static_review(source)
    fingerprint=hashlib.sha256(source.encode()).hexdigest()[:12]
    dest=BASE/(name+'_'+fingerprint)
    dest.mkdir(exist_ok=True)
    (dest/'plugin.py').write_text(source,encoding='utf-8')
    meta={'name':name,'task':task[:500],'sha256':fingerprint,'issues':problems,'status':'draft_never_executed','created':int(time.time())}
    (dest/'metadata.json').write_text(json.dumps(meta,ensure_ascii=False,indent=2),encoding='utf-8')
    log('evolution','draft',name+' '+fingerprint)
    return meta
def generate(task):
    task=str(task).strip()[:700]
    if not task:raise ValueError('empty task')
    key=os.getenv('GEMINI_API_KEY','')
    if not key:raise ValueError('GEMINI_API_KEY is required')
    model=os.getenv('GEMINI_MODEL','gemini-2.5-flash-lite')
    url=f'https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent'
    payload={'contents':[{'role':'user','parts':[{'text':'Create a tiny standalone pure Python function for this task. Return ONLY JSON {"name":"snake_case","code":"..."}. No imports except math, re, json. No filesystem, networking, subprocess, dynamic evaluation or secrets. The code is a proposal only, not executed. Task: '+task}]}], 'generationConfig':{'responseMimeType':'application/json','maxOutputTokens':1800}}
    req=urllib.request.Request(url,data=json.dumps(payload).encode(),headers={'Content-Type':'application/json','x-goog-api-key':key},method='POST')
    with urllib.request.urlopen(req,timeout=24) as resp:ans=json.load(resp)
    content=''.join(p.get('text','') for p in ans['candidates'][0]['content']['parts'])
    obj=json.loads(content)
    if not isinstance(obj.get('code'),str):raise ValueError('invalid model output')
    return stage(str(obj.get('name','plugin')),task,obj['code'])
def drafts():
    result=[]
    for f in sorted(BASE.glob('*/metadata.json'),reverse=True)[:40]:
        try:result.append(json.loads(f.read_text()))
        except (ValueError,OSError):pass
    return result
