"""端到端检索行为测试：主源降级、熔断跳过、预算超时、记忆个性化。"""
from __future__ import annotations

import unittest
import time

from tests.support import make_app


class AgentSearchTests(unittest.TestCase):
    def setUp(self):
        self.app, self.tmp = make_app()

    def tearDown(self):
        self.app.close()
        self.tmp.cleanup()

    def titles(self, payload, kind=None):
        groups = payload["groups"] if kind is None else {kind: payload["groups"][kind]}
        return [item["work"]["title"] for group in groups.values() for item in group["results"]]

    def test_sentence_query_is_definitive(self):
        payload = self.app.agent.search("她说她从来没有恨过我，只是不想再见到我", "all", "u_test")
        self.assertTrue(payload["allowed"])
        self.assertFalse(payload["degraded"])
        self.assertEqual(payload["fallback_stage"], 1)
        novel = payload["groups"]["novel"]["results"]
        self.assertEqual(novel[0]["work"]["title"], "长夜将明")
        self.assertEqual(novel[0]["layer"], 0)
        self.assertTrue(novel[0]["verified"])
        self.assertEqual(novel[0]["evidence"][0]["kind"], "sentence")

    def test_character_query_hits_main_cast(self):
        payload = self.app.agent.search("林昭", "novel", "u_test")
        self.assertEqual(self.titles(payload), ["长夜将明"])
        self.assertEqual(payload["groups"]["novel"]["results"][0]["layer"], 2)

    def test_drama_line_query(self):
        payload = self.app.agent.search("念念，你跑什么？我又不会吃了你", "drama", "u_test")
        results = payload["groups"]["short_drama"]["results"]
        self.assertEqual(results[0]["work"]["title"], "闪婚后，傅先生每天都在吃醋")
        self.assertEqual(results[0]["layer"], 0)
        self.assertEqual(results[0]["layer_label"], "台词精确命中")

    def test_primary_down_falls_back_to_backup(self):
        self.app.registry.simulate("fanqie", down=True)
        payload = self.app.agent.search("林昭", "novel", "u_test")
        self.assertTrue(payload["degraded"])
        self.assertTrue(payload["expanded"])
        self.assertGreaterEqual(payload["fallback_stage"], 2)
        self.assertIn("长夜将明", self.titles(payload))
        reasons = " ".join(payload["degraded_reasons"])
        self.assertIn("fanqie", reasons)

    def test_all_novel_sources_down_returns_empty_degraded(self):
        self.app.registry.simulate("fanqie", down=True)
        self.app.registry.simulate("qimao", down=True)
        payload = self.app.agent.search("林昭", "novel", "u_test")
        self.assertTrue(payload["degraded"])
        self.assertEqual(payload["groups"]["novel"]["total"], 0)

    def test_budget_timeout_still_returns(self):
        self.app.registry.simulate("fanqie", latency_ms=600)
        payload = self.app.agent.search("林昭", "novel", "u_test", budget_ms=120)
        self.assertLess(payload["elapsed_ms"], 500)
        self.assertTrue(payload["degraded"])
        self.assertTrue(any("预算" in r or "超时" in r or "不可用" in r for r in payload["degraded_reasons"]))

    def test_blocked_query_refused(self):
        payload = self.app.agent.search("冰毒的制作教程", "all", "u_test")
        self.assertFalse(payload["allowed"])
        self.assertEqual(payload["code"], "blocked_topic")
        self.assertEqual(payload["groups"], {})

    def test_blocked_theme_filters_results(self):
        self.app.memory.set_blocked_topics("u_block", ["悬疑"])
        payload = self.app.agent.search("复仇", "novel", "u_block")
        for group in payload["groups"].values():
            for item in group["results"]:
                self.assertNotIn("悬疑", item["work"]["themes"])
        self.assertTrue(any("屏蔽" in w for w in payload["warnings"]))

    def test_result_has_trace_and_platform_report(self):
        payload = self.app.agent.search("闪婚", "drama", "u_test")
        self.assertTrue(payload["trace_id"])
        self.assertTrue(payload["platforms"])
        self.assertTrue(any(p["used"] for p in payload["platforms"]))
        self.assertEqual(payload["understanding"]["intent"], "theme")

    def test_memory_personalization_within_layer(self):
        # 两次点击让"侯府嫡女不好惹"（重生）在主题层获得加分
        self.app.agent.search("重生", "novel", "u_mem")
        for _ in range(2):
            self.app.memory.record_click("u_mem", "s_mem", "n_houfu", "侯府嫡女不好惹",
                                         ["古装", "宅斗", "重生"], "沈清月", ["沈清月"])
        profile = self.app.memory.get_profile("u_mem")
        self.assertIn("n_houfu", profile["clicked_works"])

        payload = self.app.agent.search("重生", "novel", "u_mem")
        results = payload["groups"]["novel"]["results"]
        self.assertTrue(results)
        self.assertEqual(results[0]["work"]["id"], "n_houfu")
        self.assertEqual(results[0]["layer"], 3)

    def test_pinyin_initials_query(self):
        payload = self.app.agent.search("cyj", "novel", "u_test")
        results = payload["groups"]["novel"]["results"]
        self.assertTrue(results)
        self.assertEqual(results[0]["work"]["id"], "n_changye")
        self.assertEqual(results[0]["layer"], 0)

    def test_sentence_fragment_is_top_hit(self):
        payload = self.app.agent.search("她说她从来没有恨过我", "novel", "u_test")
        first = payload["groups"]["novel"]["results"][0]
        self.assertEqual(first["layer"], 0)
        self.assertEqual(first["layer_label"], "原文句子命中")
        self.assertTrue(first["verified"])

    def test_audit_log_records_search(self):
        self.app.agent.search("林昭", "novel", "u_audit")
        events = []
        for _ in range(30):          # 审计是异步写，最多等 1.5s
            events = self.app.audit.tail(10)
            if any(e.get("event") == "search" and e.get("user_id") == "u_audit" for e in events):
                break
            time.sleep(0.05)
        self.assertTrue(any(e.get("event") == "search" and e.get("user_id") == "u_audit" for e in events))


if __name__ == "__main__":
    unittest.main()
