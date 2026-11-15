"""NEXUS 2.2 Skill Studio: strict DSL, offline test vectors, owner activation."""
from __future__ import annotations

import ast
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

BRIDGE=Path(__file__).resolve().parents[1]/"android_bridge"
sys.path.insert(0,str(BRIDGE))
import skill_studio as studio
import skill_academy as academy


SPEC={
    "name":"uppercase_text",
    "description":"Преобразование текста в верхний регистр",
    "steps":[{"op":"strip"},{"op":"upper"}],
    "tests":[
        {"input":"  привет  ","expected":"ПРИВЕТ"},
        {"input":"  alpha Beta  ","expected":"ALPHA BETA"},
        {"input":"учимся!","expected":"УЧИМСЯ!"},
    ],
}

class SkillStudioTest(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.root=Path(self.temp.name)/"studio"
        self.patches=[
            patch.object(studio,"BASE",self.root),
            patch.object(studio,"DATABASE",self.root/"studio.sqlite3"),
            patch.object(academy,"DB_PATH",Path(self.temp.name)/"academy.sqlite3")
        ]
        for p in self.patches:p.start()

    def tearDown(self):
        for p in reversed(self.patches):p.stop()
        self.temp.cleanup()

    def _model(self, data):
        return patch.object(studio,"_model_spec",return_value=data)

    def test_generate_test_review_approve_execute_and_disable(self):
        with self._model(SPEC):
            draft=studio.propose("Преобразуй текст в верхний регистр")
        self.assertEqual(draft["status"],"tested")
        self.assertEqual(len(draft["report"]),3)
        self.assertTrue(all(x["passed"] for x in draft["report"]))
        self.assertEqual(studio.DATABASE.stat().st_mode&0o777,0o600)
        source=self.root/str(draft["id"])/"module.py"
        self.assertTrue(source.exists())
        self.assertEqual(source.stat().st_mode&0o777,0o600)
        tree=ast.parse(source.read_text())
        self.assertFalse(any(isinstance(n,(ast.Import,ast.ImportFrom)) for n in ast.walk(tree)))
        with self.assertRaises(ValueError):
            studio.run(draft["id"],"HELLO")
        ok,msg=studio.approve(draft["id"])
        self.assertTrue(ok,msg)
        self.assertIn("активирован",msg)
        self.assertEqual(studio.run(draft["id"]," Hello world "), "HELLO WORLD")
        result=academy.handle("Нексус, навык "+str(draft["id"])+":  привет, мир  ")
        self.assertIn("ПРИВЕТ, МИР",result)
        self.assertTrue(studio.disable(draft["id"]))
        with self.assertRaises(ValueError):
            studio.run(draft["id"]," Hello ")

    def test_no_auto_activation_after_learn_or_model_draft(self):
        academy.add_goal("unknown","Сделай текст заглавными")
        with self._model(SPEC):
            text=academy.handle("научись")
        self.assertIn("Спроектировал новый модуль",text)
        self.assertIn("Проверил 3/3",text)
        active=studio.listing(1)[0]
        self.assertEqual(active["status"],"tested")
        with self.assertRaises(ValueError):
            studio.run(active["id"],"abc")

    def test_invalid_op_and_arbitrary_python_are_rejected(self):
        invalid={**SPEC,"steps":[{"op":"run_shell","argument":"cat ~/.config/nexus/secrets.env"}]}
        with self.assertRaises(ValueError):
            studio.validate(invalid)
        injected={**SPEC,"steps":[{"op":"replace","from":"x",
                                    "to":"x; __import__('os').system('id')"}]}
        cleaned=studio.validate(injected)
        src=studio.render_source(cleaned)
        self.assertNotIn("\nimport os",src)
        self.assertNotIn("\n    __import__",src)
        result=studio.run_steps(cleaned["steps"],"x")
        self.assertIn("__import__",result)  # inert TEXT, never executed

    def test_failed_tests_refuse_activation(self):
        wrong={**SPEC,"tests":[{"input":"abc","expected":"wrong"},
                              {"input":"def","expected":"another"}]}
        with self._model(wrong):
            proposal=studio.propose("Хочу преобразовать текст")
        self.assertEqual(proposal["status"],"failed")
        self.assertFalse(studio.approve(proposal["id"])[0])

    def test_tampered_source_fails_digest_check(self):
        with self._model(SPEC):
            proposal=studio.propose("Текст в верхнем регистре")
        original=self.root/str(proposal["id"])/"module.py"
        original.write_text("print('malicious change')")
        self.assertFalse(studio.approve(proposal["id"])[0])
        self.assertEqual(studio.get(proposal["id"])["status"],"tested")

    def test_action_request_is_rejected_without_cloud_or_software_install(self):
        with patch.object(studio,"_model_spec",side_effect=AssertionError("no cloud")):
            for task in ("Перепиши прошивку M5StickC", "Скачай файл",
                         "Отправь SMS папе", "Открой камеру Android"):
                draft=studio.propose(task)
                self.assertEqual(draft["status"],"needs_adapter")
                self.assertFalse(studio.approve(draft["id"])[0])

    def test_missing_gemini_does_not_claim_learning(self):
        with patch.dict(os.environ,{"GEMINI_API_KEY":""}):
            draft=studio.propose("Переведи слова в верхний регистр")
        self.assertEqual(draft["status"],"failed")
        self.assertIn("ключ Gemini",draft["reason"])

    def test_explicit_unsupported_model_output(self):
        with self._model({"unsupported":"Нужен интерфейс USB, которого пока нет"}):
            draft=studio.propose("Считай пульс по сенсору")
        self.assertEqual(draft["status"],"needs_adapter")
        self.assertIn("USB",draft["reason"])

    def test_unrecognized_skill_phrases_continue_to_normal_planner(self):
        self.assertIsNone(studio.handle_voice("Расскажи про космос"))
        self.assertIsNone(studio.handle_voice("Отправь папе сообщение"))

    def test_safe_runtime_is_length_bounded(self):
        with self.assertRaises(ValueError):
            studio.run_steps([{"op":"upper"}],"x"*601)
        with self.assertRaises(ValueError):
            studio.run_steps([{"op":"suffix","value":"x"*50}]*7,"A"*500)

    def test_duplicate_candidate_reuses_existing_project(self):
        with self._model(SPEC) as model:
            first=studio.propose("Верхний регистр букв")
            second=studio.propose("Верхний регистр букв")
        self.assertEqual(first["id"],second["id"])
        self.assertEqual(model.call_count,1)

    def test_allows_pure_text_operations_without_import(self):
        steps=[{"op":"collapse_spaces"},
               {"op":"replace","from":"кот","to":"пёс"},
               {"op":"prefix","value":"!"},
               {"op":"suffix","value":"."}]
        self.assertEqual(studio.run_steps(steps,"кот  красивый"),"!пёс красивый.")

    def test_rejects_schema_mutations(self):
        examples=[
           {"name":"../../bad","description":"x","steps":[{"op":"upper"}],
            "tests":SPEC["tests"]},
           {**SPEC,"steps":[{"op":"replace","from":"","to":"x"}]},
           {**SPEC,"tests":[SPEC["tests"][0],SPEC["tests"][0]]},
           {**SPEC,"steps":[{"op":"upper","shell":"os.system"}]},
           {**SPEC,"steps":[{"op":"prefix","value":"x"*101}]}
        ]
        for spec in examples:
            with self.subTest(spec=spec),self.assertRaises(ValueError):
                studio.validate(spec)


if __name__=="__main__":
    unittest.main()
