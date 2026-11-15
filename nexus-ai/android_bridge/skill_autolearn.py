"""Explicit learn -> validate -> activate -> answer, without a second approval.

Only the existing bounded expression interpreter and five read-only adapters
may enter this path. Proposals, SMS, package installs and firmware do not.
Low-level forge.propose/store remain draft-only; old drafts are not activated.
"""
from __future__ import annotations

from dataclasses import dataclass
import hmac
import threading

# Deliberately independent of a future expansion of the general tool catalog.
AUTO_READ_TOOLS = frozenset({
    'battery', 'notes_list', 'reminders_list', 'memory_search', 'reminder_timed_list',
})
LEARNING_LOCK = threading.Lock()


@dataclass(frozen=True)
class LearnResult:
    skill_id: int | None
    active: bool
    answered: bool
    reply: str


def _check_policy(forge, draft):
    """Revalidate actual stored payload, not a model's claim or cached report."""
    if draft['state'] not in ('tested', 'active'):
        raise ValueError('Draft has not passed testing.')
    forge._integrity(draft, approved=draft['state'] == 'active')
    payload = draft['payload']
    kind = payload.get('kind')
    if kind == 'recipe':
        steps = payload.get('steps')
        if not isinstance(steps, list) or not 1 <= len(steps) <= 4:
            raise ValueError('Invalid read-only recipe.')
        if any(not isinstance(s, dict) or s.get('tool') not in AUTO_READ_TOOLS for s in steps):
            raise ValueError('This capability needs separate authorization.')
    elif kind != 'pure':
        raise ValueError('Source proposals cannot be automatically installed.')
    report = forge.validate(payload)
    checks = report.get('checks', [])
    if (report.get('tested') is not True or report.get('deployable') is not True
            or not checks or any(c.get('passed') is not True for c in checks)):
        raise ValueError('Tests have not passed.')


def _find_active(forge, intent):
    with forge._db() as connection:
        return forge._decode(connection.execute(
            "SELECT * FROM drafts WHERE state='active' AND intent_norm=? ORDER BY id DESC LIMIT 1",
            (forge._norm(intent),)).fetchone())


def _answer(forge, draft, intent, reused=False):
    identifier = draft['id']
    prefix = (f'Использую навык #{identifier}. ' if reused else
              f'Научился. Навык #{identifier} включён автоматически. ')
    # Do not feed an instruction into a transform as though it were input data.
    if draft['payload']['kind'] == 'pure' and ':' not in intent:
        return LearnResult(identifier, True, False, prefix +
            f'Для ответа нужны данные: скажи «навык {identifier}: твой текст или число».')
    try:
        raw = intent.partition(':')[2].lstrip() if ':' in intent else ''
        value = forge._argument(raw) if draft['payload']['kind'] == 'pure' else ''
        # Re-read under the same transaction used for invocation. A revoked or
        # replaced skill must not run through the stale activation snapshot.
        with forge._db() as connection:
            current = forge._decode(connection.execute(
                'SELECT * FROM drafts WHERE id=?', (identifier,)).fetchone())
            if not current or current['state'] != 'active':
                return LearnResult(identifier, False, False,
                    f'Навык #{identifier} уже отключён или заменён. Запрос не выполнялся.')
            if not hmac.compare_digest(current['digest'], draft['digest']):
                raise ValueError('Skill changed before its first invocation.')
            _check_policy(forge, current)
            reply = forge._invoke(current, value)
        return LearnResult(identifier, True, True, prefix + reply)
    except Exception:
        # Do not echo provider errors or exception text that might contain data.
        # Keep a valid skill available for a corrected input; never run it twice.
        return LearnResult(identifier, True, False, prefix +
            'Но исходный запрос не выполнен: проверь входные данные и доступность '
            'функций телефона. Результат не подтверждён; автоматического повтора нет.')


def learn_and_answer(intent: str) -> LearnResult:
    """Called only by an explicit learning command or the create-skill menu."""
    import skill_forge as forge

    if not LEARNING_LOCK.acquire(blocking=False):
        return LearnResult(None, False, False,
            'Обучение уже выполняется. Второй запуск не начат.')
    try:
        intent = forge._clean_intent(intent)
        active = _find_active(forge, intent)
        if active:
            _check_policy(forge, active)
            return _answer(forge, active, intent, reused=True)
        draft = forge.propose(intent)
        identifier = draft['id']
        # Proposal/review drafts remain visible, but are never auto-approved.
        if draft['state'] != 'tested' or draft.get('kind') not in ('pure', 'recipe'):
            return LearnResult(identifier, False, False,
                f'Создан черновик #{identifier}, но навык не включён. '
                'Проверки не пройдены либо требуется новый системный адаптер. '
                'Код не запускался. Подробности — в Лаборатории модулей.')
        _check_policy(forge, draft)
        # Never accept a candidate for another goal, including an altered hash.
        if draft['intent'] != intent:
            raise ValueError('The generated draft belongs to a different task.')
        activated = forge.approve(identifier, expected_digest=draft['digest'])
        return _answer(forge, activated, intent)
    except Exception:
        return LearnResult(None, False, False,
            'Не получилось создать и включить проверенный навык. '
            'Проверь Gemini, лимит навыков и журнал проверок. '
            'Новый навык не запускался; можно повторить «научись».')
    finally:
        LEARNING_LOCK.release()
