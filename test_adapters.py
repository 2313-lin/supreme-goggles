"""HTTP JSON 适配器测试：用本地假平台验证契约、网络错误与超时。"""
from __future__ import annotations

import json
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from nds.adapters import AdapterError, HttpJsonAdapter
from nds.corpus import Corpus
from nds.understanding import understand


class _MockPlatform(BaseHTTPRequestHandler):
    """极简假平台：/search 返回固定契约，/health 返回 ok，/boom 返回 500。"""

    fail = False
    delay_s = 0.0
    last_query: dict = {}
    last_headers: dict = {}
    require_key = False
    key_header = "X-API-Key"
    expect_key = "s3cret"
    paged = False

    def log_message(self, *args):
        return

    def do_GET(self):  # noqa: N802
        parsed = urllib_parse(self.path)
        type(self).last_headers = {k.lower(): v for k, v in self.headers.items()}
        if type(self).require_key:
            supplied = self.headers.get(type(self).key_header, "") or parsed["params"].get("key", "")
            if supplied != type(self).expect_key:
                self.send_response(401)
                self.send_header("Content-Length", "0")
                self.end_headers()
                return
        if self.fail:
            self.send_response(500)
            self.end_headers()
            self.wfile.write(b'{"error":"boom"}')
            return
        if parsed["path"] == "/health":
            self._json({"ok": True})
            return
        if parsed["path"] == "/search":
            type(self).last_query = parsed["params"]
            if self.delay_s:
                import time
                time.sleep(self.delay_s)
            if self.paged:
                page = parsed["params"].get("page", "1")
                if page == "1":
                    self._json({"works": [
                        {"id": "mock-1", "title": "长夜将明（镜像）", "author": "沈砚",
                         "themes": ["悬疑"], "channel": "character", "score": 0.9,
                         "sentences": [{"text": "她说她从来没有恨过我。", "type": "narration"}]},
                        {"id": "mock-2", "title": "深海回声（镜像）", "author": "程野",
                         "themes": ["科幻"], "channel": "title_fuzzy", "score": 0.8},
                    ], "total": 3, "has_more": True})
                else:
                    self._json({"works": [
                        {"id": "mock-3", "title": "北城有雪（镜像）", "author": "温言",
                         "themes": ["虐恋"], "channel": "title_fuzzy", "score": 0.7},
                    ], "total": 3, "has_more": False})
                return
            works = [{
                "id": "mock-1",
                "title": "长夜将明（镜像）",
                "author": "沈砚",
                "summary": "镜像平台的同作品",
                "themes": ["悬疑", "复仇"],
                "channel": "character",
                "score": 0.88,
                "sentences": [{
                    "text": "她说她从来没有恨过我，只是不想再见到我。",
                    "type": "narration",
                    "position": "镜像第1章",
                }],
            }]
            self._json({"works": works})
            return
        self.send_response(404)
        self.end_headers()

    def _json(self, payload: dict):
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        try:
            self.wfile.write(body)
        except (ConnectionError, OSError):
            pass      # 客户端超时主动断开时忽略


def urllib_parse(path: str) -> dict:
    from urllib.parse import parse_qs, urlsplit
    parts = urlsplit(path)
    return {"path": parts.path, "params": {k: v[0] for k, v in parse_qs(parts.query).items()}}


class HttpJsonAdapterTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.corpus = Corpus.load()
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), _MockPlatform)
        cls.base = f"http://127.0.0.1:{cls.server.server_address[1]}"
        threading.Thread(target=cls.server.serve_forever, kwargs={"poll_interval": 0.05},
                         daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()

    def setUp(self):
        _MockPlatform.fail = False
        _MockPlatform.delay_s = 0.0
        _MockPlatform.require_key = False
        _MockPlatform.paged = False
        _MockPlatform.last_headers = {}

    def test_maps_contract_to_candidate(self):
        adapter = HttpJsonAdapter(platform_id="mock", base_url=self.base, timeout_ms=800)
        uq = understand("林昭", self.corpus, "novel")
        cands = adapter.search(uq, "novel")
        self.assertEqual(len(cands), 1)
        cand = cands[0]
        self.assertEqual(cand.work.title, "长夜将明（镜像）")
        self.assertEqual(cand.channel, "character")
        self.assertEqual(cand.platforms, {"mock"})
        self.assertEqual(cand.work.sources[0].platform, "mock")
        self.assertEqual(cand.work.sentences[0].text, "她说她从来没有恨过我，只是不想再见到我。")
        self.assertFalse(cand.evidence[0].verified)
        self.assertIn("q", _MockPlatform.last_query)
        self.assertEqual(_MockPlatform.last_query["type"], "novel")

    def test_unknown_channel_falls_back(self):
        adapter = HttpJsonAdapter(platform_id="mock", base_url=self.base)
        uq = understand("林昭", self.corpus, "novel")
        cand = adapter._to_candidate({"title": "某作品", "channel": "not_a_channel"}, "novel", uq)
        self.assertIsNotNone(cand)
        self.assertEqual(cand.channel, "title_fuzzy")

    def test_http_500_raises_adapter_error(self):
        adapter = HttpJsonAdapter(platform_id="mock", base_url=self.base)
        _MockPlatform.fail = True
        with self.assertRaises(AdapterError):
            adapter.search(understand("林昭", self.corpus, "novel"), "novel")

    def test_timeout_raises_adapter_error(self):
        adapter = HttpJsonAdapter(platform_id="mock", base_url=self.base, timeout_ms=100)
        _MockPlatform.delay_s = 0.4
        with self.assertRaises(AdapterError):
            adapter.search(understand("林昭", self.corpus, "novel"), "novel")

    def test_missing_base_url(self):
        adapter = HttpJsonAdapter(platform_id="mock", base_url="")
        with self.assertRaises(AdapterError):
            adapter.search(understand("林昭", self.corpus, "novel"), "novel")
        self.assertFalse(adapter.health())

    def test_health(self):
        self.assertTrue(HttpJsonAdapter(platform_id="mock", base_url=self.base).health())
        _MockPlatform.fail = True
        self.assertFalse(HttpJsonAdapter(platform_id="mock", base_url=self.base).health())

    def test_api_key_in_header(self):
        _MockPlatform.require_key = True
        adapter = HttpJsonAdapter(platform_id="mock", base_url=self.base,
                                  api_key="s3cret", api_key_header="X-API-Key")
        cands = adapter.search(understand("林昭", self.corpus, "novel"), "novel")
        self.assertEqual(1, len(cands))
        self.assertEqual("s3cret", _MockPlatform.last_headers.get("x-api-key"))

    def test_api_key_as_query_param(self):
        _MockPlatform.require_key = True
        adapter = HttpJsonAdapter(platform_id="mock", base_url=self.base, api_key="s3cret")
        cands = adapter.search(understand("林昭", self.corpus, "novel"), "novel")
        self.assertEqual(1, len(cands))
        self.assertEqual("s3cret", _MockPlatform.last_query.get("key"))
        self.assertNotIn("x-api-key", _MockPlatform.last_headers)

    def test_extra_headers_merged(self):
        adapter = HttpJsonAdapter(platform_id="mock", base_url=self.base,
                                  headers={"X-Trace-Id": "trace-1"})
        adapter.search(understand("林昭", self.corpus, "novel"), "novel")
        self.assertEqual("trace-1", _MockPlatform.last_headers.get("x-trace-id"))

    def test_api_key_not_leaked_in_error(self):
        _MockPlatform.require_key = True
        adapter = HttpJsonAdapter(platform_id="mock", base_url=self.base, api_key="s3cret",
                                  api_key_header="X-Wrong-Key")
        with self.assertRaises(AdapterError) as ctx:
            adapter.search(understand("林昭", self.corpus, "novel"), "novel")
        self.assertNotIn("s3cret", str(ctx.exception))

    def test_pagination_fetches_all_pages(self):
        _MockPlatform.paged = True
        adapter = HttpJsonAdapter(platform_id="mock", base_url=self.base, page_size=2, max_pages=2)
        cands = adapter.search(understand("镜像", self.corpus, "novel"), "novel")
        self.assertEqual(3, len(cands))
        self.assertEqual("2", _MockPlatform.last_query.get("page"))

    def test_pagination_respects_max_pages(self):
        _MockPlatform.paged = True
        adapter = HttpJsonAdapter(platform_id="mock", base_url=self.base, page_size=2, max_pages=1)
        cands = adapter.search(understand("镜像", self.corpus, "novel"), "novel")
        self.assertEqual(2, len(cands))
        self.assertEqual("1", _MockPlatform.last_query.get("page"))


if __name__ == "__main__":
    unittest.main()
