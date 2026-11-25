"""Verified transport archive; contains original UTF-8 source, not binaries."""
from pathlib import Path
import hashlib, io, tarfile, sys
root = Path(__file__).resolve().parent
raw = b''.join(p.read_bytes() for p in sorted((root / 'parts').glob('*.bin')))
expected = '250723ba2dd0c24e21206e3f64aadee69ae1de95871dc71e34628bfc12dd8232'
if hashlib.sha256(raw).hexdigest() != expected:
    raise SystemExit('Source archive SHA256 mismatch; refusing extraction')
output = Path(sys.argv[1] if len(sys.argv)>1 else 'work')
output.mkdir(parents=True, exist_ok=True)
with tarfile.open(fileobj=io.BytesIO(raw), mode='r:xz') as archive:
    archive.extractall(output, filter='data')
print('Source extracted and SHA256 verified:', expected)
