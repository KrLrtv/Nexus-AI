"""NEXUS R8.4: Keep the already installed sherpa-onnx STT worker warm.

Runs in Termux's NEXUS supervisor (not in the ESP32). Starts exactly one
monitor thread; the existing 'nexus-stt start' handles the worker lifecycle.
Local port 8766 only. No cloud calls or secret handling.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
import subprocess
import threading
import time
from urllib.request import ProxyHandler, Request, build_opener

STT_HEALTH_URL = 'http://127.0.0.1:8766/health'
MODE_PATH = Path.home() / '.config/nexus/stt_mode'
CHECK_INTERVAL_SECONDS = 25
FAILED_RETRY_SECONDS = 100
START_TIMEOUT_SECONDS = 56
_guard = threading.Lock()
_started = False


def _worker_ready() -> bool:
    try:
        opener = build_opener(ProxyHandler({}))  # Never send localhost through VPN/proxy.
        with opener.open(Request(STT_HEALTH_URL, headers={'Accept': 'application/json'}), timeout=1.2) as response:
            if response.status != 200:
                return False
            obj = json.load(response)
        return obj.get('ok') is True and obj.get('engine') == 'sherpa'
    except (OSError, TimeoutError, ValueError, TypeError):
        return False


def _monitor() -> None:
    last_down = None
    while True:
        # Voice/UI settings are authoritative: do not relaunch STT when disabled.
        try:
            mode = MODE_PATH.read_text(encoding='utf-8').strip()
        except OSError:
            mode = 'gemini'
        if mode != 'sherpa':
            last_down = None
            time.sleep(CHECK_INTERVAL_SECONDS)
            continue
        if _worker_ready():
            if last_down is not None:
                print('NEXUS STT AUTO R8.4: sherpa ready at 127.0.0.1:8766', flush=True)
                last_down = None
            time.sleep(CHECK_INTERVAL_SECONDS)
            continue
        if last_down is None:
            print('NEXUS STT AUTO R8.4: sherpa offline; attempting local startup', flush=True)
            last_down = time.monotonic()
        cmd = shutil.which('nexus-stt')
        if cmd is None:
            print('NEXUS STT AUTO R8.4: nexus-stt not installed; see R6 setup', flush=True)
            time.sleep(FAILED_RETRY_SECONDS)
            continue
        try:
            completed = subprocess.run(
                [cmd, 'start'], stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                timeout=START_TIMEOUT_SECONDS, check=False, env=os.environ.copy(),
            )
            if completed.returncode != 0 or not _worker_ready():
                print('NEXUS STT AUTO R8.4: start failed; check ~/nexus-stt.log', flush=True)
                time.sleep(FAILED_RETRY_SECONDS)
            else:
                print('NEXUS STT AUTO R8.4: sherpa started automatically', flush=True)
                last_down = None
                time.sleep(CHECK_INTERVAL_SECONDS)
        except subprocess.TimeoutExpired:
            print('NEXUS STT AUTO R8.4: model startup timed out; retry later', flush=True)
            time.sleep(FAILED_RETRY_SECONDS)
        except OSError as exc:
            print(f'NEXUS STT AUTO R8.4: cannot start worker ({type(exc).__name__})', flush=True)
            time.sleep(FAILED_RETRY_SECONDS)


def start_local_stt_monitor() -> None:
    """Called once by supervisor.run(). Never block Flask startup."""
    global _started
    with _guard:
        if _started:
            return
        thread = threading.Thread(target=_monitor, name='nexus-stt-r84', daemon=True)
        thread.start()
        _started = True
        print('NEXUS STT AUTO R8.4: monitor enabled', flush=True)
