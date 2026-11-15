"""Transcribe short M5 voice clip and answer in one Gemini call."""
import base64
import os
from ai_provider import SYSTEM, ProviderError, post_json


def ask_audio(wav: bytes, history: list):
    key=os.environ.get('GEMINI_API_KEY','').strip()
    if not key:
        raise ProviderError('Для голосовых запросов нужен GEMINI_API_KEY')
    model=os.environ.get('GEMINI_MODEL','gemini-2.5-flash-lite')
    history_text='\n'.join(f'{role}: {value}' for role,value in history[-6:])
    # NEXUS_VOICE_FAST_R5: answer informational questions without a second model call.
    prompt=('Прослушай русскую или другую речь в записи. '
            'В первой строке напиши TRANSCRIPT: и точную расшифровку без выдуманных слов. '
            'Во второй строке напиши ANSWER: и содержательный, краткий ответ (1-3 предложения), '
            'если это простой информационный вопрос или приветствие. '
            'Если требуется реально управлять телефоном, менять данные, запускать инструменты '
            'или требуется особое разрешение, напиши ANSWER: ACTION_REQUIRED. '
            'Никогда не утверждай, что уже выполнил действие. '
            'Если речь неразборчива, оставь TRANSCRIPT: пустым. '
            'История предыдущего диалога:\n'+history_text)
    payload={
        'systemInstruction':{'parts':[{'text':SYSTEM}]},
        'contents':[{'role':'user','parts':[
            {'text':prompt},
            {'inlineData':{'mimeType':'audio/wav','data':base64.b64encode(wav).decode('ascii')}}
        ]}],
        'generationConfig':{'maxOutputTokens':400}
    }
    data=post_json(f'https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent',payload,{'x-goog-api-key':key})
    try:
        result=''.join(p.get('text','') for p in data['candidates'][0]['content']['parts'])
    except (KeyError,IndexError,TypeError) as e:
        raise ProviderError('Gemini вернул ответ без текста') from e
    lines=result.splitlines()
    transcript=next((x.partition(':')[2].strip() for x in lines if x.upper().startswith('TRANSCRIPT:')),'')
    answer=result.split('ANSWER:',1)[-1].strip() if 'ANSWER:' in result else result.strip()
    if not answer:raise ProviderError('Gemini вернул пустой ответ')
    return transcript,answer
