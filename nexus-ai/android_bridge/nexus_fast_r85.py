"""NEXUS R8.5: safe local replies and optional streaming of read-only Gemini answers.

No device actions or model tool calls here. Everything not unambiguously local
continues through the original policy-aware R8.1 planner/quickbrain.
"""
from __future__ import annotations

import ast
import http.client
import json
import math
from pathlib import Path
import re
import time
import urllib.error
import urllib.request

from ai_provider import ProviderError, SYSTEM
from learning20 import STORE as _learning20_store
from nexus_quickbrain_r81 import ask_fast as original_ask_fast

STREAM_FLAG = Path.home() / '.config/nexus/r85_text_stream'


def stream_enabled() -> bool:
    try:
        return STREAM_FLAG.read_text(encoding='utf-8').strip() == 'on'
    except OSError:
        return False


def _math_result(original: str) -> str | None:
    source = original.strip().casefold()
    if not source or len(source) > 100:
        return None
    prefixes = ('сколько будет ', 'чему равно ', 'посчитай ', 'вычисли ', 'вычислить ')
    if source.startswith(prefixes):
        source = source[len(next(p for p in prefixes if source.startswith(p))):]
    elif not re.match(r'^[-+\d(]', source):
        return None
    source = source.strip().rstrip('?').strip()
    for pattern, substitution in ((r'\s+умножить на\s+', '*'), (r'\s+разделить на\s+', '/'),
                                   (r'\s+делить на\s+', '/'), (r'\s+плюс\s+', '+'),
                                   (r'\s+минус\s+', '-'), (r'\s+в степени\s+', '**')):
        source = re.sub(pattern, substitution, source)
    source = source.replace('×', '*').replace('÷', '/').replace('−', '-').replace('^', '**')
    # A comma is a decimal separator only between digits.
    source = re.sub(r'(?<=\d),(?=\d)', '.', source)
    if not source or len(source) > 64 or not re.fullmatch(r'[\d\s+\-*/().]+', source):
        return None
    try:
        tree = ast.parse(source, mode='eval')
        if sum(1 for _ in ast.walk(tree)) > 26:
            return None

        def evaluate(node, depth=0):
            if depth > 12:
                raise ValueError('too deep')
            if isinstance(node, ast.Constant) and type(node.value) in (int, float):
                v = float(node.value)
            elif isinstance(node, ast.UnaryOp) and type(node.op) in (ast.UAdd, ast.USub):
                v = evaluate(node.operand, depth+1) * (-1 if isinstance(node.op, ast.USub) else 1)
            elif isinstance(node, ast.BinOp) and type(node.op) in (ast.Add, ast.Sub, ast.Mult, ast.Div, ast.Pow):
                a = evaluate(node.left, depth+1)
                b = evaluate(node.right, depth+1)
                if isinstance(node.op, ast.Add): v = a+b
                elif isinstance(node.op, ast.Sub): v = a-b
                elif isinstance(node.op, ast.Mult): v = a*b
                elif isinstance(node.op, ast.Div): v = a/b
                else:
                    if abs(a) > 1e6 or abs(b) > 6:
                        raise ValueError('exponent too large')
                    v = a**b
            else:
                raise ValueError('not a supported arithmetic expression')
            if isinstance(v, complex) or not math.isfinite(v) or abs(v) > 1e12:
                raise ValueError('out of range')
            return v

        value = evaluate(tree.body)
        answer = str(int(value)) if value == int(value) else f'{value:.8g}'
        return 'Результат: ' + answer.replace('.', ',')
    except (ArithmeticError, ValueError, OverflowError, SyntaxError, TypeError):
        return None


def local_reply(query: str) -> str | None:
    """Only exact, unmistakably read-only requests bypass Gemini."""
    text = re.sub(r'\s+', ' ', str(query or '').casefold()).strip()
    text = re.sub(r'^(?:нексус|nexus)[\s,:!]+', '', text).strip()
    normalized = text.rstrip('?.!').strip()
    normalized = re.sub(r'^(привет|здравствуй|спасибо),?\s+(?:нексус|nexus)$', r'\1', normalized)
    simple = {
        'привет': 'Привет! Я NEXUS. Слушаю тебя.',
        'здравствуй': 'Привет! Я NEXUS. Слушаю тебя.',
        'доброе утро': 'Доброе утро! Я на связи.',
        'добрый вечер': 'Добрый вечер! Я на связи.',
        'спасибо': 'Пожалуйста!',
        'благодарю': 'Пожалуйста!',
        'ты тут': 'Да, я на связи.',
        'ты здесь': 'Да, я на связи.',
        'кто ты': 'Я NEXUS, карманный ИИ-помощник на M5StickC и телефоне.',
    }
    if normalized in simple:
        return simple[normalized]
    read_only = {
        'заряд телефона': 'battery',
        'какой заряд телефона': 'battery',
        'сколько заряда на телефоне': 'battery',
        'мои заметки': 'notes_list',
        'покажи заметки': 'notes_list',
        'список заметок': 'notes_list',
        'мои напоминания': 'reminders_list',
        'покажи напоминания': 'reminders_list',
        'список напоминаний': 'reminders_list',
    }
    if normalized in read_only:
        from agent import execute
        return str(execute(read_only[normalized], ''))[:1200]
    return _math_result(normalized)


def _build_payload(query: str, history: list, model: str) -> dict:
    contents = [{'role': 'model' if role == 'assistant' else 'user',
                 'parts': [{'text': str(body)[:450]}]}
                for role, body in history[-4:]]
    contents.append({'role': 'user', 'parts': [{'text': str(query)[:500]}]})
    generation = {'maxOutputTokens': 220, 'temperature': 0.35}
    if model.startswith(('gemini-3.1-flash-lite', 'gemini-3.5-flash-lite')):
        generation['thinkingConfig'] = {'thinkingLevel': 'minimal'}
    return {'systemInstruction': {'parts': [{'text': SYSTEM + _learning20_store.safe_context_for(query) +
        ' Отвечай кратко на русском (1–3 предложения). Не выполняй действий, '
        'не утверждай об использовании инструментов и не выдумывай актуальные сведения.'}]},
        'contents': contents, 'generationConfig': generation}


def _open_stream(url: str, request_data: bytes, api_key: str, timeout: int):
    req = urllib.request.Request(url, data=request_data,
                                 headers={'Content-Type': 'application/json',
                                          'Accept': 'text/event-stream',
                                          'x-goog-api-key': api_key}, method='POST')
    # Reuse the already installed R8.3 VPN transport, if present.
    # It does not bypass Android's system VPN or disable TLS certificate checks.
    try:
        from nexus_vpn_transport_r83 import open_vpn
    except ImportError:
        return urllib.request.urlopen(req, timeout=timeout)
    return open_vpn(req, timeout=timeout)


def ask_fast_stream(query: str, history: list, on_partial=None) -> str:
    """Read-only SSE generation. Only call after R8.1's is_fast_info() veto.

    We deliberately NEVER retry once text has started arriving, to avoid
    treating a truncated network response as a successful full answer.
    """
    import os
    key = os.environ.get('GEMINI_API_KEY', '').strip()
    model = os.environ.get('GEMINI_MODEL', 'gemini-3.1-flash-lite').strip()
    if not key:
        raise ProviderError('Не найден Gemini API ключ')
    if not re.fullmatch(r'[a-zA-Z0-9._-]+', model):
        raise ProviderError('Неверное имя модели Gemini')
    url = 'https://generativelanguage.googleapis.com/v1beta/models/' + model + ':streamGenerateContent?alt=sse'
    raw = json.dumps(_build_payload(query, history, model), ensure_ascii=False).encode('utf-8')
    assembled = ''
    complete = False
    last_update = 0.0
    events = 0
    try:
        with _open_stream(url, raw, key, timeout=16) as response:
            # Google SSE: each "data: {json}" line contains a fragment.
            for line in response:
                events += 1
                if events > 450:
                    raise ProviderError('Слишком длинный ответ Gemini (SSE)')
                if len(line) > 70000:
                    raise ProviderError('Некорректный большой SSE-фрагмент')
                if not line.startswith(b'data:'):
                    continue
                value = line[5:].strip()
                if value == b'[DONE]':
                    complete = True
                    break
                try:
                    item = json.loads(value)
                except (ValueError, UnicodeDecodeError):
                    continue
                candidates = item.get('candidates') or []
                if not candidates:
                    continue
                candidate = candidates[0]
                parts = (candidate.get('content') or {}).get('parts') or []
                fragment = ''.join(str(p.get('text', '')) for p in parts if isinstance(p, dict))
                # Most Gemini SSE updates are deltas. Some gateways send cumulative text.
                if fragment:
                    assembled = fragment if fragment.startswith(assembled) else assembled + fragment
                    if len(assembled) > 2200:
                        raise ProviderError('Ответ Gemini превысил лимит длины')
                    now = time.monotonic()
                    if on_partial is not None and len(assembled) > 10 and now - last_update >= 0.55:
                        on_partial(assembled)
                        last_update = now
                reason = candidate.get('finishReason')
                if reason == 'STOP':
                    complete = True
                elif reason:
                    raise ProviderError('Gemini завершил поток без полного ответа (' + str(reason)[:30] + ')')
            if not complete:
                raise ProviderError('VPN прервал поток ответа Gemini; попробуй ещё раз')
    except urllib.error.HTTPError as exc:
        # Do not include API keys, request headers, or full URL in the log.
        raise ProviderError('Gemini HTTP ' + str(exc.code)) from exc
    except (urllib.error.URLError, TimeoutError, OSError, http.client.RemoteDisconnected) as exc:
        raise ProviderError('Нет устойчивого соединения с Gemini через VPN ('+
                            type(exc).__name__+')') from exc
    if not assembled.strip():
        raise ProviderError('Gemini вернул пустой поток ответа')
    return assembled.strip()


def ask_fast_r85(query: str, history: list, on_partial=None) -> str:
    if not stream_enabled():
        return original_ask_fast(query, history)
    return ask_fast_stream(query, history, on_partial)
