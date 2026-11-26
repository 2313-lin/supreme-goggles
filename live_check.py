"""真实聚合 API 联调：小齐 API「短剧搜索与排名榜」。

- 使用 config/platforms.live.json（agg_drama → icofun_shortdrama，真实网络）
- 起真实 HTTP 服务，跑端到端搜索；再由本地端口接不通的地址验证熔断/降级
- 产物：docs/LIVE_CHECK.md、docs/live/results.json
- 接口为公开访问（无需密钥），仅取 L1 元数据（剧名/ID/封面/集数），不请求视频链接

用法：python -X utf8 scripts/live_check.py
"""
from __future__ import annotations

import json
import statistics
import sys
import threading
import time
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from nds.adapters_icofun import IcofunShortDramaAdapter   # noqa: E402
from nds.api import NdsApp, NdsServer                    # noqa: E402
from nds.config import Settings                          # noqa: E402
from nds.understanding import understand                 # noqa: E402

OUT_DIR = ROOT / "docs" / "live"
REPORT = ROOT / "docs" / "LIVE_CHECK.md"
LIVE_CONFIG = ROOT / "config" / "platforms.live.json"


class Client:
    def __init__(self, base: str):
        self.base = base

    def _call(self, method: str, path: str, payload=None):
        data = None if payload is None else json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(self.base + path, data=data,
                                     headers={"Content-Type": "application/json"}, method=method)
        started = time.perf_counter()
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                body = json.loads(resp.read())
                return resp.status, body, (time.perf_counter() - started) * 1000
        except urllib.error.HTTPError as exc:
            return exc.code, json.loads(exc.read()), (time.perf_counter() - started) * 1000

    def get(self, path):
        return self._call("GET", path)

    def search(self, query: str, tab: str = "drama", budget_ms: int = 6000):
        return self._call("POST", "/api/search", {"query": query, "tab": tab,
                                                  "user_id": "u_live", "budget_ms": budget_ms})


def now_iso() -> str:
    return datetime.now(timezone(timedelta(hours=8))).isoformat(timespec="seconds")


class LiveCheck:
    def __init__(self):
        self.checks: list[dict] = []
        self.direct: list[dict] = []
        self.end_to_end: list[dict] = []
        self.failure: list[dict] = []

    # ------------------------------------------------------------------ setup
    def setup(self):
        if not LIVE_CONFIG.exists():
            raise SystemExit("缺少 config/platforms.live.json，请先运行 scripts/make_live_config.py")
        OUT_DIR.mkdir(parents=True, exist_ok=True)
        for suffix in ("", "-wal", "-shm"):
            (OUT_DIR / f"memory.db{suffix}").unlink(missing_ok=True)
        settings = Settings.load()
        settings.platforms_config = str(LIVE_CONFIG)
        settings.memory_db = str(OUT_DIR / "memory.db")
        settings.audit_log = str(OUT_DIR / "audit.jsonl")
        settings.budget_ms = 6000
        settings.rate_limit_per_min = 1000
        Path(settings.audit_log).unlink(missing_ok=True)

        self.app = NdsApp(settings)
        self.settings = settings
        self.server = NdsServer(("127.0.0.1", 0), self.app)
        self.base = f"http://127.0.0.1:{self.server.server_address[1]}"
        threading.Thread(target=self.server.serve_forever, kwargs={"poll_interval": 0.05},
                         daemon=True).start()
        self.client = Client(self.base)

        runtime = self.app.registry.get("agg_drama")
        self.runtime = runtime
        self.adapter = self.app.hub.get("agg_drama", runtime.defn)
        self.raw_adapter = getattr(self.adapter, "inner", self.adapter)   # 绕过缓存测真实延迟
        self.original_base = runtime.defn.base_url

    def teardown(self):
        self.server.shutdown()
        self.server.server_close()
        self.app.close()

    # ------------------------------------------------------------------ check
    def check(self, name: str, expected, actual) -> None:
        ok = expected == actual
        self.checks.append({"name": name, "expected": expected, "actual": actual, "pass": ok})
        print(f"  [{'PASS' if ok else 'FAIL'}] {name}: expected={expected!r} actual={actual!r}")

    # ------------------------------------------------------------- direct api
    def probe_direct(self):
        print("\n[1/5] 直连真实聚合接口")
        title = self.runtime.defn.adapter
        self.check("联调配置启用 agg_drama", True, self.runtime.defn.enabled)
        self.check("适配器为 icofun_shortdrama", "icofun_shortdrama", title)
        self.check("适配器类型正确", True, isinstance(self.raw_adapter, IcofunShortDramaAdapter))

        for query in ("闪婚", "重生", "总裁", "穿越"):
            started = time.perf_counter()
            try:
                uq = understand(query, self.app.corpus, "drama")
                cands = self.raw_adapter.search(uq, "short_drama")   # 直连，绕过缓存
                elapsed = (time.perf_counter() - started) * 1000
                self.direct.append({
                    "query": query, "ok": True, "count": len(cands), "latency_ms": round(elapsed, 1),
                    "titles": [c.work.title for c in cands],
                    "episodes": [c.work.episodes for c in cands],
                })
                print(f"  {query}: {len(cands)} 条 / {elapsed:.0f}ms / {[c.work.title for c in cands]}")
            except Exception as exc:                       # noqa: BLE001 网络问题如实记录
                elapsed = (time.perf_counter() - started) * 1000
                self.direct.append({"query": query, "ok": False, "error": f"{type(exc).__name__}: {exc}",
                                    "latency_ms": round(elapsed, 1)})
                print(f"  {query}: 失败 {exc}")
            time.sleep(0.4)                                # 尊重 429 限流

        ok_rows = [row for row in self.direct if row.get("ok") and row.get("count")]
        latencies = [row["latency_ms"] for row in self.direct if row.get("ok")]
        self.check("真实接口至少 3/4 查询有结果", True, len(ok_rows) >= 3)
        if latencies:
            print(f"  真实接口延迟：p50={statistics.median(latencies):.0f}ms max={max(latencies):.0f}ms")

    # ---------------------------------------------------------- end to end
    def probe_http(self):
        print("\n[2/5] 端到端 HTTP 搜索（真实聚合源参与）")
        if self.app.cache is not None:
            self.app.cache.clear()          # 保证首轮走真实网络
        for query in ("闪婚", "重生", "总裁", "穿越"):
            status, payload, latency = self.client.search(query, "drama")
            if status != 200:
                self.end_to_end.append({"query": query, "ok": False, "status": status})
                continue
            results = payload["groups"].get("short_drama", {}).get("results", [])
            agg_hits = [item for item in results
                        if "agg_drama" in item["work"]["platforms"]]
            row = {
                "query": query, "ok": True, "total": len(results), "agg_hits": len(agg_hits),
                "elapsed_ms": round(latency, 1), "degraded": payload["degraded"],
                "fallback_stage": payload["fallback_stage"],
                "top": [{"title": item["work"]["title"], "platforms": item["work"]["platforms"],
                         "layer": item["layer"], "label": item["layer_label"],
                         "episodes": item["work"].get("episodes")} for item in results[:5]],
            }
            self.end_to_end.append(row)
            print(f"  {query}: 总{len(results)} 聚合{len(agg_hits)} 耗时{latency:.0f}ms "
                  f"stage={row['fallback_stage']} degraded={row['degraded']}")
            time.sleep(0.3)

        total_agg = sum(row.get("agg_hits", 0) for row in self.end_to_end)
        with_agg = sum(1 for row in self.end_to_end if row.get("agg_hits", 0) > 0)
        self.check("端到端结果包含真实聚合源", True, total_agg > 0)
        self.check("≥3 个查询命中聚合源", True, with_agg >= 3)
        episodes = [top.get("episodes") for row in self.end_to_end for top in row.get("top", [])]
        self.check("聚合结果含集数元数据", True, any(episodes))
        self.check("所有请求均在预算内返回", True,
                   all(row.get("elapsed_ms", 0) < 6000 for row in self.end_to_end if row.get("ok")))

        # 句子/台词查询：L1 元数据源不应返回无关结果
        status, payload, latency = self.client.search("她说她从来没有恨过我，只是想再见到我", "drama")
        agg_hits = [item for item in payload["groups"].get("short_drama", {}).get("results", [])
                    if "agg_drama" in item["work"]["platforms"]]
        self.check("句子查询不污染聚合结果", 0, len(agg_hits))
        print(f"  句子查询：总{payload['groups'].get('short_drama', {}).get('total', 0)} 条，"
              f"聚合 {len(agg_hits)} 条，{latency:.0f}ms")

    # ------------------------------------------------------- failure handling
    def probe_failure(self):
        print("\n[3/5] 聚合源故障注入（本地假服务返回 500）")
        from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

        class _Broken(BaseHTTPRequestHandler):
            def log_message(self, *args):
                return

            def do_GET(self):  # noqa: N802
                self.send_response(500)
                self.send_header("Content-Length", "0")
                self.end_headers()

        broken = ThreadingHTTPServer(("127.0.0.1", 0), _Broken)
        threading.Thread(target=broken.serve_forever, kwargs={"poll_interval": 0.05},
                         daemon=True).start()
        bad = f"http://127.0.0.1:{broken.server_address[1]}"
        self.runtime.defn.base_url = bad
        self.runtime.breaker.reset()
        if self.app.cache is not None:
            self.app.cache.clear()
        try:
            for index in range(3):
                status, payload, latency = self.client.search("闪婚", "drama")
                self.failure.append({
                    "round": index + 1,
                    "elapsed_ms": round(latency, 1),
                    "degraded": payload["degraded"],
                    "agg_used": any(p["platform"] == "agg_drama" and p["used"] for p in payload["platforms"]),
                    "results": payload["groups"].get("short_drama", {}).get("total", 0),
                })
                print(f"  第{index + 1}次：{latency:.0f}ms degraded={payload['degraded']} "
                      f"结果={self.failure[-1]['results']}")

            # 第 4 次：熔断已打开，应直接跳过聚合源
            _, payload, latency = self.client.search("闪婚", "drama")
            fourth = {
                "round": 4,
                "elapsed_ms": round(latency, 1),
                "degraded": payload["degraded"],
                "agg_used": any(p["platform"] == "agg_drama" and p["used"] for p in payload["platforms"]),
                "results": payload["groups"].get("short_drama", {}).get("total", 0),
            }
            self.failure.append(fourth)
            print(f"  第4次（熔断后）：{latency:.0f}ms agg_used={fourth['agg_used']} "
                  f"结果={fourth['results']}")

            _, platforms, _ = self.client.get("/api/platforms")
            agg = next(p for p in platforms["platforms"] if p["id"] == "agg_drama")
            self.check("连续失败后聚合源熔断", "circuit_open", agg["state"])
            self.check("熔断后跳过聚合源", False, fourth["agg_used"])
            self.check("熔断后仍返回本地结果", True, fourth["results"] > 0)
            self.check("熔断期间请求标记降级", True, fourth["degraded"])
        finally:
            broken.shutdown()
            broken.server_close()
            self.runtime.defn.base_url = self.original_base
            self.runtime.breaker.reset()

        # 恢复配置后，确认聚合源重新可用
        status, payload, latency = self.client.search("闪婚", "drama")
        recovered = any("agg_drama" in item["work"]["platforms"]
                        for item in payload["groups"].get("short_drama", {}).get("results", []))
        self.check("恢复配置后聚合源重新命中", True, recovered)
        print(f"  恢复后：命中聚合 {recovered}，{latency:.0f}ms")

    # ---------------------------------------------------------------- cache
    def probe_cache(self):
        print("\n[4/5] L1 缓存：冷启动 vs 命中")
        if self.app.cache is None:
            self.check("缓存已启用", True, False)
            return
        self.app.cache.clear()
        _, cold_payload, cold_ms = self.client.search("闪婚", "drama")
        _, warm_payload, warm_ms = self.client.search("闪婚", "drama")
        stats = self.client.get("/api/stats")[1]["cache"]
        cold_titles = [item["work"]["title"] for item in
                       cold_payload["groups"].get("short_drama", {}).get("results", [])]
        warm_titles = [item["work"]["title"] for item in
                       warm_payload["groups"].get("short_drama", {}).get("results", [])]
        self.check("冷查询走真实网络（>500ms）", True, cold_ms > 500)
        self.check("二次查询命中缓存（<300ms）", True, warm_ms < 300)
        self.check("缓存命中计数 ≥1", True, stats["hits"] >= 1)
        self.check("冷/热结果一致", cold_titles, warm_titles)
        self.cache_stat = {
            "cold_ms": round(cold_ms, 1),
            "warm_ms": round(warm_ms, 1),
            "speedup": round(cold_ms / max(1.0, warm_ms), 1),
            "stats": stats,
            "titles": cold_titles,
        }
        print(f"  冷查询 {cold_ms:.0f}ms → 命中 {warm_ms:.0f}ms（{self.cache_stat['speedup']}x），"
              f"hits={stats['hits']} entries={stats['entries']}")

    # ------------------------------------------------------------------ report
    def run(self):
        self.cache_stat = {}
        self.setup()
        started = time.time()
        try:
            self.probe_direct()
            self.probe_http()
            self.probe_failure()
            self.probe_cache()
        finally:
            self.teardown()
        duration = time.time() - started

        passed = sum(1 for c in self.checks if c["pass"])
        summary = {
            "started_at": now_iso(),
            "duration_s": round(duration, 2),
            "checks": len(self.checks),
            "passed": passed,
            "failed": len(self.checks) - passed,
            "endpoint": "https://api.icofun.cn/API/djpxb.php",
            "config": str(LIVE_CONFIG.relative_to(ROOT)).replace("\\", "/"),
        }
        payload = {"summary": summary, "checks": self.checks,
                   "direct": self.direct, "end_to_end": self.end_to_end,
                   "failure": self.failure, "cache": self.cache_stat}
        OUT_DIR.mkdir(parents=True, exist_ok=True)
        (OUT_DIR / "results.json").write_text(json.dumps(payload, ensure_ascii=False, indent=2),
                                              encoding="utf-8")
        REPORT.write_text(render(payload), encoding="utf-8")

        print(f"\n[5/5] {passed}/{len(self.checks)} 项检查通过，耗时 {duration:.1f}s")
        print(f"报告：{REPORT}")
        return 0 if summary["failed"] == 0 else 1


def _top_titles(row: dict) -> str:
    if not row.get("top"):
        return "—"
    return "；".join(f"{item['title']}（{'/'.join(item['platforms'])}）" for item in row["top"][:3])


def render(payload: dict) -> str:
    s = payload["summary"]
    lines = [
        "# 真实聚合 API 联调报告",
        "",
        f"- 时间：{s['started_at']}（Asia/Shanghai）",
        f"- 接口：`{s['endpoint']}`（小齐 API「短剧搜索与排名榜」，公开访问 / 无需密钥）",
        f"- 配置：`{s['config']}`（agg_drama → icofun_shortdrama）",
        f"- 结果：**{s['passed']}/{s['checks']} 项检查通过**，总耗时 {s['duration_s']}s",
        f"- 原始数据：[results.json](live/results.json)",
        "",
        "> 该接口只提供 L1 元数据（剧名 / ID / 封面 / 集数）。本联调不请求 `n` 参数（视频链接），",
        "> 不存储、不分发任何内容，仅用于验证真实网络下的召回与降级链路。",
        "",
        "## 1. 直连聚合接口",
        "",
        "| 查询 | 结果数 | 延迟 | 返回剧名 |",
        "| --- | --- | --- | --- |",
    ]
    for row in payload["direct"]:
        if row.get("ok"):
            lines.append(f"| {row['query']} | {row['count']} | {row['latency_ms']}ms | "
                         f"{'、'.join(row.get('titles') or []) or '—'} |")
        else:
            lines.append(f"| {row['query']} | 失败 | {row['latency_ms']}ms | {row.get('error', '')} |")

    lines += ["", "## 2. 端到端 HTTP 搜索", "",
              "| 查询 | 结果数 | 其中聚合源 | 耗时 | 降级层级 | degraded | 首条结果 |",
              "| --- | --- | --- | --- | --- | --- | --- |"]
    for row in payload["end_to_end"]:
        if row.get("ok"):
            lines.append(f"| {row['query']} | {row['total']} | {row['agg_hits']} | "
                         f"{row['elapsed_ms']}ms | {row['fallback_stage']} | "
                         f"{'是' if row['degraded'] else '否'} | {_top_titles(row)} |")
        else:
            lines.append(f"| {row['query']} | 请求失败 | — | — | — | — | — |")

    lines += ["", "## 3. 聚合源故障注入", "",
              "| 轮次 | 耗时 | 结果数 | 聚合源参与 | degraded |",
              "| --- | --- | --- | --- | --- |"]
    for row in payload["failure"]:
        lines.append(f"| 第 {row['round']} 次 | {row['elapsed_ms']}ms | {row['results']} | "
                     f"{'是' if row['agg_used'] else '否'} | {'是' if row['degraded'] else '否'} |")

    lines += ["", "## 4. L1 缓存（冷/热对比）", ""]
    cache = payload.get("cache") or {}
    if cache:
        lines += [
            "| 指标 | 值 |",
            "| --- | --- |",
            f"| 冷查询（真实网络） | {cache.get('cold_ms')}ms |",
            f"| 二次查询（缓存命中） | {cache.get('warm_ms')}ms |",
            f"| 加速比 | {cache.get('speedup')}x |",
            f"| 命中 / 未命中 | {cache.get('stats', {}).get('hits')} / {cache.get('stats', {}).get('misses')} |",
            f"| TTL / 条目上限 | {cache.get('stats', {}).get('ttl_s')}s / {cache.get('stats', {}).get('max_entries')} |",
        ]
    else:
        lines.append("（本次未采集缓存数据）")

    lines += ["", "## 5. 检查项", "", "| 检查 | 期望 | 实际 | 结果 |", "| --- | --- | --- | --- |"]
    for check in payload["checks"]:
        lines.append(f"| {check['name']} | `{check['expected']}` | `{check['actual']}` | "
                     f"{'✅' if check['pass'] else '❌'} |")

    lines += [
        "",
        "## 复现",
        "",
        "```bash",
        "cd novel-drama-agent",
        "python -X utf8 scripts/make_live_config.py   # 由默认配置生成联调配置",
        "python -X utf8 scripts/live_check.py         # 真实网络联调",
        "```",
        "",
        "也可手动起服务联调：",
        "",
        "```bash",
        "# Windows PowerShell",
        "$env:NDS_PLATFORMS='config/platforms.live.json'",
        "$env:NDS_BUDGET_MS='6000'",
        "python run.py serve",
        "```",
        "",
    ]
    return "\n".join(lines)


if __name__ == "__main__":
    sys.exit(LiveCheck().run())
