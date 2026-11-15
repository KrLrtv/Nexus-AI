"""Native task -> bounded capability -> result. No arbitrary code execution.

The planner may request acquisition, but cannot change the original task or
expand the existing skill_autolearn policy. State is local, attempts are bounded,
and neither inputs nor provider errors are stored in this module's database.
"""
from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
import hashlib
import os
from pathlib import Path
import re
import sqlite3
import threading
import time
import uuid

NAMESPACE = '[native-v1] '
COOLDOWN = 600
LEASE = 210
INPUT_TTL = 600
DAILY_LIMIT = 8
PREFIX = re.compile(r'^\s*(?:нексус|nexus)[,!: -]+', re.I)
TASK = re.compile(
    r'^(?:(?:пожалуйста|можешь|можешь ли ты|мог бы ты|давай|хочу,? чтобы ты)\s+)*'
    r'(?:сделай|сделать|преобразуй|преобразовать|переведи|перевести|вычисли|вычислить|'
    r'посчитай|посчитать|отсортируй|отсортировать|разбери|разобрать|обработай|обработать|'
    r'сократи|сократить|покажи|показать|найди|найти|добавь|добавить|создай|создать|'
    r'открой|открыть|напиши|написать|отправь|отправить|собери|собрать|подключи|'
    r'подключить|исправь|исправить|убери|убрать|проверь|проверить|замени|заменить|'
    r'настрой|настроить|включи|выключи|запусти|установи|удали|сохрани)\b', re.I)
GAP = re.compile(
    r'^(?:к сожалению[, ]*|я\s+|пока\s+|сейчас\s+)*'
    r'(?:не умею\b|не поддерживаю\b|не располагаю (?:нужным|подходящим) инструментом|'
    r'нет (?:нужного|подходящего) инструмента)', re.I)
NOT_A_GAP = re.compile(
    r'разрешен|разрешён|доступ|авторизац|ключ|токен|парол|сет[ьи]|сетев|интернет|соединен|'
    r'соединён|тайм.?аут|квот|лимит|не могу помочь|не буду помогать|не могу содействовать', re.I)
SECRET = re.compile(r'(?i)\b(?:пароль|токен|password|api[_ -]?key)\b|AIza|github_pat_|\bBearer\s')
CONTROLS = {
    'включи автообучение': 'on', 'включить автообучение': 'on',
    'выключи автообучение': 'off', 'выключить автообучение': 'off',
    'отключи автообучение': 'off', 'статус автообучения': 'status',
    'останови обучение': 'cancel', 'отмени обучение': 'cancel',
}


def normalized(text):
    return ' '.join(str(text).casefold().replace('ё', 'е').split())


def is_task_request(text):
    return bool(TASK.match(PREFIX.sub('', str(text).strip())))


def is_control(text):
    return normalized(PREFIX.sub('', str(text).strip())).rstrip('.!?') in CONTROLS


@dataclass(frozen=True)
class Request:
    operation: str
    data: str | None = None

    @property
    def key(self):
        return NAMESPACE + self.operation

    @property
    def original(self):
        return self.operation if self.data is None else self.operation + ': ' + self.data


def split_request(text):
    if not isinstance(text, str) or not text.strip() or len(text) > 3000:
        raise ValueError('invalid request')
    text = PREFIX.sub('', text.strip())
    # A colon in a URL or time is not an instruction/data separator.
    colon = re.search(r'(?<!\d):(?!//)', text)
    if colon:
        operation, data = text[:colon.start()].strip(), text[colon.end():].lstrip()
    else:
        quoted = re.fullmatch(r'(.+?)\s+(?:«([^»]*)»|"([^"]*)")\s*[.!]?', text, re.S)
        if quoted:
            operation = quoted[1].strip()
            data = quoted[2] if quoted[2] is not None else quoted[3]
        else:
            operation, data = text, None
    if not operation or len(NAMESPACE + operation) > 300:
        raise ValueError('operation too long')
    return Request(operation, data)


def response(reply, status, skill_id=None, answered=False):
    return {'reply': str(reply)[:1500], 'provider': 'NEXUS-Native',
            'actions': [], 'pending': [], 'learning': {
                'status': status, 'skill_id': skill_id, 'answered': bool(answered)}}


class ForgeBackend:
    """Small adapter around the already-tested activation/execution boundary."""
    def __init__(self):
        import skill_forge
        import skill_autolearn
        self.forge, self.auto = skill_forge, skill_autolearn

    @contextmanager
    def gate(self):
        acquired = self.auto.LEARNING_LOCK.acquire(blocking=False)
        try:
            yield acquired
        finally:
            if acquired:
                self.auto.LEARNING_LOCK.release()

    def clean(self, request):
        self.forge._clean_intent(request.key)
        if self.forge.TOKEN_PATTERN.search(request.original):
            raise ValueError('sensitive input')

    def find(self, key):
        return self.auto._find_active(self.forge, key)

    def get(self, identifier):
        return self.forge._get(identifier)

    def ready(self):
        return bool(os.environ.get('GEMINI_API_KEY', '').strip())

    def propose(self, key):
        return self.forge.propose(key)

    def check(self, draft):
        self.auto._check_policy(self.forge, draft)

    def approve(self, draft):
        return self.forge.approve(draft['id'], expected_digest=draft['digest'])

    def reject(self, identifier):
        self.forge.reject(identifier)

    def answer(self, draft, request):
        invocation = 'данные: ' + request.data if request.data is not None else request.operation
        return self.auto._answer(self.forge, draft, invocation, reused=True)


class NativeLearning:
    def __init__(self, path=None, backend=None, clock=time.time):
        self.path = Path(path or os.environ.get('NEXUS_NATIVE_DB',
                         str(Path.home()/'.nexus_ai'/'native_learning.sqlite3')))
        self.backend = backend
        self.clock = clock
        self.lock = threading.RLock()

    def _backend(self):
        if self.backend is None:
            self.backend = ForgeBackend()
        return self.backend

    @contextmanager
    def db(self):
        with self.lock:
            self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            if self.path.is_symlink():
                raise RuntimeError('unsafe state path')
            try:
                fd = os.open(self.path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
                os.close(fd)
            except FileExistsError:
                if self.path.is_symlink():
                    raise RuntimeError('unsafe state path')
            os.chmod(self.path, 0o600)
            c = sqlite3.connect(self.path, timeout=5)
            c.row_factory = sqlite3.Row
            try:
                c.execute('BEGIN IMMEDIATE')
                c.execute('CREATE TABLE IF NOT EXISTS settings(key TEXT PRIMARY KEY,value TEXT)')
                c.execute('''CREATE TABLE IF NOT EXISTS jobs(
                    key TEXT PRIMARY KEY,state TEXT,run_id TEXT,updated REAL,
                    retry_at REAL,skill_id INTEGER)''')
                c.execute('CREATE TABLE IF NOT EXISTS attempts(created REAL)')
                c.execute('''CREATE TABLE IF NOT EXISTS input_wait(
                    id INTEGER PRIMARY KEY CHECK(id=1),operation TEXT,skill_id INTEGER,created REAL)''')
                yield c
                c.commit()
            except BaseException:
                c.rollback()
                raise
            finally:
                c.close()

    @staticmethod
    def _key(request):
        return hashlib.sha256(normalized(request.key).encode()).hexdigest()

    def enabled(self):
        if os.environ.get('NEXUS_NATIVE_LEARNING', '1').lower() in ('0', 'off', 'false'):
            return False
        with self.db() as c:
            row = c.execute("SELECT value FROM settings WHERE key='enabled'").fetchone()
        return not row or row[0] == 'on'

    def cancel(self):
        with self.db() as c:
            c.execute("UPDATE jobs SET state='cancelled',retry_at=? WHERE state='running'",
                      (self.clock() + COOLDOWN,))
            c.execute('DELETE FROM input_wait')

    def control(self, text):
        command = CONTROLS.get(normalized(PREFIX.sub('', str(text).strip())).rstrip('.!?'))
        if not command:
            return None
        try:
            if command in ('on', 'off'):
                with self.db() as c:
                    c.execute("INSERT OR REPLACE INTO settings VALUES('enabled',?)", (command,))
                if command == 'off':
                    self.cancel()
            if command == 'cancel':
                self.cancel()
                return response('Обучение остановлено. Уже начатый сетевой запрос может завершиться, '
                                'но его результат не будет запущен автоматически.', 'cancelled')
            on = self.enabled()
            return response('Автообучение включено.' if on else
                            'Автообучение выключено. Ранее освоенные навыки остаются доступны.',
                            'enabled' if on else 'disabled')
        except (sqlite3.Error, OSError, RuntimeError):
            return response('Не удалось изменить состояние автообучения. Проверь локальную базу.', 'error')

    def _reserve(self, request):
        now, key, token = self.clock(), self._key(request), uuid.uuid4().hex
        with self.db() as c:
            c.execute("UPDATE jobs SET state='interrupted',retry_at=? "
                      "WHERE state='running' AND updated<?", (now + COOLDOWN, now - LEASE))
            if c.execute("SELECT 1 FROM jobs WHERE state='running'").fetchone():
                return None, response('Уже осваиваю другую задачу. Второй запуск не начат.', 'busy')
            row = c.execute('SELECT * FROM jobs WHERE key=?', (key,)).fetchone()
            if row and row['retry_at'] > now:
                return None, response('Этот способ уже проверялся без подтверждённого результата. '
                                      'Повторное обучение временно приостановлено, чтобы не зациклиться.', 'cooldown')
            c.execute('DELETE FROM attempts WHERE created<?', (now - 86400,))
            if c.execute('SELECT count(*) FROM attempts').fetchone()[0] >= DAILY_LIMIT:
                return None, response('Достигнут лимит новых обучений за 24 часа. '
                                      'Существующие навыки доступны.', 'budget')
            c.execute('INSERT INTO attempts VALUES(?)', (now,))
            c.execute('INSERT OR REPLACE INTO jobs VALUES(?,?,?,?,?,NULL)',
                      (key, 'running', token, now, 0))
            c.execute("DELETE FROM jobs WHERE state!='running' AND key NOT IN "
                      '(SELECT key FROM jobs ORDER BY updated DESC LIMIT 100)')
        return token, None

    def _live(self, request, token):
        with self.db() as c:
            row = c.execute("SELECT 1 FROM jobs WHERE key=? AND run_id=? AND state='running'",
                            (self._key(request), token)).fetchone()
        return bool(row) and self.enabled()

    def _finish(self, request, token, state, identifier=None):
        retry = 0 if state in ('answered', 'needs_input', 'tool_result') else self.clock() + COOLDOWN
        with self.db() as c:
            c.execute("UPDATE jobs SET state=?,updated=?,retry_at=?,skill_id=? "
                      "WHERE key=? AND run_id=? AND state='running'",
                      (state, self.clock(), retry, identifier, self._key(request), token))

    def _answer(self, draft, request):
        backend = self._backend()
        backend.check(draft)
        identifier = draft['id']
        if draft['payload']['kind'] == 'pure' and request.data is None:
            with self.db() as c:
                c.execute('INSERT OR REPLACE INTO input_wait VALUES(1,?,?,?)',
                          (request.operation, identifier, self.clock()))
            return response('Способ готов. Нужны входные данные: пришли число, текст в кавычках '
                            'или скажи «данные: …». Повторять задачу не нужно.', 'needs_input', identifier)
        if draft['payload']['kind'] == 'recipe' and request.data is not None:
            return response('Сценарий не принимает отдельные входные данные. Запрос не выполнен.',
                            'unconfirmed', identifier)
        result = backend.answer(draft, request)
        reply = re.sub(r'^(?:Использую навык #\d+\. |Научился\. Навык #\d+ включён автоматически\. )',
                       '', result.reply)
        reply = re.sub(r'^Освоенный (?:навык|сценарий): ', '', reply)
        # Read-only adapters return text, including errors. Do not turn that into
        # a claim of verified execution, delivery, or semantic correctness.
        status = ('answered' if draft['payload']['kind'] == 'pure' else 'tool_result') if result.answered else 'unconfirmed'
        return response(reply, status, identifier,
                        answered=result.answered and draft['payload']['kind'] == 'pure')

    def reuse(self, text):
        """No cloud calls. Follow-up input requires an unambiguous data marker."""
        try:
            raw = str(text).strip()
            match = re.fullmatch(r'данные\s*:\s*(.*)', raw, re.I | re.S)
            quoted = re.fullmatch(r'«([^»]*)»|"([^"]*)"', raw, re.S)
            number = re.fullmatch(r'[+-]?\d+(?:\.\d+)?', raw)
            if match or quoted or number:
                with self.db() as c:
                    c.execute('DELETE FROM input_wait WHERE created<?', (self.clock() - INPUT_TTL,))
                    row = c.execute('SELECT * FROM input_wait WHERE id=1').fetchone()
                    if row:
                        c.execute('DELETE FROM input_wait WHERE id=1')
                if row:
                    value = match[1] if match else ((quoted[1] if quoted[1] is not None else quoted[2]) if quoted else raw)
                    request = Request(row['operation'], value)
                    backend = self._backend()
                    backend.clean(request)
                    draft = backend.get(row['skill_id'])
                    if not draft or draft['state'] != 'active' or normalized(draft['intent']) != normalized(request.key):
                        return response('Навык отключён или изменён. Данные не обрабатывались.', 'revoked')
                    return self._answer(draft, request)
                if match:
                    return response('Сейчас нет задачи, ожидающей данные.', 'no_pending_input')
                return None
            if not is_task_request(raw):
                return None
            request = split_request(raw)
            backend = self._backend()
            backend.clean(request)
            draft = backend.find(request.key)
            return self._answer(draft, request) if draft else None
        except (ValueError, TypeError):
            if re.match(r'^данные\s*:', str(text).strip(), re.I):
                return response('Данные не приняты: проверь формат и отсутствие секретов.', 'invalid_input')
            return None
        except Exception:
            return response('Освоенный способ сейчас недоступен. Автоматический повтор не запущен.', 'error')

    def learn(self, text):
        request = None
        token = None
        try:
            if not is_task_request(text):
                return response('Нужна конкретная задача, а не отдельная команда обучения.', 'not_task')
            if SECRET.search(str(text)):
                return response('Не использую пароли или токены для создания навыка.', 'sensitive')
            request = split_request(text)
            backend = self._backend()
            backend.clean(request)
            with backend.gate() as acquired:
                if not acquired:
                    return response('Обучение уже выполняется. Второй запуск не начат.', 'busy')
                active = backend.find(request.key)
                if active:
                    return self._answer(active, request)
                if not self.enabled():
                    return response('Автообучение выключено. Его можно включить голосом: '
                                    '«включи автообучение».', 'disabled')
                if not backend.ready():
                    return response('Для освоения нового способа нужно настроить Gemini в меню NEXUS.', 'needs_setup')
                # A manually rejected native skill must not silently reappear.
                with self.db() as c:
                    row = c.execute('SELECT skill_id FROM jobs WHERE key=?', (self._key(request),)).fetchone()
                previous = backend.get(row[0]) if row and row[0] is not None else None
                if previous and previous['state'] == 'rejected':
                    return response('Этот навык отключён. Автоматически заново его не включаю.', 'revoked')
                token, blocked = self._reserve(request)
                if blocked:
                    return blocked
                draft = backend.propose(request.key)
                if not self._live(request, token):
                    return response('Обучение остановлено. Полученный черновик не запущен.', 'cancelled')
                if draft['intent'] != request.key:
                    raise ValueError('wrong task')
                if draft['payload'].get('kind') == 'proposal':
                    outcome = response('Для задачи нужен новый системный адаптер. Сохранён черновик '
                                       'для проверки; код не установлен и задача не выполнена.',
                                       'needs_adapter', draft['id'])
                elif draft['state'] != 'tested':
                    outcome = response('Не удалось подтвердить работоспособность способа. '
                                       'Черновик сохранён, задача не выполнена.', 'failed', draft['id'])
                else:
                    backend.check(draft)
                    if not self._live(request, token):
                        return response('Обучение остановлено. Навык не запущен.', 'cancelled')
                    active = backend.approve(draft)
                    if not self._live(request, token):
                        backend.reject(active['id'])
                        return response('Обучение остановлено. Новый навык отключён и не запущен.', 'cancelled')
                    outcome = self._answer(active, request)
                self._finish(request, token, outcome['learning']['status'], draft['id'])
                return outcome
        except Exception:
            if request is not None and token is not None:
                try:
                    self._finish(request, token, 'failed')
                except Exception:
                    pass
            return response('Не удалось получить проверенный результат. Новые действия не повторялись; '
                            'проверь подключение и журнал навыков.', 'failed')

    def after_answer(self, text, result):
        # Never replay a partially executed request, a confirmation, a provider
        # failure, or an access/policy refusal. Only a narrowly worded skill gap.
        if (result.get('actions') or result.get('pending') or result.get('learning')
                or not is_task_request(text)):
            return result
        answer = str(result.get('reply', '')).strip()
        if GAP.match(answer) and not NOT_A_GAP.search(answer):
            return self.learn(text)
        return result


ENGINE = NativeLearning()

def control(text):
    return ENGINE.control(text)

def reuse(text):
    return ENGINE.reuse(text)

def learn(text):
    return ENGINE.learn(text)

def after_answer(text, result):
    return ENGINE.after_answer(text, result)

def cancel():
    try:
        ENGINE.cancel()
    except (sqlite3.Error, OSError, RuntimeError):
        pass


PLANNER_HINT = (
    ' Если для конкретной задачи не хватает возможности, используй learn_capability '
    'вместо просьбы сказать «научись». Сначала предпочитай существующие инструменты. '
    'Не обучайся для обычного разговора, перевода или вопроса, на который можешь ответить. '
    'Отсутствие Android-разрешения, авторизации, сети или ключа не является новым навыком. '
    'Не вызывай обучение после уже выполненных действий. Инструмент продолжит исходную '
    'задачу сам; не меняй её и не утверждай успех без результата.'
)
