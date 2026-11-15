"""NEXUS 2.1: "научись" skill acquisition, contacts and single-use SMS approval."""
from __future__ import annotations
import json
import sys
import tempfile
import threading
import time
import types
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]/"android_bridge"))
import skill_academy as academy


class SmsLearningTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.patcher = patch.object(academy,"DB_PATH",Path(self.tmp.name)/"skill.sqlite3")
        self.patcher.start()
        self.commands=[]
        self.contacts=[
            {"name":"Папа","number":"+7 (999) 000-00-01"},
            {"name":"Мама","number":"+7 999 000 00 02"},
        ]
        self.contact_ok=True
        self.sms_ok=True

    def tearDown(self):
        self.patcher.stop()
        self.tmp.cleanup()

    def fake_run(self,argv,timeout=12):
        self.commands.append(list(argv))
        if argv[0]=="termux-contact-list":
            return (self.contact_ok, json.dumps(self.contacts,ensure_ascii=False)
                    if self.contact_ok else "Permission denied")
        if argv[0]=="termux-sms-send":
            return (self.sms_ok,"" if self.sms_ok else "device error")
        raise AssertionError("Unexpected command "+repr(argv))

    def tools(self):
        return patch.object(academy.shutil,"which",return_value="/bin/test-command")

    def test_voice_learning_then_confirmed_send_exact_contact(self):
        with self.tools(),patch.object(academy,"_run",side_effect=self.fake_run):
            start=academy.handle("Нексус, напиши папе")
            self.assertIn("Скажи «научись»",start)
            self.assertEqual(self.commands,[])
            learned=academy.handle("научись")
            self.assertIn("Какой текст отправить",learned)
            self.assertIn("0001",academy._active()["number"])
            self.assertEqual([cmd[0] for cmd in self.commands],["termux-contact-list"])
            preview=academy.handle("Папа, скоро буду дома!")
            self.assertIn("Папа, скоро буду дома!",preview)
            self.assertIn("подтверждаю отправку",preview)
            self.assertEqual([cmd[0] for cmd in self.commands],["termux-contact-list"])
            result=academy.handle("подтверждаю отправку")
            self.assertIn("Команда отправки SMS принята",result)
            sends=[x for x in self.commands if x[0]=="termux-sms-send"]
            self.assertEqual(len(sends),1)
            self.assertEqual(sends[0][1:],["-n","+79990000001","Папа, скоро буду дома!"])
            self.assertEqual(academy.load_skill("sms")["status"],"learned")
            again=academy.handle("подтверждаю отправку")
            self.assertIn("Нет сообщения",again)
            self.assertEqual(len([x for x in self.commands if x[0]=="termux-sms-send"]),1)

    def test_relearned_sms_skips_training_on_second_request(self):
        with self.tools(),patch.object(academy,"_run",side_effect=self.fake_run):
            academy._set_skill("sms","learned",{"method":"Termux:API"})
            msg=academy.handle("напиши маме")
            self.assertIn("Какой текст",msg)
            self.assertNotIn("научись",msg)
            self.assertEqual(len(self.commands),1)

    def test_cancel_never_sends(self):
        with self.tools(),patch.object(academy,"_run",side_effect=self.fake_run):
            academy.handle("напиши папе")
            academy.handle("научись")
            academy.handle("Я опоздаю.")
            msg=academy.handle("отмена")
            self.assertIn("отменено",msg)
            self.assertFalse(any(x[0]=="termux-sms-send" for x in self.commands))
            self.assertIn("Нет сообщения",academy.handle("подтверждаю отправку"))

    def test_wrong_confirmation_blocked(self):
        with self.tools(),patch.object(academy,"_run",side_effect=self.fake_run):
            academy.handle("напиши папе")
            academy.handle("научись")
            academy.handle("Проверка")
            self.assertIn("не отправлено",academy.handle("отправить сейчас"))
            self.assertFalse(any(x[0]=="termux-sms-send" for x in self.commands))

    def test_no_contact_prompts_for_number_without_guessing(self):
        self.contacts=[{"name":"Папа Сергей","number":"+79991234567"}]
        with self.tools(),patch.object(academy,"_run",side_effect=self.fake_run):
            academy.handle("напиши папе")
            response=academy.handle("научись")
            self.assertIn("не найден точно",response)
            self.assertIn("Какой текст",academy.handle("номер +79990000003"))
            preview=academy.handle("Добрый вечер")
            self.assertIn("0003",preview)

    def test_ambiguous_contact_forces_explicit_selection(self):
        self.contacts=[{"name":"Папа","number":"+79991111111"},
                       {"name":"Папа","number":"+79992222222"}]
        with self.tools(),patch.object(academy,"_run",side_effect=self.fake_run):
            academy.handle("напиши папе")
            answer=academy.handle("научись")
            self.assertIn("несколько номеров",answer)
            self.assertFalse(any(x[0]=="termux-sms-send" for x in self.commands))
            answer=academy.handle("выбери 2")
            self.assertIn("2222",answer)
            self.assertEqual(academy._active()["number"],"+79992222222")

    def test_missing_api_never_calls_pkg_or_sends(self):
        with patch.object(academy.shutil,"which",return_value=None), \
             patch.object(academy,"_run",side_effect=self.fake_run):
            academy.handle("напиши папе")
            response=academy.handle("научись")
            self.assertIn("Подготовить SMS",response)
            self.assertEqual(self.commands,[])

    def test_permission_failure_is_reported_without_false_success(self):
        self.contact_ok=False
        with self.tools(),patch.object(academy,"_run",side_effect=self.fake_run):
            academy.handle("напиши папе")
            answer=academy.handle("научись")
            self.assertIn("разрешения",answer)
            self.assertNotEqual(academy.load_skill("sms"),{"status":"learned"})

    def test_uncertain_sms_result_is_not_retried(self):
        self.sms_ok=False
        with self.tools(),patch.object(academy,"_run",side_effect=self.fake_run):
            academy.handle("напиши папе")
            academy.handle("научись")
            academy.handle("Тест")
            msg=academy.handle("подтверждаю отправку")
            self.assertIn("не подтверждена",msg)
            self.assertEqual(academy._active()["stage"],"uncertain")
            academy.handle("подтверждаю отправку")
            self.assertEqual(len([x for x in self.commands if x[0]=="termux-sms-send"]),1)

    def test_contact_names_not_exposed_to_gemini(self):
        with self.tools(),patch.object(academy,"_run",side_effect=self.fake_run):
            academy.handle("напиши папе")
            academy.handle("научись")
            academy.handle("Скоро буду")
            self.assertNotIn("+79990000001",academy.handle("нет"))
            self.assertNotIn("+79990000001",academy.handle("подтверждаю отправку"))

    def test_skill_library_private_and_persistent(self):
        academy._set_skill("sms","learned",{"method":"Termux:API"})
        self.assertEqual(academy.DB_PATH.stat().st_mode&0o777,0o600)
        self.assertEqual(academy.load_skill("sms")["status"],"learned")
        self.assertEqual(academy.skills()[0]["kind"],"sms")


class GeneralLearningTest(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.patcher=patch.object(academy,"DB_PATH",Path(self.tmp.name)/"skills.sqlite3")
        self.patcher.start()

    def tearDown(self):
        self.patcher.stop()
        self.tmp.cleanup()

    def test_refusal_then_learning_creates_tested_draft_not_auto_activation(self):
        self.assertTrue(academy.note_refusal("Сделай текст заглавными",
                                            "Пока не умею преобразовывать текст."))
        import skill_studio
        tested={"id":18,"status":"tested","name":"upper_text",
                "report":[{"passed":True},{"passed":True}],"reason":""}
        with patch.object(skill_studio,"propose",return_value=tested):
            result=academy.handle("научись")
        self.assertIn("Спроектировал новый модуль",result)
        self.assertIn("Проверил 2/2",result)
        self.assertEqual(academy.skills(),[])
        self.assertIsNone(academy.pending_goal())

    def test_unknown_device_skill_requires_adaptor_not_falsely_claimed(self):
        self.assertTrue(academy.note_refusal("Измени прошивку",
                                            "Не могу перепрошивать устройства."))
        import skill_studio
        needs={"id":21,"status":"needs_adapter","reason":"Нужен проверенный адаптер",
               "name":"","report":[]}
        with patch.object(skill_studio,"propose",return_value=needs):
            result=academy.handle("научись")
        self.assertIn("проверенный адаптер",result)
        self.assertEqual(academy.skills(),[])

    def test_learning_from_dialog_without_old_skill_record(self):
        history=[("user","Создай новый аппаратный режим"),
                 ("assistant","Не могу сделать это сейчас.")]
        import skill_studio
        needs={"id":31,"status":"needs_adapter","reason":"нужно разработать прошивку",
               "name":"","report":[]}
        with patch.object(skill_studio,"propose",return_value=needs):
            result=academy.handle("научись",history)
        self.assertIn("нужно разработать",result)

    def test_failed_candidate_does_not_claim_success(self):
        academy.add_goal("unknown","Необычный преобразователь текста")
        import skill_studio
        invalid={"id":23,"status":"failed","reason":"Тесты не прошли: 1/2","report":[]}
        with patch.object(skill_studio,"propose",return_value=invalid):
            result=academy.handle("научись")
        self.assertIn("проверка не прошла",result)
        self.assertEqual(academy.skills(),[])


if __name__=="__main__":
    unittest.main()
