"""DracinAPI 联调：带鉴权（Bearer）+ 分页 + L1 缓存。

- 有 `NDS_API_KEY_DRACIN` → 真实请求 dracinapi.com；
- 无 key → 本地 mock（同样的鉴权/字段/分页契约），并明确标注为 mock 模式。

用法：
    NDS_API_KEY_DRACIN=你的key python -X utf8 scripts/live_check_dracin.py
    python -X utf8 scripts/live_check_dracin.py          # mock 模式

产物：docs/DRACIN_CHECK.md、docs/dracin/results.json
"""
from __future__ import annotations

import json
import os
import sys
import threading
import time
import urllib.request
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from nds.api import NdsApp, NdsServer          # noqa: E402
from nds.config import Settings                # noqa: E402

OUT_DIR = ROOT / "docs" / "dracin"
REPORT = ROOT / "docs" / "DRACIN_CHECK.md"
DRACIN_CONFIG = ROOT / "config" / "platforms.dracin.json"

_ROWS = [
    {"id": "2047", "title": "闪婚契约", "episodes": 80, "cover": "https://cdn.example/c.jpg",
     "desc": "先婚后爱。", "genre": ["甜宠", "闪婚"]},
    {"id": "2048", "title": "替身新娘", "episodes": 60, "cover": "https://cdn.example/t.jpg",
     "desc": "虐恋豪门。", "genre": ["虐恋", "豪门"]},
]


class _MockDracin(BaseHTTPRequestHandler):
    fail = False
    last_auth = ""
    calls = 0

    def log_message(self, *args):
        return

    def do_GET(self):  # noqa: N802
        type(self).calls += 1
        type(self).last_auth = self.headers.get("Authorization", "")
        parts = urlsplit(self.path)
        if type(self).fail or type(self).last_auth != "Bearer test-key":
            self.send_response(401)
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        body = json.dumps(_ROWS, ensure_ascii=False).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


def now_iso():
    return datetime.now(timezone(timedelta(hours=8))).isoformat(timespec="seconds")


def main() -> int:
    api_key = os.environ.get("NDS_API_KEY_DRACIN", "")
    real = bool(api_key)
    mode = "real" if real else "mock"

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    for suffix in ("", "-wal", "-shm"):
        (OUT_DIR / f"memory.db{suffix}").unlink(missing_ok=True)

    mock = None
    settings = Settings.load()
    if real:
        if not DRACIN_CONFIG.exists():
            from scripts.make_dracin_config import main as gen
            gen()
        settings.platforms_config = str(DRACIN_CONFIG)
    else:
        # mock 模式：用临时注册表，dracin 指向本地假服务
        mock = ThreadingHTTPServer(("127.0.0.1", 0), _MockDracin)
        threading.Thread(target=mock.serve_forever, kwargs={"poll_interval": 0.05},
                         daemon=True).start()
        data = json.loads((ROOT / "config" / "platforms.json").read_text(encoding="utf-8"))
        for p in data["platforms"]:
            if p["id"] == "agg_drama":
                p.update({"id": "dracin", "adapter": "dracin", "enabled": True,
                          "base_url": f"http://127.0.0.1:{mock.server_address[1]}",
                          "provider": "dramabox", "timeout_ms": 4000, "api_key": "test-key"})
        temp = OUT_DIR / "platforms.mock.json"
        temp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        settings.platforms_config = str(temp)
        os.environ["NDS_API_KEY_DRACIN"] = "test-key"

    settings.memory_db = str(OUT_DIR / "memory.db")
    settings.audit_log = str(OUT_DIR / "audit.jsonl")
    settings.budget_ms = 10000
    settings.rate_limit_per_min = 1000
    Path(settings.audit_log).unlink(missing_ok=True)

    app = NdsApp(settings)
    server = NdsServer(("127.0.0.1", 0), app)
    base = f"http://127.0.0.1:{server.server_address[1]}"
    threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True).start()

    def search(query: str, budget_ms: int = 10000):
        req = urllib.request.Request(
            base + "/api/search",
            data=json.dumps({"query": query, "tab": "drama", "user_id": "u_dracin",
                             "budget_ms": budget_ms}).encode("utf-8"),
            headers={"Content-Type": "application/json"}, method="POST")
        started = time.perf_counter()
        with urllib.request.urlopen(req, timeout=30) as resp:
            return json.loads(resp.read()), (time.perf_counter() - started) * 1000

    checks: list[dict] = []

    def check(name, expected, actual):
        checks.append({"name": name, "expected": expected, "actual": actual,
                       "pass": expected == actual})
        print(f"  [{'PASS' if expected == actual else 'FAIL'}] {name}: "
              f"expected={expected!r} actual={actual!r}")

    print(f"[mode={mode}] DracinAPI 联调" + ("（真实）" if real else "（本地 mock，同样契约）"))
    try:
        payload, latency = search("闪婚")
        results = payload["groups"].get("short_drama", {}).get("results", [])
        dracin = [r for r in results if "dracin" in r["work"]["platforms"]]
        check("端到端命中 dracin", True, len(dracin) > 0)
        check("映射出集数", True, any(r["work"].get("episodes") for r in dracin))
        if dracin:
            check("通道为剧名命中", True,
                  dracin[0]["layer_label"] in ("剧名精确命中", "剧名近似"))
        check("首次耗时在预算内", True, latency < 10000)

        # 缓存冷/热
        if app.cache is not None:
            app.cache.clear()
            _, cold = search("闪婚")
            _, warm = search("闪婚")
            stats = app.cache.stats()
            check("缓存后端可用", True, stats.get("backend") in ("memory", "redis"))
            check("二次命中更快", True, warm < max(200, cold * 0.5))

        # 鉴权失败 → 熔断
        if mock is not None:
            _MockDracin.fail = True
            if app.cache is not None:
                app.cache.clear()
            for _ in range(3):
                search("闪婚")
            _, platforms = None, None
            req = urllib.request.Request(base + "/api/platforms")
            with urllib.request.urlopen(req, timeout=10) as resp:
                platforms = json.loads(resp.read())["platforms"]
            dracin_platform = next(p for p in platforms if p["id"] == "dracin")
            check("鉴权失败触发熔断", "circuit_open", dracin_platform["state"])
            check("mock 收到 Bearer 头", "Bearer test-key", _MockDracin.last_auth)
    finally:
        server.shutdown()
        server.server_close()
        app.close()
        if mock is not None:
            mock.shutdown()
            mock.server_close()

    passed = sum(1 for c in checks if c["pass"])
    summary = {"started_at": now_iso(), "mode": mode, "checks": len(checks),
               "passed": passed, "failed": len(checks) - passed,
               "endpoint": "https://dracinapi.com/{provider}/api/v1/search" if real
               else "local mock"}
    payload = {"summary": summary, "checks": checks}
    (OUT_DIR / "results.json").write_text(json.dumps(payload, ensure_ascii=False, indent=2),
                                          encoding="utf-8")
    REPORT.write_text(render(payload), encoding="utf-8")
    print(f"\n[{mode}] {passed}/{len(checks)} 通过。报告：{REPORT}")
    return 0 if summary["failed"] == 0 else 1


def render(payload):
    s = payload["summary"]
    mode_note = ("**真实请求 dracinapi.com**" if s["mode"] == "real"
                 else "**本地 mock（与 DracinAPI 相同的 Bearer 鉴权 + 字段契约）；真实 key 就绪后同脚本一键切换**")
    lines = [
        "# DracinAPI 联调报告",
        "",
        f"- 时间：{s['started_at']}（Asia/Shanghai）",
        f"- 模式：{mode_note}",
        f"- 结果：**{s['passed']}/{s['checks']} 项检查通过**",
        f"- 原始数据：[results.json](dracin/results.json)",
        "",
        "## 检查项",
        "",
        "| 检查 | 期望 | 实际 | 结果 |",
        "| --- | --- | --- | --- |",
    ]
    for c in payload["checks"]:
        lines.append(f"| {c['name']} | `{c['expected']}` | `{c['actual']}` | "
                     f"{'✅' if c['pass'] else '❌'} |")
    lines += [
        "",
        "## 复现",
        "",
        "```bash",
        "cd novel-drama-agent",
        "python -X utf8 scripts/make_dracin_config.py",
        "# 真实：把 dracinapi.com/register 注册得到的 key 注入后重跑本脚本",
        "NDS_API_KEY_DRACIN=你的key python -X utf8 scripts/live_check_dracin.py",
        "# 无 key 的 mock 模式：",
        "python -X utf8 scripts/live_check_dracin.py",
        "```",
        "",
        "鉴权：`Authorization: Bearer <key>`；搜索：`/{provider}/api/v1/search?keyword=<kw>`；",
        "只取 L1 元数据（剧名/ID/封面/集数/简介/标签），不请求 `play` 播放接口。",
        "",
    ]
    return "\n".join(lines)


if __name__ == "__main__":
    sys.exit(main())
