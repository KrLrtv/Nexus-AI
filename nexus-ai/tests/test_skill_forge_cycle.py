"""Lifecycle regression tests; cloud and Android calls are mocked, not simulated hardware tests."""
from __future__ import annotations
import concurrent.futures
import copy
import json
import os
from pathlib import Path
import sqlite3
import sys
import tempfile
import types
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]/'android_bridge'))
import skill_forge as f


def module(code='value.upper()', tests=None):
    return {'kind': 'pure', 'name': 'upper_text', 'summary': 'Обработка текста',
            'source': 'def solve(value):\n    return ' + code + '\n', 'steps': [],
            'tests': tests if tests is not None else [{'input': 'a', 'expected': 'A'}, {'input': 'b', 'expected': 'B'}]}


def response(payload):
    return {'candidates': [{'content': {'parts': [{'text': json.dumps(payload, ensure_ascii=False)}]}}]}


class CycleTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)/'forge.sqlite3'
        self.addCleanup(patch.stopall)
        patch.object(f, 'ROOT', self.root).start()
        patch.dict(os.environ, {'GEMINI_API_KEY': 'test-placeholder', 'GEMINI_MODEL': 'test-model'}).start()

    def draft(self, intent='Преобразуй текст: hello', **kwargs):
        return f.store(intent, module(**kwargs))

    def active(self, intent='Преобразуй текст: hello', **kwargs):
        return f.approve(self.draft(intent, **kwargs)['id'])

    def test_generates_once_without_autoactivation(self):
        with patch.object(f, 'post_json', return_value=response(module())) as api:
            d = f.propose('Преобразуй текст: hello')
        self.assertEqual(d['state'], 'tested')
        self.assertIsNone(f.run_intent(d['intent']))
        self.assertEqual(api.call_count, 1)
        self.assertEqual(api.call_args.kwargs['timeout'], 30)

    def test_repairs_failed_implementation(self):
        with patch.object(f, 'post_json', side_effect=[response(module('value.lower()')), response(module())]) as api:
            d = f.propose('Преобразуй текст: hello')
        self.assertEqual(d['state'], 'tested')
        self.assertEqual([x['passed'] for x in d['attempts']], [False, True])
        self.assertEqual(api.call_count, 2)
        self.assertIn('Тест 1', api.call_args.args[1]['contents'][0]['parts'][0]['text'])

    def test_model_cannot_cheat_by_rewriting_expected_outputs(self):
        cheating = module('value.lower()', [{'input': 'a', 'expected': 'a'}, {'input': 'b', 'expected': 'b'}])
        with patch.object(f, 'post_json', side_effect=[response(module('value.lower()')), response(cheating), response(cheating)]) as api:
            d = f.propose('Верхний регистр')
        self.assertEqual(api.call_count, 3)
        self.assertEqual(d['state'], 'review')
        self.assertEqual(d['payload']['tests'][0]['expected'], 'A')
        with self.assertRaises(ValueError): f.approve(d['id'])

    def test_tests_frozen_even_when_first_code_has_syntax_error(self):
        invalid = module('(')
        cheating = module('value.lower()', [{'input': 'a', 'expected': 'a'}, {'input': 'b', 'expected': 'b'}])
        with patch.object(f, 'post_json', side_effect=[response(invalid), response(cheating), response(module())]):
            d = f.propose('Верхний регистр')
        self.assertEqual(d['state'], 'tested')
        self.assertEqual([x['passed'] for x in d['attempts']], [False, False, True])

    def test_acceptance_tests_persist_and_block_approval(self):
        owner = [{'input': 'c', 'expected': 'CUSTOM'}]
        with patch.object(f, 'post_json', return_value=response(module())) as api:
            d = f.propose('Верхний регистр', acceptance_tests=owner)
        self.assertEqual(api.call_count, 3)
        self.assertEqual(d['state'], 'review')
        self.assertEqual(d['payload']['acceptance_tests'], owner)
        with self.assertRaises(ValueError): f.approve(d['id'])
        self.assertFalse(f.retest(d['id'])['report']['deployable'])

    def test_acceptance_success_is_rechecked(self):
        with patch.object(f, 'post_json', return_value=response(module())):
            d = f.propose('Верхний регистр', acceptance_tests=[{'input': 'c', 'expected': 'C'}])
        f.approve(d['id'])
        self.assertTrue(f.retest(d['id'])['report']['checks'][-1]['passed'])
        self.assertEqual(f.run_intent(f'навык {d["id"]}: world'), 'Освоенный навык: WORLD')

    def test_bad_json_has_three_attempt_limit_and_no_draft(self):
        obj = {'candidates': [{'content': {'parts': [{'text': 'not json'}]}}]}
        with patch.object(f, 'post_json', return_value=obj) as api:
            with self.assertRaises(ValueError): f.propose('Обработай текст')
        self.assertEqual(api.call_count, 3)
        self.assertFalse(self.root.exists())

    def test_wrong_json_shape_retries(self):
        with patch.object(f, 'post_json', side_effect=[response([]), response('wrong'), response(module())]):
            d = f.propose('Обработай текст')
        self.assertEqual(d['state'], 'tested')
        self.assertEqual(len(d['attempts']), 3)

    def test_provider_error_is_not_retried_or_leaked_into_draft(self):
        with patch.object(f, 'post_json', side_effect=f.ProviderError('offline')) as api:
            with self.assertRaises(f.ProviderError): f.propose('Обработай текст')
        self.assertEqual(api.call_count, 1)
        self.assertFalse(self.root.exists())

    def test_secrets_rejected_before_network_and_storage(self):
        with patch.object(f, 'post_json') as api:
            for text in ('Запомни пароль: secret', 'API_KEY=secret', 'Bearer secret', 'github_pat_examplevalue'):
                with self.subTest(text=text), self.assertRaises(ValueError): f.propose(text)
        api.assert_not_called()
        self.assertFalse(self.root.exists())

    def test_secret_response_is_not_sent_back_to_model(self):
        bad = module(); bad['summary'] = 'Bearer sensitive-placeholder'
        with patch.object(f, 'post_json', side_effect=[response(bad), response(module())]) as api:
            f.propose('Обработай текст')
        self.assertNotIn('sensitive-placeholder', str(api.call_args.args))

    def test_proposal_is_not_silently_installed_or_retried(self):
        p = {'kind': 'proposal', 'name': 'new_adapter', 'summary': 'Только ревью', 'source': 'void setup() {}', 'language': 'cpp', 'steps': [], 'tests': []}
        with patch.object(f, 'post_json', return_value=response(p)) as api:
            d = f.propose('Измени код стика')
        self.assertEqual(api.call_count, 1)
        self.assertEqual(d['state'], 'review')
        with self.assertRaises(ValueError): f.approve(d['id'])

    def test_original_request_passes_only_argument_after_colon(self):
        d = self.active()
        self.assertEqual(f.run_intent(d['intent']), 'Освоенный навык: HELLO')

    def test_voice_reuse_by_number_and_name(self):
        d = self.active()
        self.assertEqual(f.run_intent(f'навык {d["id"]}: привет'), 'Освоенный навык: ПРИВЕТ')
        self.assertEqual(f.run_intent('модуль upper_text: world'), 'Освоенный навык: WORLD')

    def test_numeric_argument_is_typed(self):
        d = self.active('Удвой число', code='value * 2', tests=[{'input': 2, 'expected': 4}, {'input': 3, 'expected': 6}])
        self.assertEqual(f.run_intent(f'навык {d["id"]}: 7'), 'Освоенный навык: 14')

    def test_ambiguous_names_require_id(self):
        self.active('Первая задача'); self.active('Вторая задача')
        self.assertIn('неоднозначно', f.run_intent('навык upper_text: test'))

    def test_digest_rejects_payload_tampering(self):
        d = self.active()
        with f._db() as c:
            c.execute('UPDATE drafts SET payload=? WHERE id=?', (json.dumps(module('value.lower()')), d['id']))
        with self.assertRaises(ValueError): f.retest(d['id'])
        self.assertNotIn('Освоенный навык:', f.run_intent(d['intent']))

    def test_digest_checked_again_at_approval(self):
        d = self.draft()
        with self.assertRaises(ValueError): f.approve(d['id'], expected_digest='0'*64)
        self.assertEqual(f._get(d['id'])['state'], 'tested')

    def test_valid_digest_approval_is_idempotent(self):
        d = self.draft()
        f.approve(d['id'], d['digest'])
        self.assertEqual(f.approve(d['id'])['state'], 'active')
        self.assertIsNone(f._get(d['id'])['previous_id'])

    def test_rollback_restores_only_previous_approved_version(self):
        old = self.active()
        new = self.active(code='value.lower()', tests=[{'input': 'A', 'expected': 'a'}, {'input': 'B', 'expected': 'b'}])
        self.assertEqual(f._get(old['id'])['state'], 'superseded')
        self.assertEqual(f.run_intent(new['intent']), 'Освоенный навык: hello')
        self.assertIn('Восстановлена', f.run_intent(f'откати навык {new["id"]}'))
        self.assertEqual(f.run_intent(old['intent']), 'Освоенный навык: HELLO')
        self.assertEqual(f._get(new['id'])['state'], 'rejected')

    def test_cannot_rollback_without_approved_predecessor(self):
        d = self.active()
        with self.assertRaises(ValueError): f.rollback(d['id'])

    def test_rejected_predecessor_cannot_be_restored(self):
        old = self.active(); new = self.active()
        f.reject(old['id'])
        with self.assertRaises(ValueError): f.rollback(new['id'])

    def test_retest_does_not_revive_rejected_skill(self):
        d = self.active(); f.reject(d['id'])
        self.assertEqual(f.retest(d['id'])['state'], 'rejected')
        with self.assertRaises(ValueError): f.approve(d['id'])
        self.assertIsNone(f.run_intent(d['intent']))

    def test_old_active_database_requires_new_explicit_approval(self):
        with sqlite3.connect(self.root) as c:
            c.execute('''CREATE TABLE drafts(id INTEGER PRIMARY KEY,intent TEXT,intent_norm TEXT,
                name TEXT,kind TEXT,summary TEXT,payload TEXT,state TEXT,report TEXT,created INTEGER,activated INTEGER DEFAULT 0)''')
            p = module()
            c.execute('INSERT INTO drafts VALUES(1,?,?,?,?,?,?,?,?,0,0)', ('старый навык', 'старый навык', p['name'], p['kind'], p['summary'], json.dumps(p), 'active', '{}'))
        self.assertIsNone(f.run_intent('старый навык'))
        self.assertEqual(f._get(1)['state'], 'tested')
        self.assertEqual(f.approve(1)['state'], 'active')
        self.assertEqual(self.root.stat().st_mode & 0o777, 0o600)

    def test_draft_limit_preserves_active_and_single_predecessor(self):
        old = self.active(); new = self.active()
        for i in range(60): self.draft(f'Задача {i}')
        with f._db() as c: count = c.execute('SELECT count(*) FROM drafts').fetchone()[0]
        self.assertLessEqual(count, f.MAX_DRAFTS+2)
        self.assertEqual(f._get(old['id'])['state'], 'superseded')
        self.assertEqual(f._get(new['id'])['state'], 'active')

    def test_repeated_updates_keep_one_rollback_version(self):
        for _ in range(60): self.active()
        with f._db() as c: count = c.execute('SELECT count(*) FROM drafts').fetchone()[0]
        self.assertEqual(count, 2)

    def test_concurrent_approvals_leave_one_active(self):
        ids = [self.draft()['id'] for _ in range(4)]
        with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
            list(pool.map(f.approve, ids))
        with f._db() as c:
            states = [x[0] for x in c.execute('SELECT state FROM drafts')]
        self.assertEqual(states.count('active'), 1)
        self.assertEqual(states.count('superseded'), 1)

    def test_long_task_keys_do_not_collide(self):
        a = self.active('а'*210+' первая'); b = self.active('а'*210+' вторая')
        self.assertEqual(f._get(a['id'])['state'], 'active')
        self.assertEqual(f._get(b['id'])['state'], 'active')

    def test_hidden_forbidden_branch_rejected(self):
        for expr in ("value if True else __import__('os').system('id')", 'value if True else open("a")',
                     'value if True else value.__class__', 'value if True else (lambda: 1)()'):
            with self.subTest(expr=expr), self.assertRaises(ValueError): f.store('Обработай текст', module(expr))

    def test_input_limits_and_arithmetic(self):
        for expr, value in (('value * 2', 10**9), ('value / 0', 1), ('value', 'x'*601), ('value*10000000', 'abc')):
            with self.subTest(expr=expr), self.assertRaises(ValueError): f.pure_run('def solve(value):\n    return '+expr, value)

    def test_readonly_recipe_dryrun_does_not_touch_phone(self):
        p = {'kind': 'recipe', 'name': 'read_status', 'summary': 'Чтение', 'source': '', 'steps': [{'tool': 'battery', 'argument': ''}], 'tests': []}
        execute = unittest.mock.Mock(return_value='MOCKED')
        with patch.dict(sys.modules, {'agent': types.SimpleNamespace(execute=execute)}):
            d = f.store('Покажи заряд', p); f.retest(d['id']); f.approve(d['id'])
            execute.assert_not_called()
            self.assertIn('MOCKED', f.run_intent(d['intent']))
        execute.assert_called_once_with('battery', '')

    def test_recipe_cannot_send_sms_or_flash(self):
        for tool in ('sms_send', 'shell', 'firmware_flash', 'contacts_list'):
            p = {'kind': 'recipe', 'name': 'bad_recipe', 'summary': 'Запрещено', 'source': '', 'steps': [{'tool': tool, 'argument': ''}], 'tests': []}
            with self.subTest(tool=tool), self.assertRaises(ValueError): f.store('Новая операция', p)

    def test_symlink_does_not_overwrite_file(self):
        target = Path(self.tmp.name)/'private'; target.write_text('unchanged')
        self.root.symlink_to(target)
        with self.assertRaises(RuntimeError): f.list_drafts()
        self.assertEqual(target.read_text(), 'unchanged')

    def test_no_cloud_during_approval_retest_and_use(self):
        d = self.draft()
        with patch.object(f, 'post_json') as api:
            f.approve(d['id']); f.retest(d['id']); f.run_intent(d['intent'])
        api.assert_not_called()

    def test_active_limit(self):
        with patch.object(f, 'MAX_ACTIVE', 1):
            self.active('Первая задача')
            with self.assertRaises(ValueError): self.active('Другая задача')
            self.active('Первая задача')  # replacing the one slot is allowed


if __name__ == '__main__':
    unittest.main()
