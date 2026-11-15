#!/usr/bin/env python3
"""Fail the public release on actual key-like strings and private files.

Does not print suspicious secret values, only file and line number.
"""
from pathlib import Path
import re, sys
root=Path(__file__).resolve().parent.parent
patterns=[
    re.compile(r'AIza[A-Za-z0-9_-]{28,}'),
    re.compile(r'(?:gh[pousr]_|github_pat_)[A-Za-z0-9_]{25,}'),
    re.compile(r'sk-or-v1-[A-Za-z0-9]{30,}'),
    re.compile(r'sk-(?:proj-)?[A-Za-z0-9_-]{32,}'),
    re.compile(r'-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----'),
]
blocked={'config_local.h','config_generated.h','secrets.env','device.env','gemini.env','.env','credentials.json','token.json'}
problems=[]
for p in root.rglob('*'):
    if not p.is_file() or '.git' in p.parts:continue
    if p.name in blocked:
        problems.append((str(p.relative_to(root)),0,'private configuration file'))
    if p.suffix.lower() not in {'.py','.cpp','.h','.ini','.sh','.md','.yml','.yaml','.txt','.xml','.json'}:continue
    try:data=p.read_text(encoding='utf-8')
    except UnicodeError:continue
    for pattern in patterns:
        for m in pattern.finditer(data):
            problems.append((str(p.relative_to(root)),data.count('\n',0,m.start())+1,'key-like value'))
for path,line,kind in problems:print(f'BLOCKED: {path}:{line} {kind}',file=sys.stderr)
if problems:raise SystemExit(1)
print('Secret pattern audit OK (manual review still required)')
