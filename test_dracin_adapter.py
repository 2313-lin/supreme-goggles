"""DracinAPI 适配器测试：本地假服务，验证鉴权头与字段映射。"""
from __future__ import annotations

import json
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlsplit

from nds.adapters import AdapterError, AdapterHub
from nds.adapters_dracin import DracinAdapter
from nds.cache import TTLCache
from nds.corpus import Corpus
from nds.registry import PlatformDef
from nds.understanding import understand

_ROWS = [
    {"id": "2047", "title": "闪婚契约", "episodes": 80, "cover": "https://cdn.example/c.jpg",
     "desc": "先婚后爱。", "genre": ["甜宠", "闪婚"]},
    {"id": "2048", "title": "替身新娘", "episodes": 60, "cover": "https://cdn.example/t.jpg",
     "desc": "虐恋豪门。", "genre": ["虐恋", "豪门"]},
]


class _Mock(BaseHTTPRequestHandler):
    mode = "ok"            # ok | list | unauthorized | paged
    last_path = ""
    last_headers: dict = {}
    last_query: dict = {}

    def log_message(self, *args):
        return

    def do_GET(self):  # noqa: N802
        parts = urlsplit(self.path)
        type(self).last_path = parts.path
        type(self).last_headers = {k.lower(): v for k, v in self.headers.items()}
        type(self).last_query = {k: v[0] for k, v in parse_qs(parts.query).items()}
        if self.mode == "unauthorized":
            self.send_response(401)
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        if self.mode == "list":
            self._json(_ROWS)
            return
        if self.mode == "paged":
            page = self.last_query.get("page", "1")
            if page == "1":
                self._json({"data": _ROWS[:1], "total": 2, "has_more": True})
            else:
                self._json({"data": _ROWS[1:], "total": 2, "has_more": False})
            return
        self._json({"data": _ROWS})

    def _json(self, payload):
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


class DracinAdapterTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.corpus = Corpus.load()
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), _Mock)
        cls.base = f"http://127.0.0.1:{cls.server.server_address[1]}"
        threading.Thread(target=cls.server.serve_forever, kwargs={"poll_interval": 0.05},
                         daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()

    def setUp(self):
        _Mock.mode = "ok"
        _Mock.last_query = {}
        _Mock.last_headers = {}
        _Mock.last_path = ""

    def adapter(self, **kwargs):
        return DracinAdapter(platform_id="dracin", base_url=self.base, provider="dramabox",
                             api_key="test-key", **kwargs)

    def test_requires_api_key(self):
        with self.assertRaises(AdapterError):
            DracinAdapter(platform_id="dracin", base_url=self.base).search(
                understand("闪婚", self.corpus, "drama"), "short_drama")

    def test_sends_bearer_auth(self):
        cands = self.adapter().search(understand("闪婚", self.corpus, "drama"), "short_drama")
        self.assertTrue(cands)
        self.assertEqual("Bearer test-key", _Mock.last_headers.get("authorization"))
        self.assertIn("dramabox/api/v1/search", _Mock.last_path)
        self.assertEqual("闪婚", _Mock.last_query.get("keyword"))

    def test_maps_fields(self):
        cands = self.adapter().search(understand("闪婚", self.corpus, "drama"), "short_drama")
        first = cands[0]
        self.assertEqual("闪婚契约", first.work.title)
        self.assertEqual(80, first.work.episodes)
        self.assertEqual("https://cdn.example/c.jpg", first.work.cover)
        self.assertEqual(["甜宠", "闪婚"], first.work.themes)
        self.assertEqual("name_fuzzy", first.channel)
        self.assertIn("dracin:", first.work.id)

    def test_exact_title_name_exact(self):
        cands = self.adapter().search(understand("闪婚契约", self.corpus, "drama"), "short_drama")
        self.assertEqual("name_exact", cands[0].channel)

    def test_unauthorized_raises(self):
        _Mock.mode = "unauthorized"
        with self.assertRaises(AdapterError):
            self.adapter().search(understand("闪婚", self.corpus, "drama"), "short_drama")

    def test_bare_array_single_page(self):
        _Mock.mode = "list"
        cands = self.adapter(max_pages=2).search(understand("闪婚", self.corpus, "drama"), "short_drama")
        self.assertEqual(2, len(cands))

    def test_paged_contract_fetches_all(self):
        _Mock.mode = "paged"
        cands = self.adapter(max_pages=2).search(understand("闪婚", self.corpus, "drama"), "short_drama")
        self.assertEqual(2, len(cands))
        self.assertEqual("2", _Mock.last_query.get("page"))

    def test_sentence_intent_skips(self):
        self.adapter().search(
            understand("她说她从来没有恨过我，只是想再见到我", self.corpus, "drama"),
            "short_drama")
        self.assertEqual("", _Mock.last_path)

    def test_hub_builds_and_caches(self):
        defn = PlatformDef(id="dracin", type="drama", role="aggregator", stage=4,
                           adapter="dracin", base_url=self.base, provider="dramabox",
                           api_key="test-key", timeout_ms=4000)
        hub = AdapterHub(self.corpus, cache=TTLCache(ttl_s=10))
        adapter = hub.get("dracin", defn)
        self.assertEqual("dracin+cache", adapter.name)


if __name__ == "__main__":
    unittest.main()
