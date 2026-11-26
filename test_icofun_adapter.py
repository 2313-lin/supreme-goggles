"""小齐 API（短剧聚合源）适配器测试：本地假服务，不访问真实网络。"""
from __future__ import annotations

import json
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlsplit

from nds.adapters import AdapterError, AdapterHub
from nds.adapters_icofun import IcofunShortDramaAdapter
from nds.corpus import Corpus
from nds.registry import PlatformDef
from nds.understanding import understand


class _IcofunMock(BaseHTTPRequestHandler):
    mode = "ok"          # ok | empty | fail_status | http_429
    last_query: dict = {}
    calls = 0

    def log_message(self, *args):
        return

    def do_GET(self):  # noqa: N802
        type(self).calls += 1
        parts = urlsplit(self.path)
        params = {k: v[0] for k, v in parse_qs(parts.query).items()}
        type(self).last_query = params
        if self.mode == "http_429":
            self.send_response(429)
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        if self.mode == "fail_status":
            self._json({"状态": "失败", "提示信息": "参数错误", "数据列表": []})
            return
        if self.mode == "empty":
            self._json({"状态": "成功", "提示信息": "无结果", "总数量": 0,
                        "当前返回数量": 0, "数量限制": 5, "数据列表": []})
            return
        self._json({
            "状态": "成功",
            "提示信息": "ok",
            "总数量": 2,
            "当前返回数量": 2,
            "数量限制": 5,
            "数据列表": [
                {"选择序号": 1, "ID": 12333, "短剧名": "闪婚 急诊", "封面图链接": "https://img.example/1.jpg", "总集数": 103},
                {"选择序号": 2, "ID": 54354, "短剧名": "闪婚妈妈", "封面图链接": "https://img.example/2.jpg", "总集数": 66},
            ],
        })

    def _json(self, payload: dict) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


class IcofunAdapterTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.corpus = Corpus.load()
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), _IcofunMock)
        cls.base = f"http://127.0.0.1:{cls.server.server_address[1]}"
        threading.Thread(target=cls.server.serve_forever, kwargs={"poll_interval": 0.05},
                         daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()

    def setUp(self):
        _IcofunMock.mode = "ok"
        _IcofunMock.calls = 0
        _IcofunMock.last_query = {}

    def adapter(self, **kwargs):
        return IcofunShortDramaAdapter(platform_id="agg_drama", base_url=self.base, **kwargs)

    def test_maps_metadata_to_candidates(self):
        cands = self.adapter().search(understand("闪婚", self.corpus, "drama"), "short_drama")
        self.assertEqual(2, len(cands))
        first = cands[0]
        self.assertEqual("闪婚 急诊", first.work.title)
        self.assertEqual(103, first.work.episodes)
        self.assertEqual("https://img.example/1.jpg", first.work.cover)
        self.assertEqual("short_drama", first.work.type)
        self.assertEqual({"agg_drama"}, first.platforms)
        self.assertEqual("name_fuzzy", first.channel)
        self.assertFalse(first.evidence[0].verified)
        self.assertEqual("闪婚", _IcofunMock.last_query.get("msg"))
        self.assertEqual("5", _IcofunMock.last_query.get("num"))

    def test_exact_title_gets_name_exact(self):
        cands = self.adapter().search(understand("闪婚 急诊", self.corpus, "drama"), "short_drama")
        self.assertEqual("name_exact", cands[0].channel)
        self.assertGreaterEqual(cands[0].score, 0.95)

    def test_sentence_intent_does_not_hit_remote(self):
        cands = self.adapter().search(
            understand("她说她从来没有恨过我，只是不想再见到我", self.corpus, "drama"),
            "short_drama")
        self.assertEqual([], cands)
        self.assertEqual(0, _IcofunMock.calls)     # 完全没发请求

    def test_empty_list(self):
        _IcofunMock.mode = "empty"
        self.assertEqual([], self.adapter().search(understand("闪婚", self.corpus, "drama"), "short_drama"))

    def test_status_failure_raises(self):
        _IcofunMock.mode = "fail_status"
        with self.assertRaises(AdapterError):
            self.adapter().search(understand("闪婚", self.corpus, "drama"), "short_drama")

    def test_429_raises(self):
        _IcofunMock.mode = "http_429"
        with self.assertRaises(AdapterError):
            self.adapter().search(understand("闪婚", self.corpus, "drama"), "short_drama")

    def test_theme_keyword_is_used(self):
        self.adapter().search(understand("先婚后爱", self.corpus, "drama"), "short_drama")
        self.assertEqual("闪婚", _IcofunMock.last_query.get("msg"))

    def test_hub_builds_live_adapter_from_config(self):
        defn = PlatformDef(id="agg_drama", type="drama", role="aggregator", stage=4,
                           adapter="icofun_shortdrama", base_url=self.base, timeout_ms=4000)
        hub = AdapterHub(self.corpus)
        adapter = hub.get("agg_drama", defn)
        self.assertIsInstance(adapter, IcofunShortDramaAdapter)


if __name__ == "__main__":
    unittest.main()
