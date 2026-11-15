#!/usr/bin/env python3
"""One-time, hash-checked source integration for the v2.0.5 preview branch.

CI applies these edits, runs the complete suite, commits the resulting sources
and only then publishes the tag. This script is NOT a phone-side installer.
Reruns are idempotent; divergent, unmarked source files fail closed.
"""
from pathlib import Path
import ast
import hashlib

ROOT = Path(__file__).resolve().parents[1]
MARKER = '# NEXUS_AUTO_ANSWER_V205'


def replace_span(text, start, end, replacement):
    if text.count(start) != 1 or text.count(end) != 1:
        raise RuntimeError('Integration anchors are missing or ambiguous.')
    first, last = text.index(start), text.index(end)
    if last <= first:
        raise RuntimeError('Integration anchors are out of order.')
    return text[:first] + replacement + text[last:]


def integrate(path, expected_blob, transform):
    target = ROOT / path
    raw = target.read_bytes()
    old = raw.decode('utf-8')
    if MARKER in old:
        ast.parse(old)
        print('Already integrated:', path)
        return
    blob = hashlib.sha1(b'blob ' + str(len(raw)).encode() + b'\0' + raw).hexdigest()
    if blob != expected_blob:
        raise RuntimeError('Refusing to overwrite divergent source: ' + path)
    new = transform(old)
    if MARKER not in new:
        raise RuntimeError('Integration marker missing.')
    ast.parse(new, filename=path)
    target.write_text(new, encoding='utf-8')
    print('Integrated:', path)


LEARN = '''def _learn(goal):
    # NEXUS_AUTO_ANSWER_V205
    if goal["kind"] == "sms":
        _start(goal["id"], goal["recipient"])
        return _resolve(_active())
    try:
        from skill_autolearn import learn_and_answer
        result = learn_and_answer(goal["intent"])
    except Exception:
        return ("Сохранил задачу, но сейчас не могу подготовить проверенный модуль. "
                "Проверь Gemini и соединение, затем скажи «научись» ещё раз.")
    if result.active:
        with LOCK:
            with _connect() as c:
                c.execute("UPDATE skill_goals SET status='learned' WHERE id=?",
                          (goal["id"],))
        return result.reply
    return "Сохранил задачу. " + result.reply

'''

CREATE = '''        if action=='forge-create':
            # NEXUS_AUTO_ANSWER_V205
            def create_module():
                from skill_autolearn import learn_and_answer
                print('Проверенный безопасный навык включится и ответит автоматически.')
                request=input('Задача (для обработки данных: задача: данные): ').strip()
                if not request:
                    print('Задача не введена. Отменено.')
                    return False
                result=learn_and_answer(request)
                print(sanitise(result.reply))
                return result.active
            self.popup(create_module);return
'''

GENERAL = '''class GeneralLearningTest(unittest.TestCase):
    # NEXUS_AUTO_ANSWER_V205
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.patcher=patch.object(academy,"DB_PATH",Path(self.tmp.name)/"skills.sqlite3")
        self.patcher.start()

    def tearDown(self):
        self.patcher.stop()
        self.tmp.cleanup()

    def test_cloud_refusal_then_automatic_skill_returns_answer(self):
        self.assertTrue(academy.note_refusal("Проверь состояние батареи", "Не могу проверить сейчас."))
        result=types.SimpleNamespace(active=True, reply="Научился. Навык #17 включён автоматически. Заряд: 75%")
        fake=types.SimpleNamespace(learn_and_answer=lambda intent:result)
        with patch.dict(sys.modules,{"skill_autolearn":fake}):
            reply=academy.handle("научись")
        self.assertIn("75%",reply)
        self.assertNotIn("Для включения",reply)
        self.assertIsNone(academy.pending_goal())

    def test_unknown_skill_is_not_falsely_claimed(self):
        self.assertTrue(academy.note_refusal("Измени прошивку", "Не могу перепрошивать устройства."))
        result=types.SimpleNamespace(active=False, reply="Создан черновик. Код не запускался.")
        fake=types.SimpleNamespace(learn_and_answer=lambda intent:result)
        with patch.dict(sys.modules,{"skill_autolearn":fake}):
            reply=academy.handle("научись")
        self.assertIn("черновик",reply)
        self.assertIn("не запускался",reply)
        self.assertIsNotNone(academy.pending_goal())
        self.assertEqual(academy.skills(),[])

    def test_learning_from_dialog_without_old_skill_record(self):
        history=[("user","Создай новый аппаратный режим"), ("assistant","Не могу сделать это сейчас.")]
        result=types.SimpleNamespace(active=False, reply="Создан черновик, требуется ревью.")
        fake=types.SimpleNamespace(learn_and_answer=lambda intent:result)
        with patch.dict(sys.modules,{"skill_autolearn":fake}):
            reply=academy.handle("научись",history)
        self.assertIn("черновик",reply)
        self.assertIsNotNone(academy.pending_goal())

    def test_when_gemini_is_unavailable_goal_is_preserved(self):
        academy.note_refusal("Запусти неизвестный модуль","Не могу это сделать.")
        fake=types.SimpleNamespace(learn_and_answer=lambda intent:(_ for _ in ()).throw(RuntimeError("network unavailable")))
        with patch.dict(sys.modules,{"skill_autolearn":fake}):
            reply=academy.handle("научись")
        self.assertIn("Сохранил задачу",reply)
        self.assertIsNotNone(academy.pending_goal())


'''

MENU_TEST = '''    def test_create_skill_through_menu_not_cli(self):
        # NEXUS_AUTO_ANSWER_V205
        screen,tui=self._ui()
        calls=[]
        result=types.SimpleNamespace(active=True, reply="Навык #44 включён автоматически. Ответ: HELLO")
        fake=types.SimpleNamespace(learn_and_answer=lambda intent:calls.append(intent) or result)
        with patch.dict(sys.modules,{"skill_autolearn":fake}), \\
             patch("builtins.input",return_value="Регистр: hello") as input_call, \\
             patch("sys.stdout",io.StringIO()) as out:
            screen.handle("forge-create")
        self.assertFalse(screen.flash_error)
        self.assertEqual(calls,["Регистр: hello"])
        self.assertEqual(input_call.call_count,1)
        self.assertIn("HELLO",out.getvalue())

'''


def main():
    integrate('nexus-ai/android_bridge/skill_academy.py', '30e013a8f8fd5e4030931eb8c2ce37bc808a537a',
              lambda old: replace_span(old, 'def _learn(goal):', 'def _process_session(session,txt):', LEARN))
    def menu(old):
        old=replace_span(old, "        if action=='forge-create':", "        if action=='forge-list':", CREATE)
        old=old.replace("('Создать новый модуль с Gemini','forge-create')", "('Научиться и сразу ответить','forge-create')")
        return old
    integrate('nexus-ai/nexus_tui.py', '0fd0f6971d729d9c6103ad580fd9152e8253356d', menu)
    integrate('nexus-ai/tests/test_skill_academy.py', 'c2c4d2d40fc4882c995b9e49497395de7e86a9da',
              lambda old: replace_span(old, 'class GeneralLearningTest(unittest.TestCase):', 'if __name__=="__main__":', GENERAL))
    integrate('nexus-ai/tests/test_termux_menu.py', 'def531013f81effecd033ad182642ca3960c209e',
              lambda old: replace_span(old, '    def test_create_skill_through_menu_not_cli(self):',
                                       '    def test_skill_setup_declined_without_package_install(self):', MENU_TEST))


if __name__ == '__main__':
    main()
