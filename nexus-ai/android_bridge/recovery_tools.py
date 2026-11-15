"""Safe update staging and rollback operations for trusted manually installed releases.
Unreviewed model-generated code is never activated here.
"""
import os,sys,subprocess,shutil,hashlib,zipfile,tempfile,json
from pathlib import Path

def inspect_release(archive:Path,expected_sha256:str,max_bytes=12_000_000):
    data=archive.read_bytes()
    if len(data)>max_bytes:raise ValueError('Release archive exceeds maximum size')
    if hashlib.sha256(data).hexdigest()!=expected_sha256.lower():raise ValueError('SHA256 mismatch')
    with zipfile.ZipFile(archive) as z:
        files=z.infolist()
        if len(files)>200:raise ValueError('Too many files')
        for e in files:
            p=Path(e.filename)
            if p.is_absolute() or '..' in p.parts or (e.external_attr>>16)&0o170000==0o120000:
                raise ValueError('Unsafe archive path/link')
            if e.file_size>2_000_000:raise ValueError('File too big')
        return len(files)

def stage_release(archive,destination,expected_sha256):
    archive=Path(archive);destination=Path(destination)
    inspect_release(archive,expected_sha256)
    if destination.exists():raise FileExistsError('Destination must be new')
    destination.parent.mkdir(parents=True,exist_ok=True)
    tmp=Path(tempfile.mkdtemp(prefix='.nexus-stage-',dir=destination.parent))
    try:
        with zipfile.ZipFile(archive) as z:z.extractall(tmp)
        py=list(tmp.rglob('*.py'))
        for p in py:
            compile(p.read_bytes(),str(p),'exec')
        tmp.replace(destination)
        return destination
    except Exception:
        shutil.rmtree(tmp,ignore_errors=True)
        raise
