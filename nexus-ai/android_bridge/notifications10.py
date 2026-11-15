"""Local notification digest; requires manually granted Termux:API notification access."""
import json,subprocess,shutil
from notification_engine import summarize

def digest():
    if not shutil.which('termux-notification-list'):
        return {'error':'Install Termux:API and grant Android notification access'}
    try:
        proc=subprocess.run(['termux-notification-list'],capture_output=True,text=True,timeout=6)
        if proc.returncode: return {'error':proc.stderr[:180]}
        items=json.loads(proc.stdout)
        if not isinstance(items,list):return {'error':'Unexpected notification format'}
        return summarize(items[:80])
    except (ValueError,OSError,subprocess.TimeoutExpired) as exc:
        return {'error':str(exc)[:180]}
