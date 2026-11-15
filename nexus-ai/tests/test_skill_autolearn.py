"""Autoactivation is limited to tested pure functions and read-only recipes."""
from __future__ import annotations
import io
import json
from pathlib import Path
import sys
import tempfile
import types
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'android_bridge'))
import skill_forge as forge
import skill_autolearn as auto
import skill_academy as academy


def pure(source='def solve(value):\n    return value.upper()\n', tests=None):
    return dict(kind='pure', name='upper_text', summary='Верхний регистр',
                source=source, steps=[], tests=tests or [
                    dict(input='hello', expected='HELLO'), dict(input='abc', expected='ABC')])


def recipe():
    return dict(kind='recipe', name='read_status', summary='Заряд и заметки',
                source='', tests=[], steps=[dict(tool='battery', argument=''),
                                           dict(tool='notes_list', argument='')])


def proposal():
    return dict(kind='proposal', name='new_adapter', summary='Проект адаптера',
                source='void setup() {}', language='cpp', steps=[], tests=[])


def response(payload):
    return {'candidates': [{'content': {'parts': [{'text': json.dumps(payload)}]}}]}


class AutoLearningTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        for obj, attr, name in ((forge, 'ROOT', 'forge.sqlite3'),
                                (academy, 'DB_PATH', 'academy.sqlite3')):
            p = patch.object(obj, attr, Path(self.tmp.name) / name)
            p.start(); self.addCleanup(p.stop)
        p = patch.dict('os.environ', {'GEMINI_API_KEY': 'fake', 'GEMINI_MODEL': 'gemini-test'})
        p.start(); self.addCleanup(p.stop)

    def test_new_pure_activates_and_answers_without_confirmation(self):
        with patch.object(forge, 'post_json', return_value=response(pure())) as network, \
             patch('builtins.input', side_effect=AssertionError('No second approval')):
            result = auto.learn_and_answer('Верхний регистр: привет')
        self.assertTrue(result.active)
        self.assertTrue(result.answered)
        self.assertIn('ПРИВЕТ', result.reply)
        self.assertIn('автоматически', result.reply)
        draft = forge._get(result.skill_id)
        self.assertEqual(draft['state'], 'active')
        self.assertEqual(draft['digest'], draft['approved_digest'])
        self.assertEqual(network.call_count, 1)

    def test_recipe_runs_once_only_after_activation(self):
        executed = []
        def execute(tool, argument):
            self.assertEqual(forge.list_drafts()[0]['state'], 'active')
            executed.append((tool, argument))
            return '75%' if tool == 'battery' else 'Список заметок'
        with patch.object(forge, 'post_json', return_value=response(recipe())) as network, \
             patch.dict(sys.modules, {'agent': types.SimpleNamespace(execute=execute)}):
            result = auto.learn_and_answer('Покажи батарею и заметки')
        self.assertTrue(result.answered)
        self.assertIn('75%', result.reply)
        self.assertEqual(executed, [('battery', ''), ('notes_list', '')])
        self.assertEqual(network.call_count, 1)
        self.assertNotIn('75%', str(network.call_args))

    def test_repeated_learning_reuses_active_skill_without_cloud(self):
        with patch.object(forge, 'post_json', return_value=response(pure())) as network:
            first = auto.learn_and_answer('Регистр: hello')
            second = auto.learn_and_answer('Регистр: hello')
        self.assertEqual(first.skill_id, second.skill_id)
        self.assertIn('HELLO', second.reply)
        self.assertEqual(network.call_count, 1)
        self.assertEqual(len(forge.list_drafts()), 1)

    def test_voice_learn_answers_original_task_and_resolves_goal(self):
        academy.note_refusal('Регистр: hello', 'Не умею пока.')
        with patch.object(forge, 'post_json', return_value=response(pure())):
            reply = academy.handle('научись')
        self.assertIn('HELLO', reply)
        self.assertIsNone(academy.pending_goal())
        self.assertIn('WORLD', academy.handle('навык 1: world'))

    def test_history_refusal_can_learn_and_answer_immediately(self):
        history = [('user', 'Регистр: hello'), ('assistant', 'Не могу это сделать.')]
        with patch.object(forge, 'post_json', return_value=response(pure())):
            reply = academy.handle('научись', history)
        self.assertIn('HELLO', reply)
        self.assertIsNone(academy.pending_goal())

    def test_no_automatic_activation_of_old_drafts(self):
        old = forge.store('Старый модуль: hello', pure())
        with patch.object(forge, 'post_json', return_value=response(pure())):
            result = auto.learn_and_answer('Новый модуль: abc')
        self.assertTrue(result.active)
        self.assertEqual(forge._get(old['id'])['state'], 'tested')

    def test_low_level_propose_stays_draft_only(self):
        with patch.object(forge, 'post_json', return_value=response(pure())):
            draft = forge.propose('Регистр: abc')
        self.assertEqual(draft['state'], 'tested')
        self.assertIsNone(forge.run_intent('Регистр: abc'))

    def test_failed_tests_do_not_activate_or_run(self):
        wrong = pure(tests=[dict(input='a', expected='wrong'), dict(input='b', expected='B')])
        with patch.object(forge, 'post_json', return_value=response(wrong)) as network, \
             patch.object(forge, 'approve') as activate, patch.object(forge, '_invoke') as invoke:
            result = auto.learn_and_answer('Регистр: hello')
        self.assertFalse(result.active)
        self.assertFalse(result.answered)
        self.assertIn('черновик', result.reply)
        self.assertEqual(network.call_count, 3)
        activate.assert_not_called(); invoke.assert_not_called()

    def test_repair_then_autoactivate_and_answer(self):
        wrong = pure(source='def solve(value):\n    return value.lower()\n')
        with patch.object(forge, 'post_json', side_effect=[response(wrong), response(pure())]) as network:
            result = auto.learn_and_answer('Регистр: hello')
        self.assertTrue(result.answered)
        self.assertIn('HELLO', result.reply)
        self.assertEqual(network.call_count, 2)

    def test_source_proposals_never_activate(self):
        with patch.object(forge, 'post_json', return_value=response(proposal())), \
             patch.object(forge, 'approve') as activate, patch.object(forge, '_invoke') as invoke:
            result = auto.learn_and_answer('Измени прошивку M5')
        self.assertFalse(result.active)
        self.assertIn('Код не запускался', result.reply)
        activate.assert_not_called(); invoke.assert_not_called()

    def test_side_effect_recipe_is_denied(self):
        for tool in ('sms_send', 'torch_on', 'shell', 'firmware_flash', 'contacts_list'):
            bad = recipe(); bad['steps'] = [dict(tool=tool, argument='')]
            with self.subTest(tool=tool), \
                 patch.object(forge, 'post_json', return_value=response(bad)), \
                 patch.object(forge, 'approve') as activate, patch.object(forge, '_invoke') as invoke:
                result = auto.learn_and_answer('Освой неизвестную команду')
                self.assertFalse(result.active)
                activate.assert_not_called(); invoke.assert_not_called()

    def test_hidden_forbidden_code_is_denied(self):
        bad = pure(source='def solve(value):\n    return value.upper() if value else __import__("os")\n')
        with patch.object(forge, 'post_json', return_value=response(bad)), \
             patch.object(forge, 'approve') as activate:
            result = auto.learn_and_answer('Регистр: hello')
        self.assertFalse(result.active); activate.assert_not_called()

    def test_missing_data_activates_but_does_not_transform_instruction(self):
        with patch.object(forge, 'post_json', return_value=response(pure())), \
             patch.object(forge, '_invoke') as invoke:
            result = auto.learn_and_answer('Создай преобразование регистра')
        self.assertTrue(result.active)
        self.assertFalse(result.answered)
        self.assertIn('нужны данные', result.reply)
        invoke.assert_not_called()

    def test_numeric_input_keeps_its_type(self):
        payload = pure(source='def solve(value):\n    return value * 2\n',
                       tests=[dict(input=2, expected=4), dict(input=0, expected=0)])
        with patch.object(forge, 'post_json', return_value=response(payload)):
            result = auto.learn_and_answer('Удвой число: 21')
        self.assertTrue(result.answered)
        self.assertIn('42', result.reply)

    def test_failed_input_is_not_reported_as_completed(self):
        payload = pure(source='def solve(value):\n    return value / 2\n',
                       tests=[dict(input=2, expected=1.0), dict(input=4, expected=2.0)])
        with patch.object(forge, 'post_json', return_value=response(payload)):
            result = auto.learn_and_answer('Раздели: hello')
        self.assertTrue(result.active)
        self.assertFalse(result.answered)
        self.assertIn('не выполнен', result.reply)

    def test_android_failure_is_not_retried_or_claimed_success(self):
        fake = types.SimpleNamespace(execute=lambda *args: (_ for _ in ()).throw(OSError('private data')))
        with patch.object(forge, 'post_json', return_value=response(recipe())), \
             patch.dict(sys.modules, {'agent': fake}):
            result = auto.learn_and_answer('Покажи заряд и заметки')
        self.assertTrue(result.active)
        self.assertFalse(result.answered)
        self.assertNotIn('private data', result.reply)
        self.assertIn('автоматического повтора нет', result.reply)

    def test_provider_failure_preserves_goal_and_does_not_leak_error(self):
        academy.note_refusal('Регистр: hello', 'Не могу.')
        with patch.object(forge, 'post_json', side_effect=RuntimeError('PRIVATE_CLOUD_ERROR')):
            result = academy.handle('научись')
        self.assertIn('Сохранил задачу', result)
        self.assertNotIn('PRIVATE_CLOUD_ERROR', result)
        self.assertIsNotNone(academy.pending_goal())
        self.assertEqual(forge.list_drafts(), [])

    def test_secret_task_never_reaches_network(self):
        with patch.object(forge, 'post_json') as network:
            result = auto.learn_and_answer('Запомни пароль: never-send-this')
        self.assertFalse(result.active)
        network.assert_not_called()

    def test_tampered_database_blocks_automatic_activation(self):
        draft = forge.store('Регистр: abc', pure())
        with forge._db() as c:
            c.execute('UPDATE drafts SET digest=? WHERE id=?', ('0' * 64, draft['id']))
        with patch.object(forge, 'propose', return_value=draft), patch.object(forge, '_invoke') as invoke:
            result = auto.learn_and_answer('Регистр: abc')
        self.assertFalse(result.active); invoke.assert_not_called()

    def test_other_task_draft_cannot_be_activated(self):
        draft = forge.store('Другая задача: abc', pure())
        with patch.object(forge, 'propose', return_value=draft), patch.object(forge, 'approve') as activate:
            result = auto.learn_and_answer('Регистр: abc')
        self.assertFalse(result.active); activate.assert_not_called()

    def test_revoke_between_activation_and_execution_blocks_run(self):
        real_approve = forge.approve
        def revoke(identifier, **kwargs):
            draft = real_approve(identifier, **kwargs)
            forge.reject(identifier)
            return draft
        with patch.object(forge, 'post_json', return_value=response(pure())), \
             patch.object(forge, 'approve', side_effect=revoke), patch.object(forge, '_invoke') as invoke:
            result = auto.learn_and_answer('Регистр: abc')
        self.assertFalse(result.active); invoke.assert_not_called()

    def test_active_limit_is_respected(self):
        with patch.object(forge, 'MAX_ACTIVE', 0), patch.object(forge, 'post_json', return_value=response(pure())):
            result = auto.learn_and_answer('Регистр: abc')
        self.assertFalse(result.active)
        self.assertEqual(forge.list_drafts()[0]['state'], 'tested')

    def test_busy_learning_does_not_start_a_second_generation(self):
        auto.LEARNING_LOCK.acquire()
        try:
            with patch.object(forge, 'post_json') as network:
                result = auto.learn_and_answer('Регистр: hello')
            self.assertIn('уже выполняется', result.reply)
            network.assert_not_called()
        finally:
            auto.LEARNING_LOCK.release()

    def test_failure_releases_learning_lock(self):
        with patch.object(forge, 'post_json', side_effect=RuntimeError('offline')):
            auto.learn_and_answer('Регистр: hello')
        self.assertTrue(auto.LEARNING_LOCK.acquire(blocking=False))
        auto.LEARNING_LOCK.release()

    def test_sms_keeps_explicit_final_confirmation(self):
        with patch.object(academy.shutil, 'which', return_value='/mock/termux-api'), \
             patch.object(academy, '_run', return_value=(True, json.dumps([
                 dict(name='Папа', number='+79990000001')], ensure_ascii=False))) as android, \
             patch.object(forge, 'post_json') as network:
            academy.handle('Напиши папе')
            academy.handle('научись')
            reply = academy.handle('Я дома')
        self.assertIn('подтверждаю отправку', reply)
        self.assertTrue(all(call.args[0][0] != 'termux-sms-send' for call in android.call_args_list))
        network.assert_not_called()


if __name__ == '__main__':
    unittest.main()
