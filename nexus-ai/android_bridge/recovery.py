"""Atomic local DB snapshots, integrity checks and explicit/manual restore.
Never restore while the application holds a live database connection.
"""
import os,sqlite3,time,hashlib,json,shutil
from pathlib import Path

BACKUP_LIMIT=5

def integrity(db_path):
    db=Path(db_path)
    if not db.exists():return False
    try:
        with sqlite3.connect(f'file:{db}?mode=ro',uri=True) as conn:
            return conn.execute('PRAGMA integrity_check').fetchone()[0]=='ok'
    except (OSError,sqlite3.DatabaseError):return False

def backup(db_path,backup_dir):
    db=Path(db_path); out=Path(backup_dir);out.mkdir(parents=True,exist_ok=True)
    if not integrity(db):raise RuntimeError('Refusing to back up corrupted database')
    stamp=time.strftime('%Y%m%d-%H%M%S')
    target=out/f'agent-{stamp}-{time.time_ns()%1000000:06d}.sqlite3'
    with sqlite3.connect(str(db)) as source,sqlite3.connect(str(target)) as destination:
        source.backup(destination)
    if not integrity(target):
        target.unlink(missing_ok=True);raise RuntimeError('Snapshot failed integrity check')
    previous=sorted(out.glob('agent-*.sqlite3'),key=lambda p:p.stat().st_mtime,reverse=True)
    for extra in previous[BACKUP_LIMIT:]:extra.unlink()
    return target

def restore(db_path,snapshot,stopped=False):
    if not stopped:raise PermissionError('Stop NEXUS first, then restore explicitly')
    db=Path(db_path);shot=Path(snapshot)
    if not integrity(shot):raise ValueError('Invalid snapshot')
    if not shot.name.startswith('agent-') or shot.suffix!='.sqlite3':raise ValueError('Invalid snapshot name')
    db.parent.mkdir(parents=True,exist_ok=True)
    tmp=db.with_name(db.name+'.restore-tmp')
    shutil.copy2(shot,tmp)
    if not integrity(tmp):tmp.unlink(missing_ok=True);raise RuntimeError('Invalid temporary restore')
    os.replace(tmp,db)
    return db
