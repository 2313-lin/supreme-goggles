"""平台注册表、熔断器与降级链测试（§4 / §7）。"""
from __future__ import annotations

import json
import threading
import time
import unittest

from nds.registry import CircuitBreaker, PlatformDef, PlatformRegistry, PlatformRuntime


class BreakerTests(unittest.TestCase):
    def test_opens_after_threshold_and_recovers(self):
        breaker = CircuitBreaker(fail_threshold=2, cooldown_s=0.05)
        self.assertTrue(breaker.can_try())
        breaker.record_failure("boom")
        self.assertEqual(breaker.state, "closed")
        breaker.record_failure("boom")
        self.assertEqual(breaker.state, "open")
        self.assertFalse(breaker.can_try())

        time.sleep(0.06)
        self.assertTrue(breaker.can_try())       # half_open 试探
        self.assertEqual(breaker.state, "half_open")
        breaker.record_failure("still bad")
        self.assertEqual(breaker.state, "open")
        self.assertFalse(breaker.can_try())

        time.sleep(0.06)
        self.assertTrue(breaker.can_try())
        breaker.record_success()
        self.assertEqual(breaker.state, "closed")
        self.assertEqual(breaker.failures, 0)

    def test_half_open_admits_only_one_concurrent_trial(self):
        breaker = CircuitBreaker(fail_threshold=1, cooldown_s=0.02)
        breaker.record_failure("boom")
        time.sleep(0.03)

        results: list[bool] = []
        barrier = threading.Barrier(8)

        def attempt():
            barrier.wait()
            results.append(breaker.can_try())

        threads = [threading.Thread(target=attempt) for _ in range(8)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()

        self.assertEqual(1, sum(1 for allowed in results if allowed))
        self.assertEqual("half_open", breaker.state)
        self.assertFalse(breaker.available())      # 试探进行中，只读探测也为 False
        breaker.record_success()
        self.assertTrue(breaker.available())
        self.assertTrue(breaker.can_try())

    def test_stale_trial_is_released_after_timeout(self):
        breaker = CircuitBreaker(fail_threshold=1, cooldown_s=0.01, trial_timeout_s=0.05)
        breaker.record_failure("boom")
        time.sleep(0.02)
        self.assertTrue(breaker.can_try())       # 占用试探名额
        self.assertFalse(breaker.can_try())
        time.sleep(0.06)                          # 超过 trial_timeout_s，旧名额作废
        self.assertTrue(breaker.can_try())


class RegistryTests(unittest.TestCase):
    def test_plan_order(self):
        registry = PlatformRegistry.load()
        novel_plan = registry.plan("novel")
        self.assertEqual(novel_plan[0], ["fanqie"])
        self.assertEqual(novel_plan[1], ["qimao"])
        self.assertEqual(novel_plan[2], ["shuqi"])
        self.assertEqual(novel_plan[3], ["midu", "lianshang"])

        drama_plan = registry.plan("short_drama")
        self.assertEqual(drama_plan[0], ["hongguo"])
        self.assertEqual(drama_plan[1], ["hema"])

    def test_snapshot_contains_breaker_state(self):
        registry = PlatformRegistry.load()
        snap = {p["id"]: p for p in registry.snapshot()}
        self.assertEqual(snap["fanqie"]["state"], "healthy")
        self.assertIn("breaker", snap["fanqie"])
        self.assertIn("health_score", snap["fanqie"])

    def test_simulate_down_and_reset(self):
        registry = PlatformRegistry.load()
        registry.simulate("fanqie", down=True)
        self.assertFalse(registry.get("fanqie").can_try())
        registry.simulate("fanqie", down=False, reset_breaker=True)
        self.assertTrue(registry.get("fanqie").can_try())

    def test_unknown_platform_returns_none(self):
        registry = PlatformRegistry.load()
        self.assertIsNone(registry.get("not_exist"))

    def test_disabled_aggregator_is_registered_but_skipped(self):
        registry = PlatformRegistry.load()
        agg = registry.get("agg_novel")
        self.assertIsNotNone(agg)
        self.assertFalse(agg.defn.enabled)
        self.assertFalse(agg.can_try())

    def test_breaker_params_come_from_platform_config(self):
        from nds.registry import PlatformDef, PlatformRuntime
        defn = PlatformDef(id="x", type="novel", role="backup", stage=2,
                           adapter="local_corpus", breaker_threshold=2, breaker_cooldown_s=0.05)
        rt = PlatformRuntime(defn=defn)
        self.assertEqual(rt.breaker.fail_threshold, 2)
        self.assertAlmostEqual(rt.breaker.cooldown_s, 0.05)
        rt.report_failure("a")
        rt.report_failure("b")
        self.assertEqual(rt.breaker.state, "open")
        self.assertFalse(rt.can_try())
        time.sleep(0.06)
        self.assertTrue(rt.can_try())
        self.assertEqual(rt.breaker.state, "half_open")
        rt.report_success(5)
        self.assertEqual(rt.breaker.state, "closed")

    def test_snapshot_does_not_expose_api_key(self):
        defn = PlatformDef(id="paid", type="drama", role="aggregator", stage=4,
                           adapter="http_json", api_key="secret-key",
                           api_key_header="X-API-Key", headers={"X-Trace": "1"})
        payload = PlatformRuntime(defn=defn).to_dict()
        self.assertNotIn("api_key", payload)
        self.assertNotIn("secret-key", json.dumps(payload, ensure_ascii=False))

    def test_reload_preserves_runtime_breaker_state(self):
        registry = PlatformRegistry.load()
        runtime = registry.get("fanqie")
        for _ in range(3):
            runtime.breaker.record_failure("boom")
        self.assertEqual(runtime.breaker.state, "open")

        fresh = PlatformRegistry.load()
        registry.reload(fresh)
        reloaded = registry.get("fanqie")
        self.assertEqual(reloaded.breaker.state, "open")
        self.assertEqual(reloaded.breaker.failures, 3)
        self.assertFalse(reloaded.can_try())


if __name__ == "__main__":
    unittest.main()
