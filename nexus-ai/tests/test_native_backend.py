"""Real Forge/interpreter integration, with the provider call replaced offline."""
import copy
import importlib.util
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT/'nexus-ai/android_bridge'))
import native_learning as native


@unittest.skipUnless(importlib.util.find_spec('skill_forge') and importlib.util.find_spec('skill_autolearn'),
                     'requires complete upstream source')
class RealBackendTests(unittest.TestCase):
    def setUp(self):
        import skill_forge as forge
        self.forge = forge
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.env = patch.dict(os.environ, {'GEMINI_API_KEY':'test-only', 'NEXUS_NATIVE_LEARNING':'1'})
        self.env.start()
        self.addCleanup(self.env.stop)
        self.root_patch = patch.object(forge, 'ROOT', Path(self.tmp.name)/'forge.sqlite3')
        self.root_patch.start()
        self.addCleanup(self.root_patch.stop)
        self.engine = native.NativeLearning(Path(self.tmp.name)/'native.sqlite3')
        self.payload = {'kind':'pure','name':'native_upper','summary':'Преобразование регистра',
                        'source':'def solve(value):\n    return value.upper()', 'steps':[],
                        'tests':[{'input':'test','expected':'TEST'}, {'input':'','expected':''}]}
        self.proposer = patch.object(forge, 'propose', side_effect=self.propose)
        self.mock_propose = self.proposer.start()
        self.addCleanup(self.proposer.stop)

    def propose(self, key):
        return self.forge.store(key, copy.deepcopy(self.payload))

    def test_real_validation_activation_and_interpreter(self):
        result = self.engine.learn('Сделай заглавными: привет')
        self.assertEqual(result['reply'], 'ПРИВЕТ')
        self.assertTrue(result['learning']['answered'])
        draft = self.forge._get(result['learning']['skill_id'])
        self.assertEqual(draft['state'], 'active')
        self.assertEqual(draft['digest'], draft['approved_digest'])

    def test_real_reuse_and_separate_input(self):
        self.engine.learn('Сделай заглавными: привет')
        result = self.engine.reuse('Сделай заглавными: мир')
        self.assertEqual(result['reply'], 'МИР')
        self.mock_propose.assert_called_once_with(native.NAMESPACE+'Сделай заглавными')

    def test_real_followup_without_repeating_task(self):
        self.assertEqual(self.engine.learn('Сделай заглавными')['learning']['status'], 'needs_input')
        self.assertEqual(self.engine.reuse('данные: привет')['reply'], 'ПРИВЕТ')

    def test_actual_data_with_colon_is_preserved(self):
        self.engine.learn('Сделай заглавными: a:b')
        self.assertEqual(self.engine.reuse('Сделай заглавными: c:d')['reply'], 'C:D')

    def test_revoked_real_skill_is_not_resurrected(self):
        result = self.engine.learn('Сделай заглавными: привет')
        self.forge.reject(result['learning']['skill_id'])
        self.assertEqual(self.engine.learn('Сделай заглавными: мир')['learning']['status'], 'revoked')
        self.assertEqual(self.mock_propose.call_count, 1)

    def test_different_goal_is_rejected_before_activation(self):
        self.mock_propose.side_effect = lambda key: self.propose(native.NAMESPACE+'Другая задача')
        with patch.object(self.forge, 'approve', wraps=self.forge.approve) as approve:
            result = self.engine.learn('Сделай заглавными: привет')
        self.assertEqual(result['learning']['status'], 'failed')
        approve.assert_not_called()

    def test_tampering_is_detected_before_execution(self):
        result = self.engine.learn('Сделай заглавными: привет')
        identifier = result['learning']['skill_id']
        changed = copy.deepcopy(self.payload)
        changed['source'] = 'def solve(value):\n    return value.lower()'
        with self.forge._db() as c:
            c.execute('UPDATE drafts SET payload=? WHERE id=?', (json.dumps(changed), identifier))
        with patch.object(self.forge, '_invoke', wraps=self.forge._invoke) as invoke:
            result = self.engine.reuse('Сделай заглавными: МИР')
        self.assertIsNone(result)  # invalid cached capability is not executed
        invoke.assert_not_called()

    def test_side_effect_recipe_cannot_auto_activate(self):
        def malicious_candidate(key):
            draft = self.propose(key)
            draft['payload'] = {'kind':'recipe', 'name':'bad_recipe', 'summary':'No',
                                'source':'', 'tests':[], 'steps':[{'tool':'notify','argument':'No'}]}
            draft['digest'] = self.forge._digest(key, draft['payload'])
            return draft
        self.mock_propose.side_effect = malicious_candidate
        with patch.object(self.forge, 'approve', wraps=self.forge.approve) as approve:
            result = self.engine.learn('Покажи новое состояние')
        self.assertEqual(result['learning']['status'], 'failed')
        approve.assert_not_called()


if __name__ == '__main__':
    unittest.main()
