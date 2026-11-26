"""L1 缓存测试。"""
from __future__ import annotations

import time
import types
import unittest

from nds.adapters import AdapterHub
from nds.cache import CachedAdapter, TTLCache
from nds.corpus import Corpus
from nds.registry import PlatformDef


class _Inner:
    name = "fake"
    platform_id = "p1"

    def __init__(self):
        self.calls = 0

    def search(self, uq, kind):
        self.calls += 1
        return [f"result:{uq.raw}"]

    def health(self):
        return True

    def detail(self, work_id):
        return {"id": work_id}


def uq(raw: str):
    return types.SimpleNamespace(raw=raw)


class TTLCacheTests(unittest.TestCase):
    def test_hit_miss_and_stats(self):
        cache = TTLCache(ttl_s=10, max_entries=4)
        self.assertIsNone(cache.get("a"))
        cache.set("a", [1])
        self.assertEqual([1], cache.get("a"))
        stats = cache.stats()
        self.assertEqual(1, stats["hits"])
        self.assertEqual(1, stats["misses"])
        self.assertEqual(1, stats["entries"])
        self.assertEqual(50.0, round(stats["hit_rate"] * 100, 1))

    def test_expiry(self):
        cache = TTLCache(ttl_s=0.05, max_entries=4)
        cache.set("k", "v")
        self.assertEqual("v", cache.get("k"))
        time.sleep(0.07)
        self.assertIsNone(cache.get("k"))

    def test_lru_eviction(self):
        cache = TTLCache(ttl_s=10, max_entries=2)
        cache.set("a", 1)
        cache.set("b", 2)
        cache.get("a")            # a 变成最近使用
        cache.set("c", 3)         # 淘汰 b
        self.assertEqual(1, cache.get("a"))
        self.assertIsNone(cache.get("b"))
        self.assertEqual(1, cache.stats()["evictions"])

    def test_clear(self):
        cache = TTLCache(ttl_s=10)
        cache.set("a", 1)
        cache.clear()
        self.assertIsNone(cache.get("a"))
        self.assertEqual(0, len(cache))


class CachedAdapterTests(unittest.TestCase):
    def test_second_call_hits_cache(self):
        inner = _Inner()
        cache = TTLCache(ttl_s=10)
        adapter = CachedAdapter(inner, cache)
        first = adapter.search(uq("闪婚"), "short_drama")
        second = adapter.search(uq("闪婚"), "short_drama")
        self.assertEqual(first, second)
        self.assertEqual(1, inner.calls)
        self.assertEqual(1, cache.stats()["hits"])

    def test_different_query_misses(self):
        inner = _Inner()
        adapter = CachedAdapter(inner, TTLCache(ttl_s=10))
        adapter.search(uq("闪婚"), "short_drama")
        adapter.search(uq("重生"), "short_drama")
        self.assertEqual(2, inner.calls)

    def test_base_url_change_misses(self):
        inner = _Inner()
        inner.base_url = "http://a"
        adapter = CachedAdapter(inner, TTLCache(ttl_s=10))
        adapter.search(uq("闪婚"), "short_drama")
        inner.base_url = "http://b"
        adapter.search(uq("闪婚"), "short_drama")
        self.assertEqual(2, inner.calls)

    def test_punctuation_normalized_in_key(self):
        inner = _Inner()
        adapter = CachedAdapter(inner, TTLCache(ttl_s=10))
        adapter.search(uq("闪婚！"), "short_drama")
        adapter.search(uq(" 闪婚 "), "short_drama")
        self.assertEqual(1, inner.calls)

    def test_delegates_health_and_detail(self):
        adapter = CachedAdapter(_Inner(), TTLCache())
        self.assertTrue(adapter.health())
        self.assertEqual({"id": "x"}, adapter.detail("x"))


class HubCacheTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.corpus = Corpus.load()

    def test_http_adapter_is_wrapped_and_local_is_not(self):
        hub = AdapterHub(self.corpus, cache=TTLCache(ttl_s=10))
        http_defn = PlatformDef(id="agg", type="drama", role="aggregator", stage=4,
                                adapter="http_json", base_url="http://127.0.0.1:1", timeout_ms=100)
        local_defn = PlatformDef(id="fanqie", type="novel", role="primary", stage=1,
                                 adapter="local_corpus")
        self.assertIsInstance(hub.get("agg", http_defn), CachedAdapter)
        self.assertNotIsInstance(hub.get("fanqie", local_defn), CachedAdapter)

    def test_disabled_cache_returns_raw_adapter(self):
        hub = AdapterHub(self.corpus, cache=None)
        defn = PlatformDef(id="agg", type="drama", role="aggregator", stage=4,
                           adapter="http_json", base_url="http://127.0.0.1:1", timeout_ms=100)
        self.assertNotIsInstance(hub.get("agg", defn), CachedAdapter)


if __name__ == "__main__":
    unittest.main()
