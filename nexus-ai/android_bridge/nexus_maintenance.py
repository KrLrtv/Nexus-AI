"""Run from Termux shell. Restoring requires that the NEXUS server is stopped."""
import argparse,os
from pathlib import Path
from recovery import backup,restore,integrity
p=argparse.ArgumentParser()
p.add_argument('action',choices=['backup','check','restore']);p.add_argument('--snapshot')
a=p.parse_args()
db=Path(os.getenv('NEXUS_DB_PATH',str(Path.home()/'.nexus_ai'/'agent.sqlite3')))
folder=Path.home()/'.nexus_ai'/'backups'
if a.action=='check':print('OK' if integrity(db) else 'CORRUPTED/MISSING')
elif a.action=='backup':print(backup(db,folder))
else:
    if not a.snapshot:p.error('--snapshot required')
    print(restore(db,a.snapshot,stopped=True))
