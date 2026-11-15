"""Independent background operation; bounded RSS schedule, no promises of precise Android timing."""
import os,threading,time,subprocess,shutil,json,logging
from datetime import datetime
from core10 import log,enqueue
from research10 import scan
STOP=threading.Event()
THREAD=None
logger=logging.getLogger('nexus')
def notify(text):
    if not shutil.which('termux-notification'):return False
    try:
        p=subprocess.run(['termux-notification','--title','NEXUS AI','--content',str(text)[:160]],capture_output=True,timeout=6)
        return p.returncode==0
    except (OSError,subprocess.TimeoutExpired):return False
def runner():
    last=0
    last_backup=0
    while not STOP.wait(30):
        if os.getenv("NEXUS_AUTO_BACKUP","1")=="1" and time.time()-last_backup>21600:
            last_backup=time.time()
            try:
                from core10 import DB
                from recovery import backup
                backup(DB, DB.parent/"backups")
            except Exception as exc:
                logger.warning("Backup skipped: %s",exc)
        # Opt-in autonomous RSS research, six-hour interval; no uncontrolled crawling.
        if os.getenv('NEXUS_AUTO_RESEARCH')=='1' and time.time()-last>21600:
            last=time.time()
            try:
                found=scan(os.getenv('NEXUS_RESEARCH_TOPIC','technology'))
                if found:notify('Новые материалы по теме: '+str(len(found)))
            except Exception as exc:
                log('research','error',str(exc)[:180]);logger.warning('Research failed: %s',exc)
def start():
    global THREAD
    if THREAD is None or not THREAD.is_alive():
        STOP.clear();THREAD=threading.Thread(target=runner,daemon=True,name='nexus-runtime');THREAD.start()
def shutdown():STOP.set()
