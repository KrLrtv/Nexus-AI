"""Explicit source repair; apply after unpack.py, before packaging."""
from pathlib import Path
import hashlib, sys
root=Path(sys.argv[1] if len(sys.argv)>1 else 'work')
p=root/'pc/launcher.py'
if hashlib.sha256(p.read_bytes()).hexdigest()!='16a0c7b142f4c59f9b31aac60e70dd0d0b0afa608c840a44d2111c149550d318':
    raise SystemExit('Unexpected launcher base')
source='''"""Windows entry point: predictable UTF-8, including redirected output."""
import sys
for stream in (sys.stdout, sys.stderr):
    if stream is not None and hasattr(stream, 'reconfigure'):
        stream.reconfigure(encoding='utf-8', errors='backslashreplace')
from nexus.server import main
if __name__ == '__main__':
    main()
'''
p.write_bytes(source.encode('utf8'))
assert hashlib.sha256(p.read_bytes()).hexdigest()=='eb8396717a6ceac3a8c65c59ab0692513a854c8ced907b27ebb440f750049a27'
print('Launcher UTF-8 repair verified')
