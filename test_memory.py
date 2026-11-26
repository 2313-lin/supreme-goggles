"""长记忆测试（§9）。"""
from __future__ import annotations

import os
import tempfile
import time
import unittest

from nds.memory import MemoryStore


class MemoryTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = MemoryStore(os.path.join(self.tmp.name, "m.db"), session_ttl_days=7)

    def tearDown(self):
        self.store.close()
        self.tmp.cleanup()

    def test_default_profile(self):
        profile = self.store.get_profile("u1")
        self.assertEqual(profile["preferred_themes"], [])
        self.assertEqual(profile["blocked_topics"], [])

    def test_search_and_click_form_preferences(self):
        self.store.record_search("u1", "s1", "重生 复仇", ["重生"], ["novel"])
        self.store.record_click("u1", "s1", "n_tuihun", "退婚三次后我成了首富",
                                ["重生", "爽文"], "顾南枝", ["苏晚"])
        profile = self.store.get_profile("u1")
        self.assertIn("重生", profile["preferred_themes"])
        self.assertIn("顾南枝", profile["preferred_authors"])
        self.assertIn("n_tuihun", profile["clicked_works"])
        session = self.store.get_session("s1")
        self.assertIn("n_tuihun", session["clicked_works"])

    def test_blocked_topics(self):
        self.store.set_blocked_topics("u1", ["恐怖", "  ", "赌博"])
        profile = self.store.get_profile("u1")
        self.assertEqual(profile["blocked_topics"], ["恐怖", "赌博"])

    def test_procedure_memory(self):
        self.assertIsNone(self.store.procedure_stage("重生复仇"))
        self.store.record_procedure("重生复仇", 3, "重生 复仇")
        self.assertEqual(self.store.procedure_stage("重生复仇"), 3)
        rows = self.store.procedures()
        self.assertEqual(rows[0]["hits"], 1)

    def test_export_and_clear(self):
        self.store.record_search("u1", "s1", "林昭", ["悬疑"], ["novel"])
        exported = self.store.export("u1")
        self.assertIn("profile", exported)
        self.assertTrue(exported["entities"])
        self.store.clear("u1")
        self.assertEqual(self.store.export("u1")["entities"], [])

    def test_purge_expired_sessions(self):
        self.store.record_search("u1", "s_old", "旧查询", [], ["novel"])
        removed = self.store.purge_expired_sessions(now=time.time() + 8 * 86400)
        self.assertGreaterEqual(removed, 1)
        self.assertEqual(self.store.get_session("s_old")["recent_queries"], [])

    def test_weights_are_capped(self):
        for _ in range(20):
            self.store.record_click("u1", "s1", "w1", "书", ["重生"], "作者", [])
        exported = self.store.export("u1")
        work_weights = [e["weight"] for e in exported["entities"] if e["entity_type"] == "work"]
        self.assertTrue(work_weights)
        self.assertLessEqual(max(work_weights), 10.0)


if __name__ == "__main__":
    unittest.main()
