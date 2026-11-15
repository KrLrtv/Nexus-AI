#!/usr/bin/env python3
"""NEXUS_TERMUX_TUI_V1 - separate, dependency-free Termux interface.

The original nexusctl.py is intentionally never modified by this UI.
"""
from __future__ import annotations
import contextlib
import io
import os
import re
import select
import subprocess
import shutil
import socket
import sys
import termios
import time
import tty
import unicodedata
from datetime import datetime
from pathlib import Path

ROOT = Path.home() / 'nexus-ai'
sys.path.insert(0, str(ROOT))
import nexusctl as engine
import nexus_device as m5
import nexus_update as releases
sys.path.insert(0, str(ROOT / 'android_bridge'))
from learning20 import STORE as learning_store

CFG = Path.home() / '.config' / 'nexus'
LOG = Path.home() / 'nexus-ai.log'
ACCENT, GREEN, RED, MUTED, GOLD = '38;2;99;214;232', '38;2;92;208;161', '38;2;255;124;134', '38;2;147;159;177', '38;2;248;197;111'
ANSI = sys.stdout.isatty() and os.environ.get('TERM') != 'dumb' and not os.environ.get('NO_COLOR')


def c(text, color=ACCENT, bold=False):
    if not ANSI: return str(text)
    return f'\x1b[{"1;" if bold else ""}{color}m{text}\x1b[0m'


def width(s):
    s = re.sub(r'\x1b\[[0-9;]*m', '', str(s))
    return sum(2 if unicodedata.east_asian_width(ch) in ('F', 'W') else 0 if unicodedata.combining(ch) else 1 for ch in s)


def crop(s, n):
    s = str(s).replace('\r', ' ').replace('\n', ' ')
    if width(s) <= n: return s
    out = ''
    for ch in s:
        if width(out + ch) > max(0, n-1): break
        out += ch
    return out + '…'


def sanitise(s):
    s = str(s)
    s = re.sub(r'AIza[A-Za-z0-9_\-]+', '[СКРЫТ КЛЮЧ]', s)
    s = re.sub(r'(sk-or-v1-[A-Za-z0-9_\-]+)', '[СКРЫТ КЛЮЧ]', s)
    s = re.sub(r'(?i)(Authorization:\s*Bearer\s+)\S+', r'\1[СКРЫТО]', s)
    s = re.sub(r'(?i)((?:GEMINI_API_KEY|OPENROUTER_API_KEY|NEXUS_DEVICE_TOKEN|X-Device-Token)\s*[:=]\s*)\S+', r'\1[СКРЫТО]', s)
    return ''.join(ch if (ch.isprintable() or ch in '\n\t') else ' ' for ch in s)


def read_mode(filename, default, allowed):
    try:
        data = (CFG / filename).read_text(encoding='utf-8').strip()
        return data if data in allowed else default
    except OSError:
        return default


def write_mode(filename, mode):
    CFG.mkdir(parents=True, exist_ok=True)
    tmp = CFG / (filename + '.tuitmp')
    tmp.write_text(mode + '\n', encoding='utf-8')
    tmp.chmod(0o600)
    os.replace(tmp, CFG / filename)


def available_voice():
    p = ROOT / 'android_bridge' / 'nexus_buzzer_tts.py'
    try: return 'NEXUS_R88_VOICE_CLARITY' in p.read_text(encoding='utf-8')
    except OSError: return False


def available_speed():
    for name in ('ai_provider.py','planner.py'):
        try:
            if 'NEXUS_R89_SPEED' not in (ROOT/'android_bridge'/name).read_text(encoding='utf-8'):
                return False
        except OSError: return False
    return True


def check_stt():
    mode = read_mode('stt_mode', '?', {'sherpa','gemini','cloud','?'})
    with socket.socket() as sk:
        sk.settimeout(0.25)
        try:
            sk.connect(('127.0.0.1', 8766))
            ready = True
        except OSError:
            ready = False
    return mode, ready


def keypress(timeout=2.8):
    if not sys.stdin.isatty():
        try: return input('Выбор: ').strip().lower()
        except (EOFError, KeyboardInterrupt): return 'q'
    fd = sys.stdin.fileno()
    original = termios.tcgetattr(fd)
    try:
        tty.setcbreak(fd)
        if not select.select([fd], [], [], timeout)[0]: return None
        k = os.read(fd, 1)
        if k == b'\x03': return 'q'
        if k == b'\x1b':
            if not select.select([fd], [], [], .08)[0]: return 'back'
            p = os.read(fd, 1)
            if p in (b'[', b'O'):
                if not select.select([fd], [], [], .08)[0]: return 'back'
                code = os.read(fd, 1)
                return {b'A':'up',b'B':'down',b'C':'enter',b'D':'back'}.get(code,'back')
            return 'back'
        if k in (b'\x7f', b'\x08'): return 'back'
        if k in (b'\n', b'\r'): return 'enter'
        try: return k.decode('utf-8').lower()
        except UnicodeDecodeError: return None
    finally:
        termios.tcsetattr(fd, termios.TCSADRAIN, original)


class Tee(io.TextIOBase):
    def __init__(self, real): self.real, self.capture = real, io.StringIO()
    def write(self, s):
        self.capture.write(s); self.real.write(s); return len(s)
    def flush(self): self.real.flush()
    def getvalue(self): return self.capture.getvalue()


class ControlCenter:
    def __init__(self):
        self.page = 'home'
        self.selected = {'home':0,'settings':0,'m5':0,'learn':0}
        self.learning_filter = ''
        self.flash = ''
        self.flash_error = False
        self.cache_until = 0.0
        self.port = False
        self.supervisor = None
        self.run = True
        self.last_details = ''

    def state(self):
        if time.monotonic() >= self.cache_until:
            self.port = bool(engine.request_status())
            self.supervisor = engine.managed_pid()
            self.cache_until = time.monotonic() + 2.5
        return self.port, self.supervisor

    def entries(self):
        if self.page == 'home':
            up, pid = self.state()
            return [
                ('Остановить сервер' if pid or up else 'Запустить сервер','toggle'),
                ('Перезапустить NEXUS','restart'),
                ('M5StickC Plus2 · Wi-Fi и прошивка','m5'),
                ('Настройки AI и голоса','settings'),
                ('Самообучение · NEXUS 2.0','learn'),
                ('Обновить NEXUS из GitHub','update'),
                ('Откатить обновление NEXUS','rollback'),
                ('Журнал событий','logs'),
                ('Диагностика','diagnostics'),
            ]
        if self.page == 'm5':
            return [
                ('Состояние и параметры M5','m5-status'),
                ('Настроить Wi-Fi M5StickC','m5-wifi'),
                ('Собрать прошивку (без записи)','m5-build'),
                ('Прошить собранную версию','m5-flash'),
                ('Собрать и прошить M5','m5-build-flash'),
            ]
        if self.page == 'learn':
            shared = learning_store.sharing()
            return [
                ('Состояние обучения','learn-stats'),
                ('Воспоминания · список','learn-list'),
                ('Найти в памяти','learn-search'),
                ('Gemini: передавать память · '+('ДА' if shared else 'НЕТ'),'learn-toggle'),
                ('Удалить запись по ID','learn-forget'),
                ('Очистить всю память · подтверждение','learn-clear'),
            ]
        if self.page == 'settings':
            voice = read_mode('voice_clarity_mode','clear', {'clear','original'})
            speed = read_mode('speed_mode','balanced', {'fast','balanced'})
            vm = ('Clarity' if voice == 'clear' else 'Original') if available_voice() else 'нет модуля'
            sm = ('Быстро' if speed == 'fast' else 'Баланс') if available_speed() else 'нет R89'
            return [
                ('Голос · '+vm,'voice'),
                ('Скорость · '+sm,'speed'),
                ('Модель Gemini','model'),
                ('Ключ Gemini','key-gemini'),
                ('Ключ OpenRouter','key-router'),
                ('Проверить Gemini','test'),
            ]
        return []

    def line(self, label, screen_width, color=MUTED):
        print(c('  '+crop(label, screen_width-4),color))

    def draw(self):
        up, pid = self.state()
        w = max(34, min(76, shutil.get_terminal_size((48,24)).columns))
        if ANSI: print('\x1b[2J\x1b[H',end='')
        else: print('\n'+'='*w)
        print(c('  NEXUS',ACCENT,True)+' '+c('/ CONTROL CENTER',MUTED))
        print(c('  '+'━'*(w-4),MUTED))
        state = '● В СЕТИ' if up else '○ НЕТ СВЯЗИ'
        print('  '+c(state,GREEN if up else GOLD)+'  '+c(crop(('SUPERVISOR '+str(pid)) if pid else 'ПРОЦЕСС НЕ НАЙДЕН',w-19),MUTED))
        try: model = engine.config().get('GEMINI_MODEL') or engine.MODEL_DEFAULT
        except Exception: model = 'не определена'
        self.line('Модель: '+model,w)
        print()
        if self.page in ('home','settings','m5','learn'):
            print(c({'home':'  ГЛАВНАЯ','settings':'  НАСТРОЙКИ','m5':'  M5STICKC PLUS2','learn':'  ОБУЧЕНИЕ 2.0'}[self.page],ACCENT,True))
            print()
            for i,(label,action) in enumerate(self.entries()):
                focus = i == self.selected[self.page]
                mark = '▸' if focus else ' '
                text = f' {mark} {i+1}  {crop(label,w-12)}'
                print(c(text,ACCENT if focus else '38;2;221;229;242',focus))
            print()
            print(c('  '+'─'*(w-4),MUTED))
            if self.flash:
                print(c('  '+crop(sanitise(self.flash),w-4),GOLD if self.flash_error else GREEN))
            else:
                print(c('  ↑↓ выбор · Enter действие',MUTED))
            print(c('  '+crop('1–9 быстро · 0 назад/выход · R обновить',w-4),MUTED))
        elif self.page=='learning-list':
            print(c('  ЛОКАЛЬНАЯ ПАМЯТЬ 2.0',ACCENT,True))
            print()
            if self.learning_filter:
                records=learning_store.recall(self.learning_filter,limit=12)
            else:
                records=learning_store.list_facts(12)
            if not records:
                self.line('Нет подходящих записей',w)
            for rec in records:
                self.line(str(rec['id'])+' · '+rec['text'],w,'38;2;221;229;242')
            print()
            self.line('0 назад · R обновить · Q выход',w)
        elif self.page=='logs':
            print(c('  ЖУРНАЛ СОБЫТИЙ',ACCENT,True))
            print()
            try: lines=LOG.read_text(errors='replace').splitlines()
            except OSError: lines=['Журнал пока отсутствует']
            height=max(7,shutil.get_terminal_size((48,24)).lines-11)
            for ln in lines[-min(25,height):]:
                ln=sanitise(ln)
                col=RED if 'ERROR' in ln.upper() else GOLD if 'WARNING' in ln.upper() else MUTED
                print('  '+c(crop(ln,w-4),col))
            print('\n'+c('  0 назад · R обновить · Q выход',MUTED))
        elif self.page=='diagnostics':
            print(c('  ДИАГНОСТИКА',ACCENT,True))
            print()
            try: cfg=engine.config()
            except Exception: cfg={}
            stt_mode, stt_up = check_stt()
            firmware=Path.home()/'nexus-firmware/firmware/.pio/build/m5stickc_plus2/firmware.bin'
            info = [
                ('Мост 8765','HTTP отвечает' if up else 'не отвечает'),
                ('Supervisor',str(pid) if pid else 'не запущен/не управляется'),
                ('Локальный STT',stt_mode+' / '+('порт 8766 открыт' if stt_up else 'порт закрыт')),
                ('Gemini ключ','есть' if cfg.get('GEMINI_API_KEY') else 'не задан'),
                ('OpenRouter','есть' if cfg.get('OPENROUTER_API_KEY') else 'не задан'),
                ('Voice Clarity','установлен' if available_voice() else 'не установлен'),
                ('Режим R89','установлен' if available_speed() else 'не установлен'),
                ('Прошивка','есть · '+datetime.fromtimestamp(firmware.stat().st_mtime).strftime('%d.%m %H:%M') if firmware.is_file() else 'не найдена'),
                ('Версия NEXUS',(CFG/'installed_version').read_text(encoding='utf-8').strip() if (CFG/'installed_version').is_file() else 'неизвестна'),
                ('Wi-Fi M5',m5._value('NEXUS_WIFI_SSID') or 'не настроен'),
                ('Откат', releases.backup_version(releases.backup_candidates()[0]) if releases.backup_candidates() else 'копия не создана'),
            ]
            for key,val in info:
                print('  '+c(key+': ',MUTED)+crop(val,max(14,w-width(key)-6)))
            print('\n'+c('  0 назад · R обновить · Q выход',MUTED))
        sys.stdout.flush()

    def popup(self, fn, sensitive=False):
        # Read all interactive inputs using the standard cooked terminal mode.
        print('\n'+c('  ── ДЕЙСТВИЕ ──',ACCENT))
        tee=Tee(sys.stdout)
        try:
            with contextlib.redirect_stdout(tee):
                result = fn()
            lines=[sanitise(v.strip()) for v in tee.getvalue().splitlines() if v.strip()]
            if result is False:
                # Device helpers already print the actual cause (e.g. Wi-Fi
                # missing, build failed). Never replace it with RuntimeError.
                self.flash_error=True
                self.flash=lines[-1][:200] if lines else 'Действие не выполнено'
            else:
                self.flash_error=False
                self.flash='Готово' if sensitive else (lines[-1][:200] if lines else 'Готово')
        except (EOFError,KeyboardInterrupt):
            self.flash_error=True
            self.flash='Действие отменено'
        except Exception as exc:
            self.flash_error=True
            self.flash=sanitise('Ошибка: '+type(exc).__name__+': '+str(exc)[:140])
        self.cache_until=0

    def handle(self, action):
        if action=='settings': self.page='settings';self.flash='';return
        if action=='learn': self.page='learn';self.flash='';return
        if action=='learn-stats':
            def learn_stats():
                data=learning_store.stats()
                print('Воспоминаний:',data['facts'])
                print('Оценок ответов:',data['feedback'])
                print('Исправлений:',data['corrections'])
                print('Облачная персонализация:', 'включена' if data['cloud_personalization'] else 'выключена')
            self.popup(learn_stats);return
        if action=='learn-list':
            self.learning_filter='';self.page='learning-list';self.flash='';return
        if action=='learn-search':
            try: self.learning_filter=input('Что найти в памяти: ').strip()[:150]
            except (EOFError,KeyboardInterrupt):self.flash='Поиск отменён';return
            self.page='learning-list';self.flash='';return
        if action=='learn-toggle':
            def toggle_learning():
                enabled=not learning_store.sharing()
                learning_store.set_sharing(enabled)
                print('Передача релевантных воспоминаний в Gemini:', 'ВКЛ' if enabled else 'ВЫКЛ')
            self.popup(toggle_learning);return
        if action=='learn-forget':
            def forget_memory():
                raw=input('ID записи для удаления (Enter = отмена): ').strip()
                if not raw:return False
                if not raw.isdecimal():print('Нужен номер записи.');return False
                if learning_store.forget(int(raw)):
                    print('Запись удалена.')
                    return True
                print('Запись не найдена.')
                return False
            self.popup(forget_memory);return
        if action=='learn-clear':
            def clear_learning():
                if input('Очистить ВСЮ память NEXUS 2.0? Напиши УДАЛИТЬ: ').strip() != 'УДАЛИТЬ':
                    print('Очистка отменена.')
                    return False
                learning_store.clear_all()
                print('Локальная память NEXUS 2.0 очищена.')
                return True
            self.popup(clear_learning);return
        if action=='m5': self.page='m5';self.flash='';return
        if action=='m5-status': self.popup(m5.status);return
        if action=='m5-wifi': self.popup(m5.wifi,sensitive=True);return
        if action=='m5-build': self.popup(m5.build);return
        if action=='m5-flash': self.popup(m5.flash);return
        if action=='m5-build-flash': self.popup(m5.build_flash);return
        if action=='rollback':
            def run_rollback():
                code = subprocess.run([sys.executable,str(ROOT/'nexus_update.py'),'--rollback'],check=False).returncode
                if code: raise RuntimeError('Откат отменён или не выполнен. Проверь вывод выше')
                print('Предыдущая версия восстановлена. Перезапусти меню nexus.')
            self.popup(run_rollback);return
        if action=='update':
            def run_update():
                code = subprocess.run([sys.executable,str(ROOT/'nexus_update.py')],check=False).returncode
                if code: raise RuntimeError('Обновление не удалось. Проверь вывод выше')
                print('Готово. Для нового интерфейса перезапусти меню nexus.')
            self.popup(run_update);return
        if action=='logs': self.page='logs';self.flash='';return
        if action=='diagnostics': self.page='diagnostics';self.flash='';return
        if action=='toggle':
            self.popup(engine.stop if (self.supervisor or self.port) else engine.start)
        elif action=='restart':
            self.popup(lambda: (engine.stop(),engine.start()))
        elif action=='voice':
            if not available_voice(): self.flash='Сначала установи Voice Clarity R88';return
            mode=read_mode('voice_clarity_mode','clear',{'clear','original'})
            new='original' if mode=='clear' else 'clear'
            write_mode('voice_clarity_mode',new)
            self.flash='Озвучка: '+('Original' if new=='original' else 'Voice Clarity')
        elif action=='speed':
            if not available_speed(): self.flash='Режим скорости R89 не установлен';return
            mode=read_mode('speed_mode','balanced',{'fast','balanced'})
            new='balanced' if mode=='fast' else 'fast'
            write_mode('speed_mode',new)
            self.flash='Скорость: '+('Сбалансированная' if new=='balanced' else 'Максимальная')
        elif action=='model': self.popup(engine.change_model)
        elif action=='key-gemini': self.popup(lambda:engine.change_key('gemini'),sensitive=True)
        elif action=='key-router': self.popup(lambda:engine.change_key('openrouter'),sensitive=True)
        elif action=='test': self.popup(engine.test)
        elif action=='token':
            print('\n'+c('  Показывать токен на людях небезопасно',GOLD))
            self.popup(engine.show_token,sensitive=True)
            # Do not immediately erase a revealed credential without letting the owner see it.
            print(c('  Любая клавиша — скрыть токен',MUTED))
            keypress(timeout=None)
            self.flash='Данные токена скрыты'

    def loop(self):
        try:
            while self.run:
                self.draw()
                key=keypress()
                if key is None: continue
                if key=='q': return
                if key in ('0','back','h'):
                    if self.page=='home': return
                    self.page='learn' if self.page=='learning-list' else 'home'
                    self.flash='';continue
                if key=='r': self.cache_until=0;continue
                if self.page not in ('home','settings','m5','learn'): continue
                entries=self.entries()
                selection=self.selected[self.page]
                if key in ('down','j'):
                    self.selected[self.page]=(selection+1)%len(entries);continue
                if key in ('up','k'):
                    self.selected[self.page]=(selection-1)%len(entries);continue
                if key in ('enter','l',' '):
                    self.handle(entries[selection][1]);continue
                if key in ('1','2','3','4','5','6','7','8','9'):
                    idx=int(key)-1
                    if idx < len(entries):
                        self.selected[self.page]=idx
                        self.handle(entries[idx][1])
        finally:
            if ANSI: print('\x1b[0m\n',end='',flush=True)


def main():
    ControlCenter().loop()

if __name__ == '__main__':
    main()
