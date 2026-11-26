"""HTTP API 端到端测试（真实起服务，走 127.0.0.1）。"""
from __future__ import annotations

import json
import threading
import unittest
import urllib.error
import urllib.request

from nds.api import NdsServer
from tests.support import make_app


class ApiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app, cls.tmp = make_app()
        cls.server = NdsServer(("127.0.0.1", 0), cls.app)
        cls.base = f"http://127.0.0.1:{cls.server.server_address[1]}"
        cls.thread = threading.Thread(target=cls.server.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.app.close()
        cls.tmp.cleanup()
        cls.thread.join(timeout=2)

    # ----------------------------------------------------------------- utils
    def get(self, path):
        try:
            with urllib.request.urlopen(self.base + path, timeout=5) as resp:
                return resp.status, resp.read()
        except urllib.error.HTTPError as exc:
            return exc.code, exc.read()

    def post(self, path, payload, headers=None):
        req = urllib.request.Request(
            self.base + path,
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json", **(headers or {})},
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=5) as resp:
                return resp.status, json.loads(resp.read())
        except urllib.error.HTTPError as exc:
            return exc.code, json.loads(exc.read())

    # ------------------------------------------------------------------ tests
    def test_health(self):
        status, body = self.get("/api/health")
        self.assertEqual(status, 200)
        data = json.loads(body)
        self.assertEqual(data["status"], "ok")
        self.assertEqual(data["corpus"]["works"], 20)

    def test_index_page(self):
        status, body = self.get("/")
        self.assertEqual(status, 200)
        self.assertIn("夜航".encode("utf-8"), body)

    def test_static_assets(self):
        for path in ("/app.js", "/style.css"):
            status, body = self.get(path)
            self.assertEqual(status, 200)
            self.assertTrue(body)

    def test_examples(self):
        status, body = self.get("/api/examples")
        self.assertEqual(status, 200)
        self.assertTrue(json.loads(body)["examples"])

    def test_search_api(self):
        status, data = self.post("/api/search", {"query": "侯府嫡女", "tab": "novel", "user_id": "u_api"})
        self.assertEqual(status, 200)
        self.assertTrue(data["allowed"])
        results = data["groups"]["novel"]["results"]
        self.assertEqual(results[0]["work"]["title"], "侯府嫡女不好惹")
        self.assertEqual(results[0]["layer"], 0)

    def test_search_rejects_blocked_query(self):
        status, data = self.post("/api/search", {"query": "教我做冰毒的教程", "tab": "all"})
        self.assertEqual(status, 200)
        self.assertFalse(data["allowed"])
        self.assertEqual(data["code"], "blocked_topic")

    def test_bad_json(self):
        req = urllib.request.Request(self.base + "/api/search", data=b"not-json",
                                     headers={"Content-Type": "application/json"}, method="POST")
        with self.assertRaises(urllib.error.HTTPError) as ctx:
            urllib.request.urlopen(req, timeout=5)
        self.assertEqual(ctx.exception.code, 400)

    def test_work_detail_and_click(self):
        status, body = self.get("/api/works/n_changye")
        self.assertEqual(status, 200)
        work = json.loads(body)["work"]
        self.assertEqual(work["title"], "长夜将明")
        self.assertTrue(work["sentences"])

        status, data = self.post("/api/feedback/click",
                                 {"user_id": "u_api", "session_id": "s_api", "work_id": "n_changye"})
        self.assertEqual(status, 200)
        self.assertTrue(data["ok"])

        status, body = self.get("/api/memory?user_id=u_api&session_id=s_api")
        self.assertEqual(status, 200)
        memory = json.loads(body)
        self.assertIn("n_changye", memory["profile"]["clicked_works"])

    def test_work_not_found(self):
        status, _ = self.get("/api/works/nope")
        self.assertEqual(status, 404)

    def test_blocked_topics_api(self):
        status, data = self.post("/api/memory/blocked", {"user_id": "u_api", "topics": ["恐怖"]})
        self.assertEqual(status, 200)
        self.assertEqual(data["blocked_topics"], ["恐怖"])

    def test_memory_delete(self):
        req = urllib.request.Request(self.base + "/api/memory?user_id=u_api", method="DELETE")
        with urllib.request.urlopen(req, timeout=5) as resp:
            self.assertEqual(resp.status, 200)
        status, body = self.get("/api/memory?user_id=u_api")
        self.assertEqual(json.loads(body)["profile"]["clicked_works"], {})

    def test_admin_requires_token(self):
        status, data = self.post("/api/admin/platforms/fanqie/simulate", {"down": True, "confirm": True})
        self.assertEqual(status, 401)
        self.assertEqual(data["error"]["code"], "unauthorized")

    def test_admin_requires_confirmation(self):
        status, data = self.post("/api/admin/platforms/fanqie/simulate", {"down": True},
                                 headers={"X-Admin-Token": self.app.settings.admin_token})
        self.assertEqual(status, 428)
        self.assertEqual(data["error"]["code"], "need_confirmation")

    def test_admin_simulate_and_restore(self):
        headers = {"X-Admin-Token": self.app.settings.admin_token}
        status, data = self.post("/api/admin/platforms/fanqie/simulate",
                                 {"down": True, "confirm": True}, headers=headers)
        self.assertEqual(status, 200)
        self.assertTrue(data["platform"]["forced_down"])

        status, data = self.post("/api/admin/platforms/fanqie/simulate",
                                 {"down": False, "reset_breaker": True, "confirm": True}, headers=headers)
        self.assertEqual(status, 200)
        self.assertFalse(data["platform"]["forced_down"])

    def test_platforms_endpoint(self):
        status, body = self.get("/api/platforms")
        self.assertEqual(status, 200)
        ids = {p["id"] for p in json.loads(body)["platforms"]}
        self.assertIn("fanqie", ids)
        self.assertIn("hongguo", ids)

    def test_stats_contains_cache(self):
        status, body = self.get("/api/stats")
        self.assertEqual(200, status)
        data = json.loads(body)
        self.assertIn("cache", data)
        self.assertGreaterEqual(data["cache"]["max_entries"], 1)

    def test_cache_clear_requires_admin(self):
        status, _ = self.post("/api/cache/clear", {"confirm": True})
        self.assertEqual(401, status)
        headers = {"X-Admin-Token": self.app.settings.admin_token}
        status, data = self.post("/api/cache/clear", {"confirm": True}, headers=headers)
        self.assertEqual(200, status)
        self.assertEqual(0, data["cache"]["entries"])

    def test_unknown_api_404(self):
        status, _ = self.get("/api/nope")
        self.assertEqual(status, 404)

    def test_traversal_blocked(self):
        status, _ = self.get("/../config/settings.json")
        self.assertIn(status, (403, 404))


if __name__ == "__main__":
    unittest.main()
