"""命令行入口：serve / search / stats / platforms / simulate / demo。"""
from __future__ import annotations

import argparse
import json
import sys

from .config import Settings


def _build_app(settings: Settings):
    from .api import NdsApp
    return NdsApp(settings)


def _print_results(payload: dict, as_json: bool) -> None:
    if as_json:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return
    if not payload.get("allowed", True):
        print(f"[拒绝] {payload.get('code')}: {payload.get('message')}")
        return
    print(f"查询：{payload['query']}   tab={payload['tab']}   耗时 {payload['elapsed_ms']}ms   "
          f"{'⚠ 已降级' if payload['degraded'] else '✓ 正常'}")
    for reason in payload.get("degraded_reasons", []):
        print(f"  · {reason}")
    for kind, group in payload.get("groups", {}).items():
        print(f"\n== {group['label']}（{group['total']}）==")
        if not group["results"]:
            print("  （无结果）")
        for i, item in enumerate(group["results"], 1):
            work = item["work"]
            print(f"  {i}. 《{work['title']}》 [{item['layer_label']}] "
                  f"置信 {item['confidence_label']}({item['confidence']}) "
                  f"平台 {', '.join(work['platforms'])}")
            for ev in item["evidence"][:2]:
                print(f"       ↳ {ev['kind']}: {ev['text'][:60]}  （{ev['position']}）")
    if payload.get("platforms"):
        used = [p for p in payload["platforms"] if p["used"]]
        print(f"\n平台调用：{len(used)} 个参与，降级层级 {payload['fallback_stage']}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="nds", description="短篇小说 / 短剧搜索 AI Agent")
    sub = parser.add_subparsers(dest="cmd")

    p_serve = sub.add_parser("serve", help="启动 HTTP API + UI")
    p_serve.add_argument("--port", type=int, default=None)
    p_serve.add_argument("--host", default=None)
    p_serve.add_argument("--live", action="store_true",
                         help="接入真实短剧聚合源（小齐 API，公开无需密钥）并放宽预算")

    p_search = sub.add_parser("search", help="命令行搜索")
    p_search.add_argument("query")
    p_search.add_argument("--tab", default="all", choices=["all", "novel", "drama"])
    p_search.add_argument("--user", default="u_demo")
    p_search.add_argument("--json", action="store_true")

    sub.add_parser("stats", help="语料 / 平台统计")
    sub.add_parser("platforms", help="平台健康与熔断状态")
    sub.add_parser("demo", help="跑一组示例查询")

    p_sim = sub.add_parser("simulate", help="故障注入（演示熔断 / 降级）")
    p_sim.add_argument("platform")
    p_sim.add_argument("--down", action="store_true")
    p_sim.add_argument("--up", action="store_true")
    p_sim.add_argument("--latency", type=int, default=None)
    p_sim.add_argument("--reset", action="store_true")

    args = parser.parse_args(argv)
    settings = Settings.load()

    if args.cmd == "search":
        app = _build_app(settings)
        payload = app.agent.search(args.query, args.tab, args.user)
        _print_results(payload, args.json)
        app.close()
        return 0

    if args.cmd == "demo":
        app = _build_app(settings)
        cases = [
            ("她说她从来没有恨过我，只是不想再见到我", "all"),
            ("林昭", "novel"),
            ("重生 复仇", "novel"),
            ("闪婚", "drama"),
            ("念念，你跑什么？我又不会吃了你", "drama"),
            ("侯府嫡女", "all"),
        ]
        for query, tab in cases:
            payload = app.agent.search(query, tab, "u_demo")
            _print_results(payload, False)
            print("-" * 72)
        app.close()
        return 0

    if args.cmd == "stats":
        app = _build_app(settings)
        print(json.dumps(app.agent.stats(), ensure_ascii=False, indent=2))
        app.close()
        return 0

    if args.cmd == "platforms":
        app = _build_app(settings)
        for p in app.registry.snapshot():
            print(f"{p['id']:<12} {p['type']:<6} {p['role']:<10} stage={p['stage']} "
                  f"state={p['state']:<12} health={p['health_score']} latency={p['latency_ms']}ms")
        app.close()
        return 0

    if args.cmd == "simulate":
        app = _build_app(settings)
        rt = app.registry.simulate(
            args.platform,
            down=True if args.down else (False if args.up else None),
            latency_ms=args.latency,
            reset_breaker=args.reset,
        )
        print(json.dumps(rt.to_dict(), ensure_ascii=False, indent=2))
        app.close()
        return 0

    # 默认 serve
    from .api import serve
    if getattr(args, "port", None):
        settings.port = args.port
    if getattr(args, "host", None):
        settings.host = args.host
    if getattr(args, "live", False):
        from .config import ensure_live_config
        settings.platforms_config = str(ensure_live_config())
        settings.budget_ms = max(settings.budget_ms, 6000)
        print("[nds] 已启用真实短剧聚合源（小齐 API，公开无需密钥）")
    serve(settings)
    return 0


if __name__ == "__main__":
    sys.exit(main())
