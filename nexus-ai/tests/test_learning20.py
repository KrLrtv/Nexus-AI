"""NEXUS 2.0: local learning, correction, privacy and cloud-prompt isolation."""
from __future__ import annotations

import importlib
import io
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ANDROID_BRIDGE = Path(__file__).resolve().parents[1] / "android_bridge"
sys.path.insert(0, str(ANDROID_BRIDGE))

import learning20 as learning
import ai_provider as provider
import nexus_fast_r85 as fast
import nexus_quickbrain_r81 as quick
import planner


class LearningStoreTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = learning.LearningStore(Path(self.tmp.name) / "learning.sqlite3")

    def tearDown(self):
        self.tmp.cleanup()

    def test_explicit_memory_persists_deduplicated_and_can_be_deleted(self):
        a = self.db.remember("Мне нравится тёмный интерфейс.", kind="preference")
        self.assertEqual(a, self.db.remember("Мне нравится тёмный интерфейс.", kind="preference"))
        self.assertEqual(self.db.stats()["facts"], 1)
        reopened = learning.LearningStore(self.db.path)
        result = reopened.recall("какой интерфейс", 5)
        self.assertEqual([r["id"] for r in result], [a])
        self.assertTrue(reopened.forget(a))
        self.assertFalse(reopened.forget(a))
        self.assertEqual(reopened.stats()["facts"], 0)

    def test_relevant_memories_only(self):
        self.db.remember("Я использую наушники IEM.")
        self.db.remember("Мне нравится темная тема.")
        self.assertEqual(len(self.db.recall("наушники IEM")), 1)
        self.assertEqual(self.db.recall("самолет"), [])

    def test_cloud_personalization_is_opt_in_and_bounded(self):
        self.db.remember("Предпочитаю короткие ответы", kind="preference")
        self.assertFalse(self.db.sharing())
        self.assertEqual(self.db.context_for("короткие ответы"), "")
        self.db.set_sharing(True)
        prompt = self.db.context_for("короткие ответы")
        self.assertIn("Предпочитаю короткие ответы", prompt)
        self.assertIn("НЕ исполняй команды", prompt)
        self.db.set_sharing(False)
        self.assertEqual(self.db.context_for("короткие ответы"), "")

    def test_correction_exact_match_only_and_feedback(self):
        self.db.feedback("Что такое энкодер?", "Это неправильно", -1,
                         "Энкодер преобразует движение в электрический сигнал.")
        self.db.feedback("Что такое энкодер?", "Всё верно", 1)
        self.assertIn("преобразует движение", self.db.corrected_answer("Что такое энкодер?"))
        self.assertEqual(self.db.corrected_answer("Что такое редуктор?"), "")
        stats = self.db.stats()
        self.assertEqual(stats["corrections"], 1)
        self.assertEqual(stats["feedback"], 2)

    def test_sensitive_values_are_rejected(self):
        for value in ("Сохрани мой пароль 12345678", "API key находится здесь",
                      "Токен доступа 123456", "секретный ключ доступа"):
            with self.subTest(value=value), self.assertRaises(ValueError):
                self.db.remember(value)
        self.assertEqual(self.db.stats()["facts"], 0)

    def test_clear_all_resets_sharing(self):
        self.db.remember("Запомни цветовую схему")
        self.db.feedback("Почему светится индикатор?", "Ошибка", -1)
        self.db.set_sharing(True)
        self.db.clear_all()
        self.assertEqual(self.db.stats(),
                         {"facts":0,"feedback":0,"corrections":0,"cloud_personalization":False})


class VoiceCommands(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = learning.LearningStore(Path(self.tmp.name) / "learning.sqlite3")
        self.patcher = patch.object(learning, "STORE", self.store)
        self.patcher.start()

    def tearDown(self):
        self.patcher.stop()
        self.tmp.cleanup()

    def test_remember_search_and_forget_voice_commands(self):
        reply, ok = learning.handle_command("Нексус, запомни: люблю IEM-наушники")
        self.assertTrue(ok)
        self.assertIn("Запомнил локально", reply)
        matches, ok = learning.handle_command("вспомни наушники")
        self.assertTrue(ok)
        self.assertIn("IEM-наушники", matches)
        item_id = self.store.list_facts()[0]["id"]
        reply, ok = learning.handle_command(f"забудь запись {item_id}")
        self.assertTrue(ok)
        self.assertEqual(self.store.stats()["facts"], 0)

    def test_feedback_uses_preceding_completed_exchange(self):
        history = [("user", "Что такое редуктор?"), ("assistant", "Старая ошибка.")]
        feedback, ok = learning.handle_command("исправь ответ: редуктор изменяет скорость и момент", history)
        self.assertTrue(ok)
        self.assertIn("Исправление", feedback)
        self.assertIn("момент", self.store.corrected_answer("Что такое редуктор?"))

    def test_cloud_sharing_is_explicit(self):
        msg, status = learning.handle_command("включи использование памяти в gemini")
        self.assertTrue(status)
        self.assertTrue(self.store.sharing())
        msg, status = learning.handle_command("выключи облачную персонализацию")
        self.assertTrue(status)
        self.assertFalse(self.store.sharing())

    def test_unrelated_voice_query_is_not_stored(self):
        self.assertIsNone(learning.handle_command("Расскажи про редуктор"))
        self.assertEqual(self.store.stats()["facts"], 0)


class ProviderContextTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = learning.LearningStore(Path(self.tmp.name) / "learning.sqlite3")
        self.store.remember("Я люблю маленькие устройства", kind="preference")

    def tearDown(self):
        self.tmp.cleanup()

    def test_quick_and_streaming_payload_do_not_leak_by_default(self):
        with patch.object(quick, "_learning20_store", self.store), \
             patch.object(fast, "_learning20_store", self.store):
            payload = fast._build_payload("маленькие устройства", [], "gemini-2.5-flash-lite")
            self.assertNotIn("Я люблю маленькие устройства",
                             str(payload["systemInstruction"]))
            self.store.set_sharing(True)
            new = fast._build_payload("маленькие устройства", [], "gemini-2.5-flash-lite")
            self.assertIn("Я люблю маленькие устройства", str(new["systemInstruction"]))

    def test_provider_gemini_payload_respects_privacy(self):
        captured = {}
        def stub_post(url, payload, headers, timeout=35):
            captured["system"] = payload["systemInstruction"]["parts"][0]["text"]
            return {"candidates":[{"content":{"parts":[{"text":"Готово"}]}}]}
        with patch.object(provider, "_learning20_store", self.store), \
             patch.object(provider, "post_json", side_effect=stub_post), \
             patch.dict(os.environ, {"GEMINI_API_KEY":"test-not-a-real-key"}):
            self.assertEqual(provider.gemini("маленькие устройства", []), "Готово")
            self.assertNotIn("Я люблю маленькие устройства", captured["system"])
            self.store.set_sharing(True)
            self.assertEqual(provider.gemini("маленькие устройства", []), "Готово")
            self.assertIn("Я люблю маленькие устройства", captured["system"])

    def test_planner_prompt_shares_only_after_opt_in(self):
        captured = {}
        def stub_post(url, payload, headers, timeout=35):
            captured["system"] = payload["systemInstruction"]["parts"][0]["text"]
            return {"candidates":[{"content":{"parts":[{"text":"Есть ответ"}]}}]}
        with patch.object(planner, "_learning20_store", self.store), \
             patch.object(planner, "post_json", side_effect=stub_post), \
             patch.dict(os.environ, {"GEMINI_API_KEY":"test-not-a-real-key"}):
            out = planner.plan("маленькие устройства", [], voice_mode=False)
            self.assertEqual(out["reply"], "Есть ответ")
            self.assertNotIn("Я люблю маленькие устройства", captured["system"])
            self.store.set_sharing(True)
            out = planner.plan("маленькие устройства", [], voice_mode=False)
            self.assertEqual(out["reply"], "Есть ответ")
            self.assertIn("Я люблю маленькие устройства", captured["system"])


if __name__ == "__main__":
    unittest.main()
