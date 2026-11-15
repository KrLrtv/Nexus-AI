"""NEXUS Skill Forge: no arbitrary Python execution, independent tests,
review-only firmware code, read-only tools, and explicit owner approval.
"""
from __future__ import annotations
import io
import json
import os
from pathlib import Path
import sys
import tempfile
import types
import unittest
from unittest.mock import patch

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/"android_bridge"))
import skill_forge as forge


def pure_module(code='def solve(value):\n    return value.upper()\n',tests=None):
    return {
        "kind":"pure","name":"uppercase_text","summary":"Преобразование текста",
        "source":code,"steps":[],
        "tests": tests or [{"input":"hello","expected":"HELLO"},
                           {"input":"Нексус","expected":"НЕКСУС"}]
    }


def recipe_module(steps=None):
    return {
        "kind":"recipe","name":"battery_note","summary":"Чтение заряда и заметок",
        "source":"","steps":steps or [
            {"tool":"battery","argument":""},
            {"tool":"notes_list","argument":""}],
        "tests":[]
    }


class ForgeTest(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.patch=patch.object(forge,"ROOT",Path(self.tmp.name)/"forge.sqlite3")
        self.patch.start()

    def tearDown(self):
        self.patch.stop()
        self.tmp.cleanup()

    def test_independent_pure_tests_pass_and_owner_can_approve(self):
        candidate=forge.store("Преобразуй этот текст заглавными буквами",
                              pure_module())
        self.assertEqual(candidate["state"],"tested")
        self.assertEqual(len(candidate["report"]["checks"]),2)
        self.assertTrue(candidate["report"]["tested"])
        self.assertEqual(forge.ROOT.stat().st_mode & 0o777,0o600)
        self.assertIsNone(forge.run_intent(candidate["intent"]))
        forge.approve(candidate["id"])
        result=forge.run_intent(candidate["intent"])
        self.assertIn("ПРЕОБРАЗУЙ",result)
        self.assertIsNone(forge.run_intent("Какая сегодня погода?"))

    def test_wrong_expected_result_blocks_approval(self):
        candidate=forge.store("Учись переводить в верхний регистр",
                              pure_module(tests=[
                                  {"input":"hello","expected":"hell0"},
                                  {"input":"world","expected":"WORLD"}]))
        self.assertEqual(candidate["state"],"review")
        self.assertFalse(candidate["report"]["deployable"])
        with self.assertRaises(ValueError):
            forge.approve(candidate["id"])
        self.assertIsNone(forge.run_intent(candidate["intent"]))

    def test_malicious_python_is_never_activated(self):
        for code in [
            'import os\ndef solve(value):\n    return os.system("id")',
            'def solve(value):\n    return __import__("os").system("id")',
            'def solve(value):\n    return open("/etc/passwd").read()',
            'def solve(value):\n    return value.__class__',
            'def solve(value):\n    return [x for x in value]',
            'def solve(value):\n    while True: pass',
            'def solve(value):\n    return (lambda: 123)()',
            'def solve(value):\n    return value * 10000000'
        ]:
            with self.subTest(code=code):
                with self.assertRaises((ValueError,SyntaxError)):
                    forge.pure_run(code,"abc")

    def test_bound_output_and_numbers(self):
        self.assertEqual(forge.pure_run("def solve(value):\n    return value[::-1]\n","NEXUS"),"SUXEN")
        self.assertEqual(forge.pure_run("def solve(value):\n    return value * 2\n",9),18)
        with self.assertRaises(ValueError):
            forge.pure_run("def solve(value):\n    return value * 2\n",10**9)
        with self.assertRaises(ValueError):
            forge.pure_run("def solve(value):\n    return value.replace('a','aaaaa')\n","a"*400)
        with self.assertRaises(ValueError):
            forge.pure_run("def solve(value):\n    return value / 0\n",100)

    def test_read_only_recipe_dry_run_and_approval(self):
        candidate=forge.store("Покажи аккумулятор и заметки",recipe_module())
        self.assertEqual(candidate["state"],"tested")
        self.assertEqual(len(candidate["report"]["checks"]),2)
        self.assertIsNone(forge.run_intent(candidate["intent"]))
        executed=[]
        fake_agent=types.SimpleNamespace(
            execute=lambda name,arg:executed.append((name,arg)) or "MOCKED")
        with patch.dict(sys.modules,{"agent":fake_agent}):
            forge.approve(candidate["id"])
            result=forge.run_intent(candidate["intent"])
        self.assertEqual(executed,[("battery",""),("notes_list","")])
        self.assertIn("MOCKED",result)

    def test_recipe_rejects_side_effects_and_unknown_calls(self):
        for action in ("torch_on","notify","speak","sms_send","shell","firmware_flash",
                       "contacts_list","memory_delete","termux-sms-send"):
            with self.subTest(action=action):
                with self.assertRaises(ValueError):
                    forge.store("Вызывай недоверенный инструмент",
                                recipe_module([{"tool":action,"argument":"x"}]))
        self.assertFalse(forge.list_drafts())

    def test_approval_cannot_activate_unreviewed_cpp(self):
        payload={
            "kind":"proposal","name":"m5_display_patch","summary":"Макет прошивки M5",
            "source":"// concept only\nvoid setup() { }","language":"cpp",
            "steps":[],"tests":[]}
        proposal=forge.store("Измени код стика",payload)
        self.assertEqual(proposal["state"],"review")
        self.assertFalse(proposal["report"]["tested"])
        self.assertFalse(proposal["report"]["deployable"])
        with self.assertRaises(ValueError):
            forge.approve(proposal["id"])
        self.assertIsNone(forge.run_intent("Измени код стика"))

    def test_python_proposal_parses_but_not_executed_or_approved(self):
        payload={
            "kind":"proposal","name":"new_android_capability","summary":"Новый адаптер",
            "source":"import os\nos.system('do not run')", "language":"python",
            "steps":[],"tests":[]}
        proposal=forge.store("Новый адаптер Android",payload)
        self.assertEqual(proposal["state"],"review")
        self.assertIn("Исходники не выполнялись",proposal["report"]["errors"][0])
        with self.assertRaises(ValueError):
            forge.approve(proposal["id"])

    def test_revoke_approved_skill(self):
        draft=forge.store("Преобразуй строку",pure_module())
        forge.approve(draft["id"])
        self.assertIsNotNone(forge.run_intent("Преобразуй строку"))
        self.assertTrue(forge.reject(draft["id"]))
        self.assertIsNone(forge.run_intent("Преобразуй строку"))

    def test_code_stays_in_private_sqlite_not_in_source_tree(self):
        draft=forge.store("Покажи аккумулятор и заметки",recipe_module())
        self.assertEqual(list(Path(self.tmp.name).glob("*.py")),[])
        self.assertEqual(forge.ROOT.stat().st_mode & 0o777,0o600)
        self.assertEqual(forge._get(draft["id"])["payload"]["name"],"battery_note")

    def test_rejects_secrets_before_network_and_disk(self):
        for task in ("Запомни пароль: qwerty","My API_KEY=secret"):
            with self.subTest(task=task),self.assertRaises(ValueError):
                forge.store(task,pure_module())
        self.assertEqual(forge.list_drafts(),[])

    def test_no_validated_module_when_json_is_bad(self):
        response={"candidates":[{"content":{"parts":[{"text":"not JSON"}]}}]}
        with patch.dict(os.environ,{"GEMINI_API_KEY":"fake","GEMINI_MODEL":"gemini-test"}),\
             patch.object(forge,"post_json",return_value=response):
            with self.assertRaises(ValueError):
                forge.propose("Сделай навык")

    def test_cloud_generation_does_not_activate_proposal(self):
        generated=pure_module()
        response={"candidates":[{"content":{"parts":[{"text":json.dumps(generated,ensure_ascii=False)}]}}]}
        with patch.dict(os.environ,{"GEMINI_API_KEY":"fake","GEMINI_MODEL":"gemini-test"}),\
             patch.object(forge,"post_json",return_value=response) as call:
            draft=forge.propose("Преобразуй мой текст")
        self.assertEqual(draft["state"],"tested")
        self.assertIsNone(forge.run_intent(draft["intent"]))
        self.assertEqual(call.call_count,1)
        self.assertEqual(call.call_args.kwargs["timeout"],30)

    def test_retest_checks_stored_rules(self):
        draft=forge.store("Покажи статус батареи",recipe_module())
        tested=forge.retest(draft["id"])
        self.assertEqual(tested["state"],"tested")
        self.assertTrue(all(x["passed"] for x in tested["report"]["checks"]))

    def test_no_symlink_to_private_db(self):
        root=Path(self.tmp.name)
        other=root/"out.txt"
        other.write_text("untouched")
        forge.ROOT.symlink_to(other)
        with self.assertRaises(RuntimeError):
            forge.list_drafts()
        self.assertEqual(other.read_text(),"untouched")


if __name__=="__main__":
    unittest.main()
