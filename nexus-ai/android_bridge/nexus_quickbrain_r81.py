"""NEXUS R8.1: fast, no-tools answers for a conservative set of informational queries.

This module has NO capability to call NEXUS device/Android actions. Everything with
unclear or potentially actionable intent is handled by the existing agent planner.
"""
from __future__ import annotations

import os
import re
from ai_provider import post_json, ProviderError, SYSTEM
from learning20 import STORE as _learning20_store
import http.client as _nexus_http
import ssl as _nexus_ssl
import time as _nexus_time

_PREFIXES = (
    'привет', 'здравствуй', 'добрый день', 'добрый вечер',
    'что такое ', 'что значит ', 'что означает ', 'почему ', 'зачем ',
    'объясни ', 'расскажи о ', 'расскажи про ', 'как работает ',
    'как устроен ', 'как устроена ', 'как устроено ',
    'кто такой ', 'кто такая ', 'кто такие ', 'чем отличается ',
    'в чем разница ', 'в чём разница ', 'для чего ',
    'сколько будет ', 'сколько получится ', 'переведи на ',
)

# Veto action/memory/permission/system/settings requests. If unsure, use the
# original policy-aware planner. Fast branch can only return text, not act.
_VETO = (
    'включ', 'выключ', 'отключ', 'открой', 'запусти', 'установ',
    'удали', 'созда', 'запиши', 'запомн', 'сохрани', 'пришли',
    'отправ', 'нажми', 'перезагруз', 'выполн', 'разреш', 'подтвер',
    'отмен', 'закаж', 'напомн', 'уведомл', 'фонар', 'музык',
    'громкост', 'телефон', 'android', 'termux', 'nexus', 'нексус',
    'настрой', 'плагин', 'модул', 'приложен', 'батаре',
    'памят', 'заметк', 'цель', 'задач', 'команд', 'инструмент',
    'сделай', 'включение', 'отправка',
)


def is_fast_info(query: str) -> bool:
    s = ' '.join(str(query or '').casefold().split())
    s = re.sub(r'^(?:нексус|nexus)[,!: ]+', '', s)
    if not s or len(s) > 250 or '\n' in str(query):
        return False
    # Avoid stale/made-up current news and live data in a no-tools route.
    dynamic = ('сегодня', 'завтра', 'сейчас', 'последн', 'новост', 'погод', 'курс ',
               'который час', 'сколько времени', 'расписан', 'кто президент')
    if any(term in s for term in dynamic):
        return False
    if any(term in s for term in _VETO):
        return False
    return s.startswith(_PREFIXES)


def ask_fast(query: str, history: list) -> str:
    key = os.getenv('GEMINI_API_KEY', '').strip()
    if not key:
        raise ProviderError('Gemini API key is missing')
    model = os.getenv('GEMINI_MODEL', 'gemini-3.1-flash-lite').strip()
    if not re.fullmatch(r'[a-zA-Z0-9._-]+', model):
        raise ProviderError('Invalid Gemini model configuration')
    contents = [{'role':'model' if role == 'assistant' else 'user',
                 'parts':[{'text':str(body)[:500]}]}
                for role,body in history[-4:]]
    contents.append({'role':'user','parts':[{'text':str(query)[:500]}]})
    config = {'maxOutputTokens': 250, 'temperature': 0.5}
    # Gemini 3.1 Flash-Lite supports minimal thinking; don't send this
    # parameter to Gemini 2.5 models or non-Lite 3.x/Pro without checking.
    if model.startswith(('gemini-3.1-flash-lite','gemini-3.5-flash-lite')):
        config['thinkingConfig'] = {'thinkingLevel':'minimal'}
    payload = {
        'systemInstruction': {'parts':[{'text': SYSTEM + _learning20_store.safe_context_for(query) +
            ' Отвечай на вопрос кратко, на русском, 1–3 предложения. '
            'Не вызывай инструменты, не выполняй действий, не выдумывай актуальные факты.'}]},
        'contents':contents,
        'generationConfig':config,
    }
    url=f'https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent'
    # NEXUS_R83_VPN_FIX: shared provider handles one retry through system VPN.
    data=post_json(url,payload,{'x-goog-api-key':key},timeout=18)
    try:
        parts=data['candidates'][0]['content']['parts']
        text=''.join(str(item.get('text','')) for item in parts if isinstance(item,dict)).strip()
    except (KeyError,TypeError,IndexError,ValueError) as ex:
        raise ProviderError('Gemini returned no text content') from ex
    if not text:
        raise ProviderError('Gemini returned an empty answer')
    return text[:1300]
