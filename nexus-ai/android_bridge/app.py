"""Experimental Termux bridge for NEXUS AI. Bind to trusted local Wi-Fi only."""
import os
import threading
import unicodedata
from flask import Flask, request, jsonify, render_template_string
from nexus_quickbrain_r81 import is_fast_info as _nexus_is_fast_info, ask_fast as _nexus_ask_fast  # NEXUS_R81_WAITFIX
from nexus_fast_r85 import local_reply as _nexus_local_reply, ask_fast_r85 as _nexus_ask_fast_r85  # NEXUS_R85_QUICK_RESPONSE
from authority import current as approval_current, consume as approval_consume, button_confirm, cancel_all, request as approval_request
from ai_provider import ask as cloud_ask, ProviderError
from nexus_local_stt import ask_audio  # NEXUS_LOCAL_STT_R6
from agent import execute, recognize, recent_dialog, save_dialog, REQUIRES_APPROVAL
from planner import plan
from reminder_clock import worker as reminder_worker, listing as reminder_listing
from memory import recall as memory_recall
from learning20 import handle_command as _learning20_handle, STORE as _learning20_store
from skill_academy import handle as _skill_academy_handle, note_refusal as _skill_academy_note_refusal
from core10 import permission,set_permission,goals,graph_search,logs,get_events,ack_events,enqueue,log
from gestures import train, configure, match, SAFE_GESTURE_ACTIONS
import nexus_runtime_settings as nexus_settings  # NEXUS_R8_SETTINGS
from session import VoiceSession
VOICE_SESSION=VoiceSession()

app=Flask(__name__)
mutex=threading.Lock()
history=[]
pending={}
training_label=None
import secrets
from flask import abort
from runtime10 import start as start_runtime
start_runtime()
stop_reminders=threading.Event()
threading.Thread(target=reminder_worker,args=(stop_reminders,),daemon=True,name='reminder-clock').start()
state={'state':'READY','reply':'Привет! Я NEXUS AI.','display':'NEXUS AI READY','last_event':'','seq':0}
TOKEN=os.environ.get('NEXUS_DEVICE_TOKEN','')
if not TOKEN or TOKEN=='REPLACE_WITH_LONG_RANDOM_TOKEN':
    raise RuntimeError('Set a strong NEXUS_DEVICE_TOKEN environment variable before launch.')

# Simple Russian-to-ASCII transliteration until a Cyrillic font is embedded on M5.
CYR='абвгдеёжзийклмнопрстуфхцчшщъыьэюя'
LAT=['a','b','v','g','d','e','yo','zh','z','i','y','k','l','m','n','o','p','r','s','t','u','f','kh','ts','ch','sh','sch','','y','','e','yu','ya']
TABLE={ord(c):r for c,r in zip(CYR,LAT)}
TABLE.update({ord(c.upper()):r.capitalize() for c,r in zip(CYR,LAT)})
def ascii_display(value):
    # NEXUS_R32_UTF8: legacy name, but preserve UTF-8 Cyrillic for M5GFX
    # Python string is Unicode; Flask JSON will serialize it safely.
    return str(value or "")[:1500]

def publish(reply,status,event):
    with mutex:
        state.update(reply=reply[:1500],display=ascii_display(reply),state=status,last_event=event,seq=state['seq']+1)

def authorized():
    import hmac
    return hmac.compare_digest(request.headers.get('X-Device-Token',''),TOKEN)

HTML='''<!doctype html><html lang="ru"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>NEXUS AI</title>
<style>body{font-family:system-ui;background:#0c1523;color:#e5f8f2;margin:20px;max-width:560px}button,input{border-radius:12px;padding:12px;font:inherit}button{background:#53d9c3;border:0}input{width:94%;background:#14263a;color:#fff;border:1px solid #506575}#reply{white-space:pre-wrap;line-height:1.55}small{color:#a9bec9}</style>
<h2>◉ NEXUS AI</h2><p id="state">Connecting...</p><p id="reply"></p>
<form onsubmit="ask(event)"><input id="prompt" placeholder="Спроси NEXUS..."><button>Отправить</button></form>
<div id="approval"></div>
<h3>Локальная память и планировщик</h3>
<button onclick="showLocal('memory')">Воспоминания</button>
<button onclick="showLocal('reminders')">Назначенные напоминания</button>
<pre id="localInfo" style="white-space:pre-wrap"></pre>
<h3>Саморасширение · Extensions</h3>
<p><small>ИИ предлагает сценарий из безопасных инструментов; запуск только после одобрения.</small></p>
<input id="extIntent" placeholder="Какую функцию добавить?">
<button onclick="proposeExt()">Предложить модуль</button>
<div id="extList"></div>
<script>
async function proposeExt(){let intent=document.getElementById('extIntent').value;
let r=await fetch('/extensions/propose',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({intent})});
let j=await r.json();alert(j.error||j.reason||'Предложение создано; проверь перед включением');refreshExt();}
async function extDecide(id,approve){await fetch('/extensions/'+id+'/decide',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({approve})});refreshExt();}
async function extRun(id){let r=await fetch('/extensions/'+id+'/run',{method:'POST'});let j=await r.json();alert(JSON.stringify(j,null,2));}
async function refreshExt(){let r=await fetch('/extensions');let j=await r.json();let host=document.getElementById('extList');host.replaceChildren();
for(let x of j.items){let box=document.createElement('div');box.style.cssText='padding:10px;border:1px solid #39566a;margin:8px 0;border-radius:10px';
let pre=document.createElement('pre');pre.style.whiteSpace='pre-wrap';pre.textContent=x.name+' ['+x.status+']\n'+x.intent+'\n'+JSON.stringify(x.recipe,null,2)+(x.reason?'\n'+x.reason:'');box.append(pre);
function btn(text,fn){let b=document.createElement('button');b.textContent=text;b.onclick=fn;box.append(b)}
if(x.status==='pending'){btn('Одобрить',()=>extDecide(x.id,true));btn('Отклонить',()=>extDecide(x.id,false));}
if(x.status==='active')btn('Запустить',()=>extRun(x.id));host.append(box);}}
refreshExt();</script>
<h3>Обучение жестам</h3><p>Нажми «Обучать», затем удерживай B на M5StickC около секунды и выполни жест. Сделай несколько примеров.</p>
<select id="gesture"><option value="left">Влево</option><option value="right">Вправо</option><option value="up">Вверх</option><option value="down">Вниз</option><option value="circle">Круг</option><option value="custom">Свой жест</option></select>
<select id="gestureAction"><option value="battery">Заряд телефона</option><option value="notes_list">Список заметок</option><option value="reminders_list">Список напоминаний</option></select>
<button onclick="armGesture()">Обучать</button><button onclick="setGesture()">Назначить действие</button>
<p id="gestureStatus"></p><p><button onclick="speakLatest()">🔊 Прочитать ответ</button> <label><input id="auto" type="checkbox" style="width:auto"> Автоозвучка</label></p>
<small>Не закрывай страницу для автоозвучки. Голос с M5 записывается нажатием кнопки A.</small>
<script>
let lastSeq=-1,latest='';
async function showLocal(kind){let r=await fetch('/local/'+kind);let j=await r.json();document.getElementById('localInfo').textContent=j.text||j.error;}
function speakLatest(){if(!latest||!('speechSynthesis' in window))return;let u=new SpeechSynthesisUtterance(latest);u.lang='ru-RU';speechSynthesis.cancel();speechSynthesis.speak(u);}
async function ask(e){e.preventDefault();let v=document.getElementById('prompt').value;let r=await fetch('/ask',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({text:v})});let j=await r.json();document.getElementById('reply').textContent=j.reply||j.error;}
async function approve(id,yes){let r=await fetch('/approve',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({id:id,approve:yes})});let j=await r.json();document.getElementById('reply').textContent=j.reply||j.error;document.getElementById('approval').textContent='';}
async function armGesture(){let label=document.getElementById('gesture').value;let r=await fetch('/gesture/train/arm',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({label})});document.getElementById('gestureStatus').textContent=(await r.json()).message;}
async function setGesture(){let label=document.getElementById('gesture').value,action=document.getElementById('gestureAction').value;let r=await fetch('/gesture/config',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({label,action})});document.getElementById('gestureStatus').textContent=(await r.json()).message;}
async function poll(){try{let r=await fetch('/status');let j=await r.json();document.getElementById('state').textContent=j.state+' · '+j.last_event;let a=document.getElementById('approval');a.textContent='';if(j.pending){let p=document.createElement('p');p.textContent='Подтвердить: '+j.pending.label;a.appendChild(p);let y=document.createElement('button');y.textContent='Разрешить';y.onclick=()=>approve(j.pending.id,true);a.appendChild(y);let n=document.createElement('button');n.textContent='Отмена';n.onclick=()=>approve(j.pending.id,false);a.appendChild(n);}document.getElementById('reply').textContent=j.reply;
if(lastSeq>=0&&j.seq!==lastSeq&&j.state.startsWith('ANSWER')&&document.getElementById('auto').checked){latest=j.reply;speakLatest();}else latest=j.reply;lastSeq=j.seq;}catch(e){document.getElementById('state').textContent='Disconnected';}}
setInterval(poll,1200);poll();
</script></html>'''

def local_only():
    if request.remote_addr not in ('127.0.0.1','::1'):
        abort(403)
@app.get('/')
def home():
    local_only()
    return render_template_string(HTML)

@app.get('/health')
def health():
    if not authorized() and request.remote_addr not in ('127.0.0.1','::1'):
        return jsonify(error='unauthorized'),401
    from recovery import integrity
    from pathlib import Path
    db=Path(os.environ.get('NEXUS_DB_PATH',str(Path.home()/'.nexus_ai'/'agent.sqlite3')))
    # Database is initialized lazily by some modules; missing is not corruption.
    return jsonify(ok=(not db.exists()) or integrity(db),server='running')

@app.get('/status')
def get_status():
    # Mobile browser opens without token; local network read is limited to status only.
    if not authorized() and request.remote_addr not in ('127.0.0.1','::1'):return jsonify(error='unauthorized'),401
    with mutex:
        result=state.copy()
        result['voice_session']=VOICE_SESSION.status()
        result['approval']=approval_current()
        if request.remote_addr in ('127.0.0.1','::1') and pending:
            pid,act=next(iter(pending.items()))
            result['pending']={'id':pid,'label':act[0]+' '+act[1][:90]}
        result["settings"]=nexus_settings.read()
        return jsonify(result)

@app.post('/device/settings')
def nexus_settings_from_stick_r8():
    if not authorized():return jsonify(error='unauthorized'),401
    data=request.get_json(silent=True) or {}
    key=data.get('key')
    desired=data.get('value')
    ok,message=nexus_settings.update(key,desired)
    if ok:publish(message,'SETTINGS','device-setting')
    return jsonify(reply=message,settings=nexus_settings.read()),200 if ok else 409

@app.post('/event')
def event():
    if not authorized():return jsonify(error='unauthorized'),401
    j=request.get_json(silent=True) or {}
    event=j.get('event','')
    if event not in ('mode','confirm_button','stop','session_start','session_stop'):return jsonify(error='invalid event'),400
    if event=='session_start':
        VOICE_SESSION.start();publish('Разговор включён на 90 секунд','SESSION','session_start');return jsonify(ok=True)
    if event=='session_stop':
        VOICE_SESSION.stop();publish('Разговор завершён','READY','session_stop');return jsonify(ok=True)
    if event=='stop':
        from runtime10 import shutdown
        shutdown();cancel_all();publish('Остановлено пользователем','STOPPED','stop');return jsonify(ok=True)
    if event=='confirm_button':
        # NEXUS_R893_DIRECT_COMMANDS: legacy firmware may still send this event.
        return jsonify(state='disabled', result='Подтверждения NEXUS отключены')
    publish('Rezhim pereklyuchen', 'GESTURE',event)
    return jsonify(ok=True)

def process_text(text,event='text'):
    text=text.strip()[:3000]  # NEXUS_R86_PTT_10SEC
    if not text:return {'error':'Пустой запрос'},400
    # NEXUS 2.1: "научись" and interactive skills are routed locally BEFORE
    # a cloud request. Contact names/numbers, SMS drafts and confirmations
    # never enter Gemini dialogue history or the general tool planner.
    try:
        skill_reply=_skill_academy_handle(text, recent_dialog())
    except Exception:
        app.logger.exception('NEXUS Skill Academy failed')
        skill_reply="Ошибка обучения новому навыку. Ничего не отправлено."
    if skill_reply is not None:
        publish(skill_reply, 'SKILL', event)
        return {'reply':skill_reply,'provider':'NEXUS-Skill-Academy',
                'actions':[], 'pending':False},200
    # NEXUS 2.0 local learning: process explicit owner commands BEFORE any
    # cloud call. Do not save these commands to dialog history, otherwise
    # memories could leak into future Gemini history even when sharing is OFF.
    try:
        learning_command = _learning20_handle(text, recent_dialog())
    except Exception:
        app.logger.exception('NEXUS 2.0 local learning error')
        message = 'Ошибка локальной базы обучения NEXUS.'
        publish(message,'ERROR',event)
        return {'error':message},503
    if learning_command is not None:
        reply, ok = learning_command
        publish(reply, 'LEARN' if ok else 'ERROR', event)
        return {'reply':reply, 'provider':'NEXUS-2-Local',
                'actions':[], 'pending':False}, 200 if ok else 400
    settings_command=nexus_settings.handle_voice(text)
    if settings_command is not None:
        reply,ok=settings_command
        publish(reply,'SETTINGS' if ok else 'ERROR','settings')
        return {'reply':reply,'provider':'local-settings','actions':[], 'pending':False},200 if ok else 409
    # Permanent capability grants are explicitly scoped and require voice challenge + physical B hold.
    grant=__import__('re').fullmatch(r'(разрешаю навсегда|запрещаю навсегда)\s+(фонарик|уведомления|озвучивание)',text.lower())
    if grant:
        # NEXUS_R893_DIRECT_COMMANDS: internal permission grants no longer exist.
        _nexus_r893_message = ('Внутренние разрешения NEXUS отключены. '
                               'Системные разрешения изменяются в настройках Android.')
        publish(_nexus_r893_message, 'SETTINGS', event)
        return {'reply':_nexus_r893_message,'provider':'Local','actions':[], 'pending':False},200
    # Explicit extension activation requests create a voice+button approval.
    match=__import__('re').fullmatch(r'(?:активируй|установи) (?:расширение|навык)\s*(\d+)',text.lower())
    if match:
        # NEXUS_R893_DIRECT_COMMANDS: only an explicit numbered user command activates an extension.
        from extensions import listing, decide
        _nexus_r893_id = int(match.group(1))
        _nexus_r893_items = [x for x in listing()
                             if x['id'] == _nexus_r893_id and x['status'] == 'pending']
        if not _nexus_r893_items:
            return {'error':'Нет ожидающего расширения с таким ID'},404
        _nexus_r893_result, _nexus_r893_status = decide(_nexus_r893_id, True)
        if isinstance(_nexus_r893_result, dict):
            _nexus_r893_message = str(_nexus_r893_result.get('reply') or
                                     _nexus_r893_result.get('message') or
                                     _nexus_r893_result)[:450]
        else:
            _nexus_r893_message = str(_nexus_r893_result)[:450]
        publish(_nexus_r893_message,
                'ACTION' if _nexus_r893_status == 200 else 'ERROR', event)
        return {'reply':_nexus_r893_message, 'provider':'Local', 'pending':False,
                 'actions':[{'name':'extension_activate','result':_nexus_r893_message}]}, _nexus_r893_status
    if text.lower() in ('стоп','остановись','отмена'):
        cancel_all();publish('Остановлено','STOPPED',event);return {'reply':'Остановлено'},200
    # The owner's exact correction to a read-only question can be answered
    # locally without an API call. Never apply a cached correction to tool
    # or device-changing requests.
    if _nexus_is_fast_info(text):
        corrected = _learning20_store.corrected_answer(text)
        if corrected:
            reply = 'По твоему исправлению: ' + corrected[:1000]
            publish(reply,'ANSWER via NEXUS 2.0 Memory',event)
            return {'reply':reply,'provider':'NEXUS-2-Local',
                    'actions':[], 'pending':False},200
    # NEXUS_R85_QUICK_RESPONSE: strictly read-only local responses; never run
    # permission, shell, plugin, or device-changing commands on this path.
    _nexus_local_answer=_nexus_local_reply(text)
    if _nexus_local_answer is not None:
        save_dialog('user', text)
        save_dialog('assistant', _nexus_local_answer)
        publish(_nexus_local_answer, 'ANSWER via Local', event)
        app.logger.warning('NEXUS R85 TIME: route=local elapsed=0s')
        return {'reply':_nexus_local_answer, 'provider':'Local',
                'actions':[], 'pending':False},200
    publish('Обрабатываю запрос...','THINKING',event)
    # NEXUS_R81_WAITFIX: fast informational answers skip the giant tool catalog.
    # Device actions/permission changes always use the original gated planner.
    import time as _nexus_clock
    _nexus_started=_nexus_clock.monotonic()
    _nexus_route='fast' if _nexus_is_fast_info(text) else 'planner'
    try:
        if _nexus_route == 'fast':
            _nexus_answer=_nexus_ask_fast_r85(
                text, recent_dialog(),
                on_partial=lambda partial: publish(partial, 'THINKING', 'fast-stream'))
            response={'reply':_nexus_answer,'provider':'Gemini-fast','actions':[],'pending':[]}
        else:
            response=plan(text,recent_dialog(),voice_mode=(event=='voice'))
    except ProviderError as e:
        app.logger.warning('NEXUS BRAIN TIME: route=%s elapsed=%.2fs provider_error=%s',
                           _nexus_route,_nexus_clock.monotonic()-_nexus_started,type(e).__name__)
        publish('Ошибка облачного ИИ','ERROR',event)
        return {'error':str(e)[:300]},503
    except Exception:
        app.logger.exception('NEXUS BRAIN ERROR: route=%s elapsed=%.2fs',
                             _nexus_route,_nexus_clock.monotonic()-_nexus_started)
        publish('Ошибка обработки запроса','ERROR',event)
        return {'error':'Не удалось обработать запрос. См. журнал NEXUS.'},500
    app.logger.warning('NEXUS BRAIN TIME: route=%s elapsed=%.2fs',
                       _nexus_route,_nexus_clock.monotonic()-_nexus_started)
    # The bounded planner already issued a single-use approval challenge.
    answer=response['reply']
    save_dialog('user',text);save_dialog('assistant',answer)
    try:
        _skill_academy_note_refusal(text,answer)
    except Exception:
        app.logger.warning('NEXUS Skill Academy: could not remember unsupported intent')
    publish(answer,'CONFIRM' if response.get('pending') else 'ANSWER via '+response['provider'],event)
    # Optional voice output on Android; opt-in only and never synthesize an unapproved tool action.
    if os.environ.get('NEXUS_AUTO_TTS')=='1' and not response.get('pending'):
        threading.Thread(target=execute,args=('speak',answer[:350]),daemon=True).start()
    return {'reply':answer,'provider':response['provider'],'actions':response['actions'],'pending':bool(response.get('pending'))},200

@app.get('/local/<kind>')
def local_info(kind):
    local_only()
    if kind=='memory':return jsonify(text=memory_recall())
    if kind=='learning':
        records = _learning20_store.list_facts(12)
        return jsonify(text=('\n'.join(f"{x['id']}. {x['text']}" for x in records)
                             if records else 'Новых воспоминаний пока нет.'))
    if kind=='reminders':return jsonify(text=reminder_listing())
    return jsonify(error='unknown'),404

@app.post('/ask')
def ask():
    local_only()
    text=str((request.get_json(silent=True) or {}).get('text',''))
    result,status=process_text(text)
    return jsonify(result),status

@app.post('/approve')
def approve():
    local_only()
    j=request.get_json(silent=True) or {}
    pid=j.get('id','')
    with mutex:task=pending.pop(pid,None)
    if not task:return jsonify(error='Заявка не найдена или уже обработана'),404
    if __import__('time').monotonic()-task[2]>120:return jsonify(error='Подтверждение просрочено'),410
    if j.get('approve') is not True:
        publish('Действие отменено','READY','confirm')
        return jsonify(reply='Отменено')
    answer=execute(*task[:2])
    publish(answer,'ACTION','confirm')
    return jsonify(reply=answer)


# NEXUS_VOICE_FAST_R5: strictly informational voice requests can use the already
# computed multimodal Gemini answer instead of a second agent-model call.
# Never bypass the tool planner for phone actions, memory edits, permissions,
# reminders, or ambiguous / unrecognized speech.
def _nexus_fast_chat(transcript: str, answer: str) -> bool:
    text = ' '.join(transcript.casefold().split())
    if not text or len(text) > 260:
        return False
    if not answer or answer.strip().upper().startswith('ACTION_REQUIRED'):
        return False
    fast_starts = (
        'привет', 'здравствуй', 'добрый день', 'добрый вечер',
        'как дела', 'что такое ', 'что означает ', 'объясни ',
        'почему ', 'как работает ', 'как устроен ',
        'кто такой ', 'кто такая ', 'кто такие ',
        'чем отличается ', 'расскажи о ', 'расскажи про ',
        'для чего ', 'сколько будет ', 'в чем разница ',
        'в чём разница ', 'переведи ', 'как называется ',
    )
    if not text.startswith(fast_starts):
        return False
    # A conservative veto. This is not a general intent classifier: if in doubt
    # use the existing gated planner, never silently execute anything.
    veto = (
        'разреш', 'код ', 'нажми', 'подтвер', 'отмен', 'стоп', 'запиши',
        'запомни', 'памят', 'напомн', 'уведомлен', 'фонар', 'батаре',
        'заряд телефон', 'включи', 'выключи', 'установи', 'отправ',
        'создай', 'удали', 'сохрани', 'запусти', 'открой', 'покажи',
        'сделай', 'перезагруз', 'сценари', 'плагин', 'модул',
        'задач', 'цель', 'заметк', 'микрофон', 'настрой',
    )
    return not any(word in text for word in veto)


@app.post('/voice')
def voice():
    if not authorized():return jsonify(error='unauthorized'),401
    if request.content_type!='audio/wav':return jsonify(error='WAV required'),415
    import time as _nexus_r7_time
    _received_at=_nexus_r7_time.monotonic()
    wav=request.get_data(cache=False)
    app.logger.warning('NEXUS R7 RECEIVE: WAV %d bytes, read %.3fs, device_capture=%sms',
                       len(wav),_nexus_r7_time.monotonic()-_received_at,
                       request.headers.get('X-Nexus-Capture-Ms','n/a')[:16])
    return _nexus_voice_process_r7(wav)

def _nexus_voice_process_r7(wav):
    VOICE_SESSION.consume()  # Advances an active session; A-button push-to-talk also works without it.
    if len(wav)<44 or len(wav)>350000 or wav[:4]!=b'RIFF' or wav[8:12]!=b'WAVE':
        return jsonify(error='invalid WAV'),400
    with mutex:snapshot=recent_dialog()
    publish('Распознаю голос...','THINKING','voice')
    import time as _nexus_time
    _nexus_voice_at = _nexus_time.monotonic()
    try:transcript,answer=ask_audio(wav,snapshot)
    except ProviderError as e:
        publish(str(e)[:200],'ERROR','voice');return jsonify(error=str(e)),503
    _nexus_asr_s = _nexus_time.monotonic() - _nexus_voice_at
    # All transcriptions pass through the Gemini tool planner.
    if transcript:
        # NEXUS_R893_DIRECT_COMMANDS: no more voice-code interception; transcript goes to process_text.
        # R5 FAST: the first Gemini request already answered simple questions.
        # Action requests, permission confirmations, and uncertain cases still
        # pass through the unchanged bounded tool planner.
        if _nexus_fast_chat(transcript, answer):
            save_dialog('user', transcript)
            save_dialog('assistant', answer)
            publish(answer, 'ANSWER via Gemini', 'voice-fast')
            app.logger.warning('NEXUS VOICE TIMING: first Gemini %.2fs; second call skipped', _nexus_asr_s)
            return jsonify(display=ascii_display(answer), reply=answer,
                           transcript=transcript, provider='Gemini-fast',
                           actions=[], pending=False), 200
        _nexus_planner_at = _nexus_time.monotonic()
        result,status=process_text(transcript,'voice')
        app.logger.warning('NEXUS VOICE TIMING: first Gemini %.2fs; agent %.2fs',
                           _nexus_asr_s, _nexus_time.monotonic() - _nexus_planner_at)
        result['transcript']=transcript
        result['display']=ascii_display(result.get('reply',result.get('error','')))
        return jsonify(result),status
    save_dialog('user',transcript or '[аудиозапрос]')
    save_dialog('assistant',answer)
    publish(answer,'ANSWER via Gemini','voice')
    return jsonify(display=ascii_display(answer),reply=answer,transcript=transcript)





# NEXUS_SMART_VOICE_R7
# Experimental streamed PCM upload: captures while packets arrive; decode after speech ends.
# Keep the original /voice WAV route unchanged for the default transport.
@app.post('/voice/pcm-stream')
def nexus_voice_pcm_stream_r7():
    import io
    import time
    import wave
    if not authorized():
        return jsonify(error='unauthorized'), 401
    if request.mimetype != 'application/octet-stream':
        return jsonify(error='PCM16 required'), 415
    if not request.environ.get('wsgi.input_terminated') and request.content_length is None:
        return jsonify(error='streaming unsupported by this server'), 501
    t0=time.monotonic()
    pcm=bytearray()
    # The Flask/Werkzeug streaming input has to end with a 0-length HTTP chunk.
    # Data is capped even when the peer supplies no Content-Length.
    try:
        while True:
            block=request.stream.read(4096)
            if not block:break
            pcm.extend(block)
            if len(pcm)>128000:
                return jsonify(error='too much PCM'), 413
            if time.monotonic()-t0>12:
                return jsonify(error='capture too long'), 408
    except (OSError,ValueError,TimeoutError):
        return jsonify(error='incomplete PCM stream'), 400
    if len(pcm)<6400 or len(pcm)%2:
        return jsonify(error='invalid PCM length'), 400
    buf=io.BytesIO()
    with wave.open(buf,'wb') as w:
        w.setnchannels(1);w.setsampwidth(2);w.setframerate(16000)
        w.writeframes(pcm)
    app.logger.warning('NEXUS R7 STREAM: %d PCM bytes in %.2fs',len(pcm),time.monotonic()-t0)
    return _nexus_voice_process_r7(buf.getvalue())



# NEXUS_R87_PTT_REGISTER (installed by install_nexus_ptt_r87.py)
from nexus_ptt_r87 import register_ptt_stream as _nexus_register_ptt_r87
_nexus_register_ptt_r87(
    app, authorized=authorized, publish=publish, process_text=process_text,
    approval_current=approval_current, approval_consume=approval_consume,
    voice_session=VOICE_SESSION,
)
# END NEXUS_R87_PTT_REGISTER

@app.post('/gesture/train/arm')
def gesture_arm():
    local_only()
    global training_label
    label=(request.get_json(silent=True) or {}).get('label','')
    if label not in ('circle','left','right','up','down','custom'):return jsonify(error='invalid gesture'),400
    training_label=label
    return jsonify(message='Сбор примера для '+label+' включён. Удерживай B и выполни движение.')

@app.post('/gesture/config')
def gesture_config():
    local_only()
    data=request.get_json(silent=True) or {}
    ok=configure(data.get('label',''),data.get('action',''))
    return jsonify(message='Назначено' if ok else 'Ошибка выбора'),200 if ok else 400

@app.post('/gesture/sample')
def gesture_sample():
    if not authorized():return jsonify(error='unauthorized'),401
    global training_label
    data=request.get_json(silent=True) or {}
    features=data.get('features')
    if training_label:
        label=training_label
        if not train(label,features):return jsonify(error='invalid sample'),400
        training_label=None
        publish('Пример жеста сохранён: '+label,'GESTURE','train')
        return jsonify(trained=label)
    result=match(features)
    if result and result.get('action') in SAFE_GESTURE_ACTIONS:
        reply=execute(result['action'])
        publish(reply,'GESTURE','recognized')
    return jsonify(result=result)


# Extension authoring endpoints: Android localhost only. Generated code is never executed.
from extensions import propose as propose_extension, listing as list_extensions, decide as decide_extension, run as run_extension
@app.get('/extensions')
def extension_list():
    local_only()
    return jsonify(items=list_extensions())
@app.post('/extensions/propose')
def extension_propose():
    local_only()
    intent=str((request.get_json(silent=True) or {}).get('intent',''))
    try:return jsonify(propose_extension(intent))
    except (ProviderError,ValueError) as e:return jsonify(error=str(e)[:300]),400
@app.post('/extensions/<int:identifier>/decide')
def extension_decide(identifier):
    local_only()
    if request.is_json is False:return jsonify(error='JSON required'),415
    approval=(request.get_json(silent=True) or {}).get('approve')
    if not isinstance(approval,bool):return jsonify(error='Boolean approve required'),400
    try:result,code=decide_extension(identifier,approval)
    except (ValueError,TypeError) as e:return jsonify(error=str(e)),400
    return jsonify(result),code
@app.post('/extensions/<int:identifier>/run')
def extension_run(identifier):
    local_only()
    try:result,code=run_extension(identifier)
    except (ValueError,TypeError) as e:return jsonify(error=str(e)),400
    return jsonify(result),code



@app.post('/authority/extension/<int:identifier>/request')
def ext_activate_request(identifier):
    local_only()
    from extensions import listing
    if not any(x['id']==identifier and x['status']=='pending' for x in listing()):return jsonify(error='not pending'),404
    p=approval_request('extension_activate',str(identifier))
    publish('Разрешаю '+p['code']+'; затем удерживать B 3s','CONFIRM','extension')
    return jsonify(code=p['code'],level=p['level'])


@app.get('/v1/grants')
def v1_grants():
    local_only()
    return jsonify({k:permission(k) for k in ('torch_on','torch_off','notify','speak','gesture_automation')})
@app.get('/v1/goals')
def v1_goals():
    local_only();return jsonify(goals=goals())
@app.get('/v1/graph')
def v1_graph():
    local_only();return jsonify(nodes=graph_search(request.args.get('q','')[:80]))
@app.get('/v1/logs')
def v1_logs():
    local_only();return jsonify(items=logs())
@app.get('/v1/drafts')
def v1_drafts():
    local_only();from evolution10 import drafts;return jsonify(items=drafts())
@app.get('/v1/events')
def v1_events():
    if not authorized():return jsonify(error='unauthorized'),401
    return jsonify(items=get_events())
@app.post('/v1/events/ack')
def v1_ack():
    if not authorized():return jsonify(error='unauthorized'),401
    ack_events((request.get_json(silent=True) or {}).get('ids',[]))
    return jsonify(ok=True)
@app.post('/v1/stop')
def v1_stop():
    if not authorized():return jsonify(error='unauthorized'),401
    from runtime10 import shutdown
    shutdown();cancel_all();publish('Autonomous tasks stopped','STOPPED','stop')
    return jsonify(ok=True)



# NEXUS_BUZZER_R4: local offline eSpeak synthesized PCM
@app.get('/tts/pcm')
def nexus_buzzer_pcm():
    if not nexus_settings.read()['tts']:
        return '', 204
    # Use precisely the same device-token authentication as /voice and /status.
    if not authorized():
        return jsonify(error='unauthorized'), 401
    try:
        requested = int(request.args.get('seq','-1'))
    except (TypeError, ValueError):
        return jsonify(error='invalid sequence'), 400
    with mutex:
        seq = state['seq']
        status = state['state']
        message = state['reply']
    if requested != seq or not status.startswith('ANSWER'):
        return jsonify(error='no finished answer with that sequence'), 409
    from nexus_buzzer_tts import synthesize, BuzzerTTSError
    try:
        pcm = synthesize(message)
    except BuzzerTTSError as exc:
        app.logger.warning('NEXUS buzzer TTS unavailable: %s', exc)
        return jsonify(error=str(exc)), 503
    from flask import Response
    return Response(pcm, content_type='application/octet-stream', headers={
        'Cache-Control': 'no-store',
        'X-NEXUS-Audio': 'pcm-u8-mono-8000',
        'Content-Length': str(len(pcm))
    })

from nexus_ptt_r86 import install_routes as _nexus_r86_install  # NEXUS_R86_PTT_10SEC
_nexus_r86_install(app, authorized, publish, process_text, _nexus_voice_process_r7,
                   approval_current, approval_consume, VOICE_SESSION)

if __name__=='__main__':app.run(host='0.0.0.0',port=8765,threaded=True)
