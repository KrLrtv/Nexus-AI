#!/usr/bin/env python3
"""NEXUS AI 1.0 Termux text manager. No external Python packages required."""
import os,sys,shlex,time,signal,subprocess,urllib.request,urllib.error,json,getpass,re
from pathlib import Path
HOME=Path.home(); CFG=HOME/'.config/nexus'; ENV=CFG/'secrets.env'; GEM=CFG/'gemini.env'
TARGET=HOME/'nexus-ai'; LOG=HOME/'nexus-ai.log'; PID=CFG/'managed.pid'
URL='http://127.0.0.1:8765/status'
MODEL_DEFAULT='gemini-3.1-flash-lite'
FIELDS=('NEXUS_DEVICE_TOKEN','GEMINI_API_KEY','GEMINI_MODEL','OPENROUTER_API_KEY')
def config():
    # Parse only export X='...' statements. Do not source arbitrary files in Python.
    result={}
    for p in (ENV,GEM):
        if not p.is_file():continue
        for line in p.read_text(errors='replace').splitlines():
            line=line.strip()
            if not line.startswith('export '):continue
            try: chunks=shlex.split(line[7:],posix=True)
            except ValueError:continue
            if len(chunks)==1 and '=' in chunks[0]:
                k,v=chunks[0].split('=',1)
                if k in FIELDS and (p==ENV or (k=='GEMINI_API_KEY' and not result.get(k))):result[k]=v
    return result
def save(**values):
    CFG.mkdir(parents=True,exist_ok=True);CFG.chmod(0o700)
    current=config();current.update(values)
    # Preserve unknown config entries such as runtime flags, without sourcing them.
    extras=[]
    if ENV.exists():
        for line in ENV.read_text(errors='replace').splitlines():
            if not any(line.startswith('export '+k+'=') for k in FIELDS):extras.append(line)
    temp=CFG/'secrets.env.tmp'
    with temp.open('w') as f:
        for k in FIELDS:
            if k in current: f.write('export '+k+'='+shlex.quote(current[k])+'\n')
        for line in extras:f.write(line+'\n')
    os.chmod(temp,0o600);os.replace(temp,ENV)
def request_status():
    try:
        with urllib.request.urlopen(URL,timeout=1.5) as r:return r.status
    except Exception:return 0
def managed_pid():
    try:
        pid=int(PID.read_text().strip())
        os.kill(pid,0)
        # On Linux verify PID hasn't been recycled into another process.
        cmd=(Path('/proc')/str(pid)/'cmdline').read_bytes().replace(b'\0',b' ').decode(errors='ignore')
        if 'supervisor.py' in cmd and 'python' in cmd:return pid
    except (ValueError,FileNotFoundError,ProcessLookupError,PermissionError):pass
    return None
def start():
    if request_status():print('⚠️ Порт 8765 уже обслуживает сервер. Второй экземпляр не запускаю.');return
    if managed_pid():print('Supervisor уже запущен, проверяй журнал.');return
    if not (TARGET/'android_bridge/supervisor.py').exists():print('❌ Файлы NEXUS не найдены');return
    env=os.environ.copy();env.update(config())
    if not env.get('NEXUS_DEVICE_TOKEN'):
        print('❌ Отсутствует NEXUS_DEVICE_TOKEN. Повтори установку.');return
    env['GEMINI_MODEL']=env.get('GEMINI_MODEL') or MODEL_DEFAULT
    with LOG.open('ab') as f:
        proc=subprocess.Popen([sys.executable,'supervisor.py'],cwd=TARGET/'android_bridge',env=env,
                              stdin=subprocess.DEVNULL,stdout=f,stderr=subprocess.STDOUT,start_new_session=True)
    PID.write_text(str(proc.pid)+'\n');PID.chmod(0o600)
    for _ in range(12):
        if request_status():break
        if proc.poll() is not None:break
        time.sleep(.5)
    if request_status():print('✅ NEXUS работает: http://127.0.0.1:8765')
    else:print('⚠️ Нет ответа. Посмотри журнал: nexus-logs')
def stop():
    pid=managed_pid()
    if not pid:
        if request_status():print('⚠️ Сервер работает, но запущен не через новое меню. Останови старый Termux-сеанс через CTRL+C.');
        else:print('NEXUS не запущен.')
        return
    try:os.kill(pid,signal.SIGTERM)
    except ProcessLookupError:pass
    for _ in range(30):
        if not managed_pid():break
        time.sleep(.2)
    if not managed_pid():PID.unlink(missing_ok=True);print('✅ Остановлен')
    else:print('⚠️ Остановка ещё выполняется. Проверь статус.')
def status():
    print('Сервер:', '✅ HTTP '+str(request_status()) if request_status() else '⚪ не отвечает')
    print('Supervisor:',managed_pid() or 'не управляется этим меню')
    cfg=config();print('Gemini:', 'ключ задан' if cfg.get('GEMINI_API_KEY') else 'нет ключа')
    print('Модель:',cfg.get('GEMINI_MODEL') or MODEL_DEFAULT)
    print('OpenRouter:', 'ключ задан' if cfg.get('OPENROUTER_API_KEY') else 'не настроен')
def test():
    cfg=config();key=cfg.get('GEMINI_API_KEY','').strip();model=cfg.get('GEMINI_MODEL') or MODEL_DEFAULT
    if not key:print('❌ Сначала добавь Gemini API-ключ.');return
    print('Проверка генерации:',model)
    req=urllib.request.Request('https://generativelanguage.googleapis.com/v1beta/models/'+model+':generateContent',
       json.dumps({'contents':[{'parts':[{'text':'Ответь одним словом: Привет'}]}]}).encode(),
       {'Content-Type':'application/json','x-goog-api-key':key},method='POST')
    try:
        with urllib.request.urlopen(req,timeout=30) as r:
            data=json.load(r)
        answer=''.join(p.get('text','') for p in data['candidates'][0]['content']['parts'])
        print('✅ Gemini ответил:',answer[:240])
    except urllib.error.HTTPError as e:
        try:detail=json.load(e).get('error',{}).get('message','')
        except Exception:detail='нет подробностей'
        detail=re.sub(r'AIza[A-Za-z0-9_-]+','[KEY HIDDEN]',detail)
        print('❌ HTTP',e.code,detail[:280])
    except Exception as e:print('❌ Сбой соединения:',type(e).__name__,str(e)[:100])
def change_key(provider='gemini'):
    keyname='GEMINI_API_KEY' if provider=='gemini' else 'OPENROUTER_API_KEY'
    print('Ввод ключа (можно видеть символы через режим 2).')
    print('1 — скрытый ввод (рекомендуется)\n2 — видимый ввод\n0 — отмена')
    opt=input('Выбор: ').strip()
    if opt=='0':return
    if opt=='1':key=getpass.getpass('Вставь API-ключ: ')
    elif opt=='2':
        print('⚠️ Ключ будет виден на экране. Не делай скриншоты.')
        key=input('Вставь API-ключ: ')
    else:return
    key=key.strip()
    if not key:print('Пустой ввод: прежний ключ сохранён.');return
    if provider=='gemini' and (not key.startswith('AIza') or len(key)<30):
        print('⚠️ Ключ не похож на Gemini API-ключ. Не сохраняю.');return
    save(**{keyname:key});print('✅ Ключ сохранён, значение не показываю. Для сервера требуется перезапуск.')
def change_model():
    models=['gemini-3.1-flash-lite','gemini-flash-lite-latest','gemini-2.5-flash','gemini-2.5-flash-lite']
    for i,m in enumerate(models,1):print(i,m)
    print('5 Другое имя модели\n0 Назад')
    opt=input('Номер: ').strip()
    if opt=='0':return
    if opt in ('1','2','3','4'):model=models[int(opt)-1]
    elif opt=='5':model=input('Имя модели: ').strip()
    else:return
    if not re.fullmatch(r'[a-zA-Z0-9._-]{2,100}',model):print('❌ Неверное имя модели');return
    save(GEMINI_MODEL=model)
    print('✅ Модель сохранена. Для сервера требуется перезапуск.')
def logs():
    if not LOG.exists():print('Журнал пока отсутствует');return
    lines=LOG.read_text(errors='replace').splitlines()[-35:]
    for line in lines:
        line=re.sub(r'AIza[A-Za-z0-9_-]+','[KEY HIDDEN]',line)
        print(line[:500])
def show_token():
    tok=config().get('NEXUS_DEVICE_TOKEN')
    if tok:
        print('⚠️ Токен будет виден. Не отправляй скриншот.')
        if input('Показать? [y/N]: ').strip().lower()=='y':print(tok)
    else:print('Токен не найден')
# NEXUS_R88_VOICE_MENU
def voice_clarity_menu():
    tts = HOME / 'nexus-ai' / 'android_bridge' / 'nexus_buzzer_tts.py'
    mode_file = CFG / 'voice_clarity_mode'
    if not tts.is_file() or 'NEXUS_R88_VOICE_CLARITY' not in tts.read_text(encoding='utf-8', errors='replace'):
        print('Сначала установи NEXUS_R88_VoiceClarity_Termux.py install')
        return
    current = mode_file.read_text(encoding='utf-8').strip() if mode_file.exists() else 'clear'
    print('Текущий режим:', 'Original' if current == 'original' else 'Voice Clarity')
    print('1 — Voice Clarity (без срезания пиков)')
    print('2 — Original (прежний голос)')
    print('0 — Назад')
    choice = input('Выбор: ').strip()
    if choice == '0': return
    if choice not in ('1', '2'):
        print('Неверный выбор'); return
    value = 'clear' if choice == '1' else 'original'
    CFG.mkdir(parents=True, exist_ok=True)
    temp = mode_file.with_name(mode_file.name + '.tmp')
    temp.write_text(value + '\n', encoding='utf-8')
    os.chmod(temp, 0o600)
    os.replace(temp, mode_file)
    print('Режим сохранён:', 'Voice Clarity' if value == 'clear' else 'Original')
    print('Применится к следующей озвучке; сервер перезапускать не требуется.')


# NEXUS_R89_SPEED

def nexus_speed_menu():
    """Select latency policy for subsequent requests."""
    setting = CFG / 'speed_mode'
    mode = setting.read_text().strip() if setting.exists() else 'balanced'
    print('Текущий режим:', 'Максимальная скорость' if mode == 'fast' else 'Сбалансированный')
    print('1 — Максимальная скорость (короче ожидание и ответы)')
    print('2 — Сбалансированный (дольше ожидание, больше попыток)')
    print('0 — Назад')
    choice = input('Выбор: ').strip()
    if choice not in ('1', '2'):
        return
    setting.parent.mkdir(parents=True, exist_ok=True)
    temp = setting.with_suffix('.tmp')
    temp.write_text(('fast' if choice == '1' else 'balanced') + '\n')
    os.chmod(temp, 0o600)
    os.replace(temp, setting)
    print('Режим сохранён. Новые запросы используют выбранные ограничения.')


def menu():
    """One stable screen: menu on top, action feedback below it."""
    while True:
        # ANSI erase + cursor home. Termux supports this without an external clear command.
        print('\033[2J\033[H',end='',flush=True)
        print('═'*38+'\n      NEXUS AI 1.0 · TERMUX\n'+'═'*38)
        print('1  ▶ Запустить сервер\n2  ■ Остановить сервер\n3  ↻ Перезапустить\n4  ● Состояние')
        print('5  ◇ Проверить Gemini\n6  🔑 Изменить Gemini API-ключ\n7  🔑 Изменить OpenRouter ключ')
        print('8  ⚙ Выбрать модель\n9  ≡ Последние ошибки\n10 ◇ Показать токен M5StickC\n11 ♫ Настройки голоса\n12 ⚡ Скорость NEXUS\n0  Выход')
        print('─'*38)
        try:choice=input('Выбор: ').strip()
        except (KeyboardInterrupt,EOFError):print();return
        if choice=='0':return
        actions={'1':start,'2':stop,'3':lambda:(stop(),start()),'4':status,'5':test,'6':lambda:change_key('gemini'),
                 '7':lambda:change_key('openrouter'),'8':change_model,'9':logs,'10':show_token,'11':voice_clarity_menu,'12':nexus_speed_menu}
        print('\n'+'─'*10+' РЕЗУЛЬТАТ '+'─'*17,flush=True)
        if choice in actions:
            try:actions[choice]()
            except (KeyboardInterrupt,EOFError):print('\nОтменено')
            except Exception as e:print('Ошибка меню:',type(e).__name__,str(e)[:120])
        else:print('Выбери номер из меню.')
        # Feedback stays below menu until the user is ready. Next screen redraw clears it.
        try:input('\n↵ Enter — вернуться в меню...')
        except (KeyboardInterrupt,EOFError):print();return
if __name__=='__main__':
    aliases={'start':start,'stop':stop,'restart':lambda:(stop(),start()),'status':status,'test':test,'key':lambda:change_key('gemini'),
             'model':change_model,'logs':logs}
    if len(sys.argv)>1:
        cmd=sys.argv[1]
        if cmd in aliases:aliases[cmd]()
        else:print('Команды: nexus start|stop|restart|status|test|key|model|logs')
    else:menu()
