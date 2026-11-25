"""Verified source transport plus explicit, hash-checked build repairs."""
from pathlib import Path
import hashlib, io, tarfile, sys, re
root = Path(__file__).resolve().parent
raw = b''.join(p.read_bytes() for p in sorted((root / 'parts').glob('*.bin')))
expected = '250723ba2dd0c24e21206e3f64aadee69ae1de95871dc71e34628bfc12dd8232'
if hashlib.sha256(raw).hexdigest() != expected:
    raise SystemExit('Source archive SHA256 mismatch; refusing extraction')
output = Path(sys.argv[1] if len(sys.argv)>1 else 'work')
output.mkdir(parents=True, exist_ok=True)
with tarfile.open(fileobj=io.BytesIO(raw), mode='r:xz') as archive:
    archive.extractall(output, filter='data')
p = output / 'firmware/src/main.cpp'
if hashlib.sha256(p.read_bytes()).hexdigest() != '1d0f056881ad2c5cb014804f9796f36daf6b5ac4b45da5d232b194017d7c6589':
    raise SystemExit('Unexpected firmware source before repair')
text, count = re.subn(r'\blink\b', 'btLink', p.read_text(encoding='utf-8'))
if count != 16:
    raise SystemExit('Unexpected rename count')
p.write_bytes(text.encode('utf-8'))
if hashlib.sha256(p.read_bytes()).hexdigest() != '91cf0d931d6b0d6c0eae12dd3c576cbdce2737010d38f587c42cb7a05ff7b83b':
    raise SystemExit('Unexpected firmware source after repair')
print('Source archive verified; explicit firmware symbol repair applied:', expected)
