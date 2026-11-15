"""NEXUS R8: bounded, persisted voice-controlled runtime options.

No AI permissions, arbitrary shell, model downloads, or background installs.
The sherpa worker must already be running before on-device / voice enable.
"""
from __future__ import annotations
import json
import os
from pathlib import Path
import re
import tempfile
import threading
from urllib.request import Request, ProxyHandler, build_opener

CONFIG_DIR = Path.home() / '.config' / 'nexus'
SETTINGS_FILE = CONFIG_DIR / 'device_ui.json'
STT_MODE_FILE = CONFIG_DIR / 'stt_mode'
_DEFAULT = {'tts': True, 'streaming': False, 'autoscroll': True}
_MUTEX = threading.RLock()
_KEYS = frozenset(('tts', 'local_stt', 'streaming', 'autoscroll'))


def _write_atomic(path: Path, body: str):
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(prefix='.nexus-r8-', dir=str(path.parent))
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as f:
            f.write(body)
            f.flush()
            os.fsync(f.fileno())
        os.chmod(name, 0o600)
        os.replace(name, path)
    finally:
        if os.path.exists(name):
            os.unlink(name)


def read() -> dict:
    with _MUTEX:
        try:
            found = json.loads(SETTINGS_FILE.read_text(encoding='utf-8'))
        except (OSError, ValueError, TypeError):
            found = {}
        result = {key: found.get(key) if type(found.get(key)) is bool else default
                  for key, default in _DEFAULT.items()}
        try:
            result['local_stt'] = STT_MODE_FILE.read_text(encoding='utf-8').strip() == 'sherpa'
        except OSError:
            result['local_stt'] = False
        return result


def _stt_ready() -> bool:
    url = 'http://127.0.0.1:8766/health'
    try:
        req = Request(url, headers={'Accept': 'application/json'})
        with build_opener(ProxyHandler({})).open(req, timeout=1.5) as resp:
            result = json.load(resp)
        return result.get('ok') is True and result.get('engine') == 'sherpa'
    except (OSError, TimeoutError, ValueError, TypeError):
        return False


def update(key: str, enable: bool) -> tuple[bool, str]:
    if key not in _KEYS or type(enable) is not bool:
        return False, 'Неизвестная настройка'
    if key == 'local_stt':
        if enable and not _stt_ready():
            return False, 'Локальная модель не запущена. В Termux введи nexus-stt start'
        with _MUTEX:
            _write_atomic(STT_MODE_FILE, 'sherpa\n' if enable else 'gemini\n')
    else:
        with _MUTEX:
            current = read()
            stored = {k: current[k] for k in _DEFAULT}
            stored[key] = enable
            _write_atomic(SETTINGS_FILE, json.dumps(stored, ensure_ascii=False))
    labels = {'tts': 'Озвучивание', 'local_stt': 'Локальное распознавание',
              'streaming': 'Потоковая передача голоса', 'autoscroll': 'Автопрокрутка'}
    return True, labels[key] + (' включено' if enable else ' выключено')


def handle_voice(text: str):
    """Return (message, success) only for explicit local settings utterances.

    A refusal, quoted text, or ambiguous phrase is NEVER treated as approval.
    """
    t = ' '.join(text.casefold().replace('ё', 'е').split()).strip(' .!?')
    t = re.sub(r'^(?:нексус|nexus)[, ]+', '', t)
    if t in ('покажи настройки', 'какие настройки', 'настройки нексус', 'настройки nexus'):
        x = read()
        def state(v): return 'включено' if v else 'выключено'
        return ('Озвучивание ' + state(x['tts']) + '. Локальное распознавание ' +
                state(x['local_stt']) + '. Потоковый голос ' + state(x['streaming']) +
                '. Автопрокрутка ' + state(x['autoscroll']) + '.', True)
    match = re.fullmatch(r'(включи|включить|активируй|выключи|выключить|отключи)\s+(.+)', t)
    if not match:
        return None
    enable = match.group(1) in ('включи', 'включить', 'активируй')
    target = match.group(2)
    words = {
        'tts': ('озвучивание', 'озвучку', 'голосовое озвучивание', 'чтение ответов', 'озвучивание текста'),
        'local_stt': ('локальное распознавание', 'локальное распознавание речи', 'распознавание на телефоне', 'локальный stt', 'sherpa'),
        'streaming': ('потоковый голос', 'потоковую передачу', 'потоковую передачу голоса', 'экспериментальный потоковый голос', 'стриминг голоса'),
        'autoscroll': ('автопрокрутку', 'автоматическую прокрутку', 'автопрокрутка', 'автоматическое листание'),
    }
    for key, aliases in words.items():
        if target in aliases:
            return update(key, enable)
    return None
