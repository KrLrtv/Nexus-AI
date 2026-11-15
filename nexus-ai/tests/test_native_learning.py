"""Offline orchestration tests; no live provider, phone or M5 is used."""
import ast
from contextlib import contextmanager
import copy
import hashlib
import importlib.util
import os
from pathlib import Path
import sqlite3
import sys
import tempfile
import threading
import time
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[2]
BRIDGE = ROOT / 'nexus-ai' / 'android_bridge'
sys.path.insert(0, str(BRIDGE))
import native_learning as native


class Backend:
    def __init__(self):
        self.items = {}
        self.proposals = []
        self.invocations = []
        self.next_kind = 'pure'
        self.on_propose = None
        self.on_approve = None
        self.provider_ready = True
        self.mutex = threading.Lock()
        self.bad = False

    @contextmanager
    def gate(self):
        ok = self.mutex.acquire(blocking=False)
        try:
            yield ok
        finally:
            if ok:
                self.mutex.release()

    def clean(self, request):
        if native.SECRET.search(request.original):
            raise ValueError('sensitive')

    def find(self, key):
        return next((copy.deepcopy(x) for x in self.items.values()
                     if native.normalized(x['intent']) == native.normalized(key)
                     and x['state'] == 'active'), None)

    def get(self, identifier):
        return copy.deepcopy(self.items.get(identifier))

    def ready(self):
        return self.provider_ready

    def propose(self, key):
        self.proposals.append(key)
        if self.on_propose:
            self.on_propose()
        item = dict(id=len(self.items)+1, state='tested', intent=key,
                    payload={'kind': self.next_kind}, digest='test-digest')
        self.items[item['id']] = item
        return copy.deepcopy(item)

    def check(self, draft):
        if self.bad or draft['payload']['kind'] not in ('pure', 'recipe'):
            raise ValueError('policy')
        if self.items[draft['id']]['state'] not in ('active', 'tested'):
            raise ValueError('revoked')

    def approve(self, draft):
        self.items[draft['id']]['state'] = 'active'
        if self.on_approve:
            self.on_approve()
        return self.get(draft['id'])

    def reject(self, identifier):
        self.items[identifier]['state'] = 'rejected'

    def answer(self, draft, request):
        self.invocations.append((draft['id'], request.data))
        return SimpleNamespace(active=True, answered=True,
            reply=f"Использую навык #{draft['id']}. Освоенный навык: " + str(request.data).upper())


class NativeTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.backend = Backend()
        self.now = 1000000.0
        self.path = Path(self.tmp.name)/'native.sqlite3'
        self.engine = native.NativeLearning(self.path, self.backend, lambda: self.now)
        self.environment = patch.dict(os.environ, {'NEXUS_NATIVE_LEARNING':'1'})
        self.environment.start()
        self.addCleanup(self.environment.stop)
        self.addCleanup(self.tmp.cleanup)

    def test_learns_and_answers_same_request(self):
        result = self.engine.learn('Сделай заглавными: привет')
        self.assertEqual(result['reply'], 'ПРИВЕТ')
        self.assertTrue(result['learning']['answered'])
        self.assertEqual(len(self.backend.proposals), 1)

    def test_different_data_reuses_same_operation_without_provider(self):
        self.engine.learn('Сделай заглавными: привет')
        self.backend.provider_ready = False
        result = self.engine.reuse('Сделай заглавными: мир')
        self.assertEqual(result['reply'], 'МИР')
        self.assertEqual(len(self.backend.proposals), 1)

    def test_actual_data_is_not_given_to_generator(self):
        self.engine.learn('Сделай заглавными: личные данные')
        self.assertEqual(self.backend.proposals, [native.NAMESPACE+'Сделай заглавными'])

    def test_actual_data_is_not_in_new_state_database(self):
        self.engine.learn('Сделай заглавными: my-private-input')
        with sqlite3.connect(self.path) as c:
            dump = '\n'.join(c.iterdump())
        self.assertNotIn('my-private-input', dump)
        self.assertNotIn('MY-PRIVATE-INPUT', dump)

    def test_input_followup_no_skill_number(self):
        result = self.engine.learn('Сделай заглавными')
        self.assertEqual(result['learning']['status'], 'needs_input')
        self.assertEqual(self.backend.invocations, [])
        result = self.engine.reuse('данные: привет')
        self.assertEqual(result['reply'], 'ПРИВЕТ')

    def test_quoted_followup(self):
        self.engine.learn('Сделай заглавными')
        self.assertEqual(self.engine.reuse('«привет»')['reply'], 'ПРИВЕТ')

    def test_numeric_followup(self):
        self.engine.learn('Посчитай модуль числа')
        self.assertTrue(self.engine.reuse('-3')['learning']['answered'])

    def test_followup_consumed_once(self):
        self.engine.learn('Сделай заглавными')
        self.engine.reuse('данные: привет')
        self.assertEqual(self.engine.reuse('данные: привет')['learning']['status'], 'no_pending_input')
        self.assertEqual(len(self.backend.invocations), 1)

    def test_unrelated_chat_is_not_input(self):
        self.engine.learn('Сделай заглавными')
        self.assertIsNone(self.engine.reuse('Привет'))
        self.assertEqual(self.backend.invocations, [])

    def test_expired_input_not_executed(self):
        self.engine.learn('Сделай заглавными')
        self.now += native.INPUT_TTL + 1
        self.assertEqual(self.engine.reuse('данные: привет')['learning']['status'], 'no_pending_input')

    def test_pending_survives_new_engine_instance(self):
        self.engine.learn('Сделай заглавными')
        restored = native.NativeLearning(self.path, self.backend, lambda: self.now)
        self.assertEqual(restored.reuse('данные: привет')['reply'], 'ПРИВЕТ')

    def test_case_normalized_pending(self):
        self.engine.learn('Сделай заглавными: тест')
        self.engine.reuse('сделай заглавными')
        self.assertEqual(self.engine.reuse('данные: привет')['reply'], 'ПРИВЕТ')

    def test_revocation_before_followup(self):
        self.engine.learn('Сделай заглавными')
        self.backend.reject(1)
        self.assertEqual(self.engine.reuse('данные: привет')['learning']['status'], 'revoked')
        self.assertEqual(self.backend.invocations, [])

    def test_manual_disable_does_not_regenerate(self):
        self.engine.learn('Сделай заглавными: привет')
        self.backend.reject(1)
        self.assertEqual(self.engine.learn('Сделай заглавными: мир')['learning']['status'], 'revoked')
        self.assertEqual(len(self.backend.proposals), 1)

    def test_controls_persist(self):
        self.engine.control('Нексус, выключи автообучение')
        restored = native.NativeLearning(self.path, self.backend, lambda: self.now)
        self.assertFalse(restored.enabled())
        self.assertEqual(restored.learn('Сделай заглавными: привет')['learning']['status'], 'disabled')
        self.assertEqual(self.backend.proposals, [])

    def test_disabled_learning_does_not_disable_existing_skills(self):
        self.engine.learn('Сделай заглавными: привет')
        self.engine.control('Выключи автообучение')
        self.assertEqual(self.engine.reuse('Сделай заглавными: мир')['reply'], 'МИР')

    def test_environment_off_cannot_be_overridden(self):
        with patch.dict(os.environ, {'NEXUS_NATIVE_LEARNING':'0'}):
            self.assertEqual(self.engine.control('включи автообучение')['learning']['status'], 'disabled')

    def test_cancel_during_generation_keeps_draft(self):
        self.backend.on_propose = self.engine.cancel
        result = self.engine.learn('Сделай заглавными: привет')
        self.assertEqual(result['learning']['status'], 'cancelled')
        self.assertEqual(self.backend.items[1]['state'], 'tested')
        self.assertEqual(self.backend.invocations, [])

    def test_cancel_during_activation_revokes_new_skill(self):
        self.backend.on_approve = self.engine.cancel
        result = self.engine.learn('Сделай заглавными: привет')
        self.assertEqual(result['learning']['status'], 'cancelled')
        self.assertEqual(self.backend.items[1]['state'], 'rejected')
        self.assertEqual(self.backend.invocations, [])

    def test_failed_attempt_has_cooldown(self):
        self.backend.bad = True
        self.engine.learn('Сделай заглавными: привет')
        second = self.engine.learn('Сделай заглавными: другой текст')
        self.assertEqual(second['learning']['status'], 'cooldown')
        self.assertEqual(len(self.backend.proposals), 1)

    def test_cooldown_can_expire(self):
        self.backend.bad = True
        self.engine.learn('Сделай заглавными: привет')
        self.now += native.COOLDOWN + 1
        self.backend.bad = False
        self.assertTrue(self.engine.learn('Сделай заглавными: мир')['learning']['answered'])

    def test_rolling_budget(self):
        for i in range(native.DAILY_LIMIT):
            self.engine.learn(f'Сделай преобразование {i}: текст')
        self.assertEqual(self.engine.learn('Сделай новое преобразование: текст')['learning']['status'], 'budget')
        self.now += 86401
        self.assertTrue(self.engine.learn('Сделай новое преобразование: текст')['learning']['answered'])

    def test_process_restart_cannot_replay_interrupted_job(self):
        req = native.split_request('Сделай заглавными: привет')
        self.engine._reserve(req)
        self.now += native.LEASE + 1
        other = native.NativeLearning(self.path, self.backend, lambda: self.now)
        self.assertEqual(other.learn(req.original)['learning']['status'], 'cooldown')
        self.assertEqual(self.backend.proposals, [])

    def test_concurrent_generation_does_not_start(self):
        self.backend.mutex.acquire()
        try:
            self.assertEqual(self.engine.learn('Сделай заглавными: привет')['learning']['status'], 'busy')
        finally:
            self.backend.mutex.release()
        self.assertEqual(self.backend.proposals, [])

    def test_database_serializes_different_engine_instances(self):
        self.engine._reserve(native.split_request('Сделай заглавными: привет'))
        other = native.NativeLearning(self.path, Backend(), lambda: self.now)
        result = other.learn('Посчитай абсолютное значение: -1')
        self.assertEqual(result['learning']['status'], 'busy')

    def test_missing_key_is_setup_not_learning(self):
        self.backend.provider_ready = False
        self.assertEqual(self.engine.learn('Сделай заглавными: привет')['learning']['status'], 'needs_setup')
        self.assertEqual(self.backend.proposals, [])

    def test_new_adapter_is_not_installed(self):
        self.backend.next_kind = 'proposal'
        result = self.engine.learn('Подключи неизвестное устройство')
        self.assertEqual(result['learning']['status'], 'needs_adapter')
        self.assertEqual(self.backend.items[1]['state'], 'tested')
        self.assertEqual(self.backend.invocations, [])

    def test_policy_rejection_does_not_activate(self):
        self.backend.bad = True
        self.engine.learn('Сделай заглавными: привет')
        self.assertEqual(self.backend.items[1]['state'], 'tested')
        self.assertEqual(self.backend.invocations, [])

    def test_no_claim_of_verified_adapter_execution(self):
        self.backend.next_kind = 'recipe'
        result = self.engine.learn('Покажи состояние устройства')
        self.assertEqual(result['learning']['status'], 'tool_result')
        self.assertFalse(result['learning']['answered'])

    def test_recipe_cannot_silently_drop_separate_input(self):
        self.backend.next_kind = 'recipe'
        result = self.engine.learn('Покажи состояние устройства: данные')
        self.assertEqual(result['learning']['status'], 'unconfirmed')
        self.assertEqual(self.backend.invocations, [])

    def test_secret_does_not_reach_generator(self):
        self.assertEqual(self.engine.learn('Обработай пароль: пример')['learning']['status'], 'sensitive')
        self.assertEqual(self.backend.proposals, [])

    def test_empty_data_is_not_missing_data(self):
        result = self.engine.learn('Сделай заглавными:')
        self.assertEqual(result['learning']['status'], 'answered')
        self.assertEqual(self.backend.invocations[0][1], '')

    def test_plain_chat_never_trains(self):
        self.assertEqual(self.engine.learn('Привет')['learning']['status'], 'not_task')
        self.assertEqual(self.backend.proposals, [])

    def test_task_refusal_can_continue(self):
        result = self.engine.after_answer('Сделай заглавными: привет',
                                         {'reply':'Я пока не умею выполнять такое преобразование.'})
        self.assertEqual(result['reply'], 'ПРИВЕТ')

    def test_not_every_refusal_is_a_gap(self):
        for text in ('Не могу помочь с этим.', 'Нет доступа к файлам.',
                     'Я пока не умею это делать без разрешения Android.',
                     'Не умею обработать запрос из-за сети.',
                     'В тексте герой говорит «не умею».'):
            result = {'reply':text}
            self.assertIs(self.engine.after_answer('Сделай заглавными: привет', result), result)
        self.assertEqual(self.backend.proposals, [])

    def test_no_automatic_replay_after_side_effect_or_pending(self):
        for more in ({'actions':[{'name':'note_add'}]}, {'pending':[1]}, {'learning':{'status':'failed'}}):
            result = dict(reply='Не умею это делать', **more)
            self.assertIs(self.engine.after_answer('Сделай что-то', result), result)
        self.assertEqual(self.backend.proposals, [])

    def test_status_control_and_unknown_control(self):
        self.assertEqual(self.engine.control('статус автообучения')['learning']['status'], 'enabled')
        self.assertIsNone(self.engine.control('Привет'))

    def test_state_file_permissions(self):
        self.engine.enabled()
        self.assertEqual(self.path.stat().st_mode & 0o777, 0o600)

    def test_symlink_state_refused(self):
        target = Path(self.tmp.name)/'target'
        target.write_text('do-not-touch')
        self.path.symlink_to(target)
        self.assertEqual(self.engine.learn('Сделай заглавными: привет')['learning']['status'], 'failed')
        self.assertEqual(target.read_text(), 'do-not-touch')


class ParseTests(unittest.TestCase):
    def test_wakeword_and_polite_task(self):
        self.assertTrue(native.is_task_request('Нексус, пожалуйста сделай это'))
        self.assertTrue(native.is_task_request('Можешь ли ты преобразовать текст'))
        self.assertFalse(native.is_task_request('Как сделать это?'))
        self.assertFalse(native.is_task_request('Не отправляй ничего'))

    def test_quotes_and_colon_have_same_operation(self):
        self.assertEqual(native.split_request('Сделай заглавными «привет»'),
                         native.split_request('Сделай заглавными: привет'))

    def test_colon_without_space_for_numeric_data(self):
        self.assertEqual(native.split_request('Посчитай модуль:123').data, '123')

    def test_url_and_time_not_data_separators(self):
        self.assertIsNone(native.split_request('Открой https://example.invalid').data)
        self.assertIsNone(native.split_request('Создай напоминание на 15:00').data)

    def test_length_bound(self):
        with self.assertRaises(ValueError):
            native.split_request('Сделай ' + 'x'*400)
        with self.assertRaises(ValueError):
            native.split_request('Сделай: ' + 'x'*4000)


class MigrationTests(unittest.TestCase):
    def setUp(self):
        spec = importlib.util.spec_from_file_location('native_migration', ROOT/'scripts/integrate_native_learning.py')
        self.migration = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.migration)

    def test_hash_checked_idempotent_migration(self):
        source = 'x = 1\n'
        raw = source.encode()
        digest = hashlib.sha1(b'blob '+str(len(raw)).encode()+b'\0'+raw).hexdigest()
        edits = [('x = 1\n', 'x = 2  # NATIVE_MARK\n', 'NATIVE_MARK')]
        changed = self.migration.apply(source, digest, edits)
        self.assertEqual(self.migration.apply(changed, digest, edits), changed)
        with self.assertRaises(RuntimeError):
            self.migration.apply('x = 3\n', digest, edits)
        with self.assertRaises(RuntimeError):
            self.migration.apply('x = 4  # NATIVE_MARK\n', digest, edits)


# The complete upstream source tree is used by GitHub CI. Local standalone
# execution deliberately reports these as skipped instead of pretending to
# exercise files which are not present in a partial checkout.
@unittest.skipUnless((BRIDGE/'app.py').exists() and (BRIDGE/'planner.py').exists(), 'requires full source tree')
class IntegrationTests(unittest.TestCase):
    @staticmethod
    def function(path, name, namespace):
        tree = ast.parse(path.read_text())
        node = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == name)
        exec(compile(ast.Module(body=[node], type_ignores=[]), str(path), 'exec'), namespace)
        return namespace[name]

    def planner(self, responses):
        learner = SimpleNamespace(PLANNER_HINT='', learn=Mock(return_value=native.response('RESULT','answered')),
                                  after_answer=Mock(side_effect=lambda text, result: result))
        env = dict(os=os, time=time, ProviderError=RuntimeError, SYSTEM='',
                   _learning20_store=SimpleNamespace(safe_context_for=lambda text:''),
                   nexus_r89_fast=lambda:False, MAX_CALLS=4, FUNCTIONS=[],
                   TOOL_DESCRIPTIONS={'learn_capability':'', 'battery':'', 'extension_request':'', 'evolution_draft':''},
                   REQUIRES_APPROVAL=set(), post_json=Mock(side_effect=responses),
                   execute=Mock(return_value='battery result'), native_learning=learner)
        return self.function(BRIDGE/'planner.py', 'plan', env), env, learner

    def test_original_request_not_model_arguments(self):
        data = {'candidates':[{'content':{'parts':[{'functionCall':{
            'name':'learn_capability','args':{'argument':'DIFFERENT TASK'}}}]}}]}
        plan, env, learner = self.planner([data])
        with patch.dict(os.environ, {'GEMINI_API_KEY':'test-only'}):
            result = plan('Сделай заглавными: привет', [])
        learner.learn.assert_called_once_with('Сделай заглавными: привет')
        env['execute'].assert_not_called()
        self.assertEqual(result['reply'], 'RESULT')
        self.assertEqual(env['post_json'].call_count, 1)

    def test_no_learning_after_prior_action(self):
        replies = [{'candidates':[{'content':{'parts':[{'functionCall':{
            'name':name,'args':{'argument':''}}}]}}]} for name in ('battery','learn_capability')]
        plan, env, learner = self.planner(replies)
        with patch.dict(os.environ, {'GEMINI_API_KEY':'test-only'}):
            result = plan('Покажи состояние и сделай ещё что-то', [])
        learner.learn.assert_not_called()
        env['execute'].assert_called_once()
        self.assertEqual(result['learning']['status'], 'partial')

    def test_legacy_voice_does_not_skip_task_routing(self):
        fn = self.function(BRIDGE/'app.py', '_nexus_fast_chat', {'_native_learning':native})
        self.assertFalse(fn('Переведи в верхний регистр: привет', 'placeholder'))
        self.assertFalse(fn('Выключи автообучение', 'placeholder'))
        self.assertTrue(fn('Что такое атом', 'Атом — ...'))

    def test_migration_is_complete_and_idempotent(self):
        spec = importlib.util.spec_from_file_location('migration_ci', ROOT/'scripts/integrate_native_learning.py')
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        for path, edits in ((BRIDGE/'app.py', module.APP), (BRIDGE/'planner.py', module.PLANNER)):
            text = path.read_text()
            self.assertEqual(module.apply(text, 'unused-after-integration', edits), text)


if __name__ == '__main__':
    unittest.main()
