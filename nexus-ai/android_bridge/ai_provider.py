"""NEXUS AI cloud provider, with a Gemini Free Tier -> OpenRouter Free fallback.
Keys are read from environment variables on Android, never embedded in ESP32 firmware.
"""
import json
import os
import urllib.request
import urllib.error
from nexus_vpn_transport_r83 import open_vpn as _nexus_vpn_open  # NEXUS_R83_VPN_FIX
from learning20 import STORE as _learning20_store

SYSTEM = ('Ты NEXUS AI, карманный русскоязычный помощник. '
          'Отвечай кратко, точно и дружелюбно, не утверждай, что выполнил действия, '
          'которых не выполнял. Ты работаешь через Android-мост.')


# NEXUS_R89_SPEED
from pathlib import Path as _nexus_r89_Path

def nexus_r89_fast():
    try:
        return (_nexus_r89_Path.home() / '.config/nexus/speed_mode').read_text().strip() == 'fast'
    except OSError:
        return False

class ProviderError(Exception):
    pass

def post_json(url, payload, headers, timeout=35):
    if nexus_r89_fast() and timeout == 35:
        timeout = 7
    req = urllib.request.Request(url, data=json.dumps(payload, ensure_ascii=False).encode('utf-8'),
                                 headers={'Content-Type': 'application/json', **headers}, method='POST')
    try:
        with _nexus_vpn_open(req, timeout=timeout) as resp:
            return json.load(resp)
    except urllib.error.HTTPError as exc:
        detail = exc.read(500).decode('utf-8', 'replace')
        raise ProviderError(f'HTTP {exc.code}: {detail}') from exc
    except (urllib.error.URLError, TimeoutError, ValueError) as exc:
        raise ProviderError(str(exc)) from exc

def gemini(prompt, history):
    key = os.getenv('GEMINI_API_KEY', '').strip()
    if not key:
        raise ProviderError('GEMINI_API_KEY отсутствует')
    model = os.getenv('GEMINI_MODEL', 'gemini-2.5-flash-lite').strip()
    contents = []
    for role, content in history[-8:]:
        contents.append({'role':'model' if role == 'assistant' else 'user',
                         'parts':[{'text':content}]})
    contents.append({'role':'user', 'parts':[{'text':prompt}]})
    payload={'systemInstruction': {'parts':[{'text': SYSTEM + _learning20_store.safe_context_for(prompt)}]},
             'contents':contents,
             'generationConfig': {'maxOutputTokens': (120 if nexus_r89_fast() else 320)}}
    data=post_json(f'https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent',
                   payload, {'x-goog-api-key':key})
    try:
        answer=''.join(x.get('text', '') for x in data['candidates'][0]['content']['parts']).strip()
    except (KeyError, IndexError, TypeError) as exc:
        raise ProviderError('Ответ Gemini не содержит текста') from exc
    if not answer:
        raise ProviderError('Пустой ответ Gemini')
    return answer

def openrouter(prompt, history):
    key=os.getenv('OPENROUTER_API_KEY', '').strip()
    if not key:
        raise ProviderError('OPENROUTER_API_KEY отсутствует')
    messages=[{'role':'system','content':SYSTEM + _learning20_store.safe_context_for(prompt)}]
    messages.extend({'role':r, 'content':t} for r,t in history[-8:])
    messages.append({'role':'user','content':prompt})
    data=post_json('https://openrouter.ai/api/v1/chat/completions',
                   {'model':'openrouter/free', 'messages':messages, 'max_tokens':320},
                   {'Authorization':f'Bearer {key}', 'X-Title':'NEXUS AI'})
    try:
        answer=data['choices'][0]['message']['content']
        if isinstance(answer, list):
            answer=' '.join(x.get('text','') for x in answer if isinstance(x,dict))
        answer=answer.strip()
    except (KeyError, IndexError, TypeError, AttributeError) as exc:
        raise ProviderError('Ответ OpenRouter не содержит текста') from exc
    if not answer:
        raise ProviderError('Пустой ответ OpenRouter')
    return answer

def ask(prompt, history):
    errors=[]
    if os.getenv('GEMINI_API_KEY'):
        try:return gemini(prompt, history), 'Gemini'
        except ProviderError as exc:
            errors.append(f'Gemini: {exc}')
            if nexus_r89_fast():
                raise ProviderError('Gemini недоступен: ' + str(exc)[:250]) from exc
    if os.getenv('OPENROUTER_API_KEY'):
        try:return openrouter(prompt, history), 'OpenRouter Free'
        except ProviderError as exc:errors.append(f'OpenRouter: {exc}')
    if not errors:
        raise ProviderError('Добавь GEMINI_API_KEY или OPENROUTER_API_KEY в Termux')
    raise ProviderError('Оба провайдера недоступны: ' + ' | '.join(errors)[:400])
