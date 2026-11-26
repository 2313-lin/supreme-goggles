"""真实 Redis 多实例共享验证。

- 启动本地 mock HTTP 平台（带 300ms 人工延迟，计数调用次数）；
- 两个独立 NdsApp 实例共享同一个 Redis（redis://127.0.0.1:6399/0）；
- 实例 A 冷查询 → 实例 B 同查询命中 Redis 缓存（mock 不再被调用、延迟显著下降）。

用法：
    python -X utf8 scripts/redis_check.py
    可通过 NDS_REDIS_URL 覆盖目标 Redis。
"""
from __future__ import annotations

import json
import os
import sys
import threading
import time
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from nds.api import NdsApp, NdsServer              # noqa: E402
from nds.cache_redis import RedisClient            # noqa: E402
from nds.config import Settings                    # noqa: E402

REDIS_URL = os.environ.get("NDS_REDIS_URL", "redis://127.0.0.1:6399/0")
PREFIX = "nds:redischeck:"


class _Mock(BaseHTTPRequestHandler):
    calls = 0
    delay_s = 0.3

    def log_message(self, *args):
        return

    def do_GET(self):  # noqa: N802
        parts = urlsplit(self.path)
        if parts.path != "/search":
            self.send_response(404)
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        type(self).calls += 1
        time.sleep(type(self).delay_s)
        body = json.dumps({"works": [
            {"id": "r1", "title": "闪婚 急诊（缓存验证）", "episodes": 103,
             "themes": ["闪婚"], "channel": "name_fuzzy", "score": 0.85},
            {"id": "r2", "title": "闪婚妈妈（缓存验证）", "episodes": 66,
             "themes": ["闪婚"], "channel": "name_fuzzy", "score": 0.8},
        ]}, ensure_ascii=False).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


def make_app(mock_base: str, tag: str) -> tuple[NdsApp, NdsServer, str]:
    settings = Settings.load()
    settings.platforms_config = str(_write_config(mock_base))
    settings.cache_backend = "redis"
    settings.redis_url = REDIS_URL
    settings.redis_prefix = PREFIX
    settings.cache_ttl_s = 60
    settings.memory_db = str(ROOT / "docs" / "redischeck" / f"memory_{tag}.db")
    settings.audit_log = str(ROOT / "docs" / "redischeck" / f"audit_{tag}.jsonl")
    settings.budget_ms = 5000
    settings.rate_limit_per_min = 1000
    Path(settings.memory_db).parent.mkdir(parents=True, exist_ok=True)
    Path(settings.memory_db).unlink(missing_ok=True)
    app = NdsApp(settings)
    server = NdsServer(("127.0.0.1", 0), app)
    port = server.server_address[1]
    threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.05},
                     daemon=True).start()
    return app, server, f"http://127.0.0.1:{port}"


def _write_config(mock_base: str) -> Path:
    data = {
        "_comment": "redis_check 专用：仅一个 mock drama 平台",
        "platforms": [{
            "id": "mockagg", "type": "drama", "role": "primary", "stage": 1,
            "adapter": "http_json", "tier": "L1", "content_level": "L1",
            "timeout_ms": 3000, "priority": 10, "enabled": True,
            "base_url": mock_base, "page_size": 20, "max_pages": 1,
        }],
    }
    out = ROOT / "docs" / "redischeck" / "platforms.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    return out


def search(base: str, query: str) -> tuple[dict, float]:
    req = urllib.request.Request(
        base + "/api/search",
        data=json.dumps({"query": query, "tab": "drama", "user_id": "u_redis"}).encode("utf-8"),
        headers={"Content-Type": "application/json"}, method="POST")
    started = time.perf_counter()
    with urllib.request.urlopen(req, timeout=15) as resp:
        return json.loads(resp.read()), (time.perf_counter() - started) * 1000


def main() -> int:
    print(f"[redis_check] target={REDIS_URL}")

    # 前置：确认 Redis 可达并清空命名空间
    client = RedisClient.from_url(REDIS_URL)
    assert client.ping() == "PONG", "Redis 不可达"
    client.delete(*client.scan_keys(f"{PREFIX}*"))
    print("  Redis PING=PONG，命名空间已清空")

    mock = ThreadingHTTPServer(("127.0.0.1", 0), _Mock)
    threading.Thread(target=mock.serve_forever, kwargs={"poll_interval": 0.05},
                     daemon=True).start()
    mock_base = f"http://127.0.0.1:{mock.server_address[1]}"

    app_a, srv_a, base_a = make_app(mock_base, "a")
    app_b, srv_b, base_b = make_app(mock_base, "b")

    checks: list[dict] = []

    def check(name, expected, actual):
        checks.append({"name": name, "expected": expected, "actual": actual, "pass": expected == actual})
        print(f"  [{'PASS' if expected == actual else 'FAIL'}] {name}: expected={expected!r} actual={actual!r}")

    try:
        check("两实例后端均为 redis", "redis", app_a.cache.backend)
        check("实例 B 后端为 redis", "redis", app_b.cache.backend)

        # 实例 A：冷查询
        calls_before = _Mock.calls
        payload_a, cold = search(base_a, "闪婚")
        total_a = payload_a["groups"].get("short_drama", {}).get("total", 0)
        check("实例 A 冷查询命中 mock", 2, total_a)
        check("mock 被调用 1 次", 1, _Mock.calls - calls_before)

        # 实例 B：同查询应命中 Redis
        payload_b, warm = search(base_b, "闪婚")
        total_b = payload_b["groups"].get("short_drama", {}).get("total", 0)
        check("实例 B 结果一致", total_a, total_b)
        check("实例 B 未再调用 mock（命中共享缓存）", 1, _Mock.calls - calls_before)
        check("实例 B 显著更快", True, warm < cold * 0.5)

        stats_b = app_b.cache.stats()
        check("实例 B 缓存命中 ≥1", True, stats_b["hits"] >= 1)
        keys = client.scan_keys(f"{PREFIX}*")
        check("Redis 中存在 1 个共享键", 1, len(keys))
    finally:
        srv_a.shutdown(); srv_a.server_close(); app_a.close()
        srv_b.shutdown(); srv_b.server_close(); app_b.close()
        mock.shutdown(); mock.server_close()
        client.close()

    passed = sum(1 for c in checks if c["pass"])
    payload = {"redis_url": REDIS_URL, "checks": checks, "passed": passed,
               "total": len(checks), "mock_calls": _Mock.calls,
               "cold_ms": round(cold, 1), "warm_ms": round(warm, 1)}
    out_dir = ROOT / "docs" / "redischeck"
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "results.json").write_text(json.dumps(payload, ensure_ascii=False, indent=2),
                                          encoding="utf-8")
    (ROOT / "docs" / "REDIS_CHECK.md").write_text(render(payload), encoding="utf-8")
    print(f"\n{passed}/{len(checks)} 通过。报告：docs/REDIS_CHECK.md")
    return 0 if passed == len(checks) else 1


def render(payload):
    lines = [
        "# Redis 多实例缓存验证报告",
        "",
        f"- 目标 Redis：`{payload['redis_url']}`（真实 redis-server，非 mock）",
        f"- 结果：**{payload['passed']}/{payload['total']} 项检查通过**",
        f"- 冷/热耗时：{payload.get('cold_ms')}ms → {payload.get('warm_ms')}ms",
        f"- 上游 mock 平台总调用次数：{payload['mock_calls']}（两实例共享后应保持 1）",
        f"- 原始数据：[results.json](redischeck/results.json)",
        "",
        "| 检查 | 期望 | 实际 | 结果 |",
        "| --- | --- | --- | --- |",
    ]
    for c in payload["checks"]:
        lines.append(f"| {c['name']} | `{c['expected']}` | `{c['actual']}` | "
                     f"{'✅' if c['pass'] else '❌'} |")
    lines += ["", "复现：", "```bash",
              "redis-server --port 6399 --bind 127.0.0.1",
              "cd novel-drama-agent && python -X utf8 scripts/redis_check.py",
              "```", ""]
    return "\n".join(lines)


if __name__ == "__main__":
    sys.exit(main())
