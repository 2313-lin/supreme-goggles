"""HTTP API + 静态 UI（标准库 http.server，零依赖）。

路由：
  GET  /                                  单页 UI
  GET  /api/health                        健康检查
  GET  /api/platforms                     平台注册表 / 熔断 / 健康分
  GET  /api/stats                         语料与运行统计
  GET  /api/examples                      示例查询
  GET  /api/works/{id}                    作品详情（角色 / 原文句子 / 台词）
  GET  /api/memory?user_id=&session_id=   长记忆导出
  GET  /api/audit?limit=                  审计日志尾部
  POST /api/search                        搜索（主接口）
  POST /api/feedback/click                点击反馈（写入实体记忆）
  POST /api/memory/blocked                设置屏蔽主题
  DELETE /api/memory?user_id=             清除记忆（数据可删除）
  POST /api/admin/platforms/{id}/simulate 故障注入（需 token + confirm）
  POST /api/admin/platforms/reload        热加载平台注册表（需 token + confirm）
"""
from __future__ import annotations

import json
import mimetypes
import time
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any
from urllib.parse import parse_qs, unquote, urlsplit

from . import __version__
from .adapters import AdapterHub
from .audit import AuditLog
from .cache import build_cache
from .config import WEB_DIR, Settings
from .corpus import Corpus
from .guardrails import Guardrails
from .memory import MemoryStore
from .orchestrator import SearchAgent
from .registry import PlatformRegistry

_MAX_BODY = 64 * 1024

EXAMPLES = [
    {"tab": "all", "query": "她说她从来没有恨过我，只是不想再见到我", "hint": "原文句子（最高优先级）"},
    {"tab": "novel", "query": "林昭", "hint": "主角名称"},
    {"tab": "novel", "query": "重生 复仇", "hint": "主题"},
    {"tab": "drama", "query": "闪婚", "hint": "短剧名称/主题"},
    {"tab": "drama", "query": "念念，你跑什么？我又不会吃了你", "hint": "台词精确命中"},
    {"tab": "all", "query": "侯府嫡女", "hint": "书名/别名"},
]


class NdsApp:
    def __init__(self, settings: Settings | None = None):
        self.settings = settings or Settings.load()
        self.started_at = time.time()
        self.corpus = Corpus.load()
        self.registry = PlatformRegistry.load(self.settings.platforms_config)
        if self.settings.cache_ttl_s > 0:
            self.cache, self.cache_fallback = build_cache(self.settings)
        else:
            self.cache, self.cache_fallback = None, ""
        self.hub = AdapterHub(self.corpus, self.cache)
        self.memory = MemoryStore(self.settings.memory_db, self.settings.session_ttl_days)
        self.guardrails = Guardrails(self.settings.max_query_chars, self.settings.rate_limit_per_min)
        self.audit = AuditLog(self.settings.audit_log)
        self.agent = SearchAgent(self.settings, self.corpus, self.registry, self.hub,
                                 self.memory, self.guardrails, self.audit)

    # ------------------------------------------------------------------ routes
    def handle(self, method: str, path: str, query: dict[str, list[str]],
               body: bytes, headers: dict[str, str]) -> tuple[int, bytes, dict[str, str]]:
        try:
            if path.startswith("/api/"):
                return self._api(method, path, query, body, headers)
            return self._static(path)
        except KeyError as exc:
            return self._json(404, {"error": {"code": "not_found", "message": f"缺少资源：{exc}"}})
        except json.JSONDecodeError:
            return self._json(400, {"error": {"code": "bad_json", "message": "请求体不是合法 JSON"}})
        except Exception as exc:  # noqa: BLE001 兜底，避免线程崩溃
            return self._json(500, {"error": {"code": "internal_error",
                                              "message": f"{type(exc).__name__}: {exc}"}})

    # --------------------------------------------------------------------- API
    def _api(self, method: str, path: str, query: dict[str, list[str]],
             body: bytes, headers: dict[str, str]) -> tuple[int, bytes, dict[str, str]]:
        if path == "/api/health" and method == "GET":
            platforms = self.registry.snapshot()
            return self._json(200, {
                "status": "ok",
                "version": __version__,
                "uptime_s": int(time.time() - self.started_at),
                "corpus": self.corpus.stats(),
                "platforms_healthy": sum(1 for p in platforms if p["state"] == "healthy"),
                "platforms_total": len(platforms),
            })

        if path == "/api/platforms" and method == "GET":
            return self._json(200, {"platforms": self.registry.snapshot()})

        if path == "/api/stats" and method == "GET":
            payload = self.agent.stats()
            payload["cache"] = self._cache_stats()
            return self._json(200, payload)

        if path == "/api/examples" and method == "GET":
            return self._json(200, {"examples": EXAMPLES})

        if path.startswith("/api/works/") and method == "GET":
            work_id = unquote(path[len("/api/works/"):])
            detail = self.agent.detail(work_id)
            if not detail:
                return self._json(404, {"error": {"code": "work_not_found", "message": "作品不存在"}})
            return self._json(200, {"work": detail})

        if path == "/api/memory" and method == "GET":
            user_id = self._qs(query, "user_id", "u_demo")
            session_id = self._qs(query, "session_id", "")
            payload = self.memory.export(user_id)
            if session_id:
                payload["session"] = self.memory.get_session(session_id)
            return self._json(200, payload)

        if path == "/api/memory" and method == "DELETE":
            user_id = self._qs(query, "user_id", "u_demo")
            self.memory.clear(user_id)
            self.audit.write({"event": "memory_deleted", "user_id": user_id})
            return self._json(200, {"ok": True, "user_id": user_id})

        if path == "/api/memory/blocked" and method == "POST":
            data = self._body(body)
            user_id = data.get("user_id") or "u_demo"
            topics = data.get("topics") or []
            profile = self.memory.set_blocked_topics(user_id, topics)
            return self._json(200, {"ok": True, "blocked_topics": profile.get("blocked_topics", [])})

        if path == "/api/audit" and method == "GET":
            limit = int(self._qs(query, "limit", "20"))
            return self._json(200, {"events": self.audit.tail(max(1, min(200, limit)))})

        if path == "/api/search" and method == "POST":
            data = self._body(body)
            result = self.agent.search(
                query=str(data.get("query", "")),
                tab=str(data.get("tab", "all")),
                user_id=str(data.get("user_id") or "u_demo"),
                session_id=data.get("session_id"),
                limit=int(data["limit"]) if data.get("limit") else None,
                budget_ms=int(data["budget_ms"]) if data.get("budget_ms") else None,
            )
            status = 200 if result.get("allowed", True) else 200
            return self._json(status, result)

        if path == "/api/feedback/click" and method == "POST":
            data = self._body(body)
            user_id = str(data.get("user_id") or "u_demo")
            session_id = str(data.get("session_id") or "s_anon")
            work_id = str(data.get("work_id") or "")
            work = self.corpus.get(work_id)
            if not work:
                return self._json(404, {"error": {"code": "work_not_found", "message": "作品不存在"}})
            self.memory.record_click(user_id, session_id, work.id, work.title,
                                     work.themes, work.author, [c.name for c in work.characters[:2]])
            return self._json(200, {"ok": True})

        if path == "/api/cache/clear" and method == "POST":
            data = self._body(body)
            guard = self.guardrails.check_admin(headers.get("x-admin-token", ""),
                                                self.settings.admin_token, bool(data.get("confirm")))
            if not guard.allowed:
                code = 401 if guard.code == "unauthorized" else 428
                return self._json(code, {"error": {"code": guard.code, "message": guard.message}})
            if self.cache is not None:
                self.cache.clear()
            self.audit.write({"event": "cache_cleared"})
            return self._json(200, {"ok": True, "cache": self._cache_stats()})

        if path.startswith("/api/admin/platforms/") and method == "POST":
            token = headers.get("x-admin-token", "")
            data = self._body(body)
            confirm = bool(data.get("confirm"))
            guard = self.guardrails.check_admin(token, self.settings.admin_token, confirm)
            if not guard.allowed:
                code = 401 if guard.code == "unauthorized" else 428
                return self._json(code, {"error": {"code": guard.code, "message": guard.message}})

            rest = path[len("/api/admin/platforms/"):]
            if rest == "reload":
                fresh = PlatformRegistry.load(self.settings.platforms_config)
                self.registry.reload(fresh)
                self.agent._priority = {pid: p.defn.priority for pid, p in self.registry.platforms.items()}
                if self.cache is not None:
                    self.cache.clear()          # 配置变了，缓存可能已失效
                self.audit.write({"event": "registry_reloaded", "trace_id": uuid.uuid4().hex[:8]})
                return self._json(200, {"ok": True, "platforms": self.registry.snapshot()})

            platform_id = unquote(rest.split("/")[0])
            rt = self.registry.get(platform_id)
            if not rt:
                return self._json(404, {"error": {"code": "platform_not_found", "message": "平台不存在"}})
            self.registry.simulate(
                platform_id,
                down=data.get("down") if isinstance(data.get("down"), bool) else None,
                latency_ms=int(data["latency_ms"]) if data.get("latency_ms") is not None else None,
                reset_breaker=bool(data.get("reset_breaker")),
            )
            self.audit.write({"event": "platform_simulated", "platform": platform_id,
                              "down": data.get("down"), "latency_ms": data.get("latency_ms")})
            return self._json(200, {"ok": True, "platform": rt.to_dict()})

        return self._json(404, {"error": {"code": "not_found", "message": f"未知接口 {method} {path}"}})

    # ------------------------------------------------------------------ static
    def _static(self, path: str) -> tuple[int, bytes, dict[str, str]]:
        rel = "index.html" if path in ("/", "") else path.lstrip("/")
        target = (WEB_DIR / rel).resolve()
        try:
            target.relative_to(WEB_DIR.resolve())
        except ValueError:
            return self._json(403, {"error": {"code": "forbidden", "message": "非法路径"}})
        if not target.is_file():
            return self._html(404, "<h1>404</h1><p>页面不存在</p>")
        content_type = mimetypes.guess_type(str(target))[0] or "application/octet-stream"
        if content_type.startswith("text/") or content_type in ("application/javascript", "application/json"):
            content_type += "; charset=utf-8"
        return 200, target.read_bytes(), {"Content-Type": content_type}

    # ----------------------------------------------------------------- helpers
    def _cache_stats(self) -> dict[str, Any]:
        if self.cache is None:
            return {"enabled": False, "backend": "disabled"}
        stats = dict(self.cache.stats())
        stats["fallback_reason"] = getattr(self, "cache_fallback", "") or ""
        return stats

    @staticmethod
    def _qs(query: dict[str, list[str]], key: str, default: str = "") -> str:
        values = query.get(key)
        return values[0] if values else default

    @staticmethod
    def _body(body: bytes) -> dict[str, Any]:
        if not body:
            return {}
        data = json.loads(body.decode("utf-8"))
        if not isinstance(data, dict):
            raise json.JSONDecodeError("body must be object", "", 0)
        return data

    @staticmethod
    def _json(status: int, payload: dict[str, Any]) -> tuple[int, bytes, dict[str, str]]:
        return status, json.dumps(payload, ensure_ascii=False).encode("utf-8"), {
            "Content-Type": "application/json; charset=utf-8",
            "Cache-Control": "no-store",
        }

    @staticmethod
    def _html(status: int, html: str) -> tuple[int, bytes, dict[str, str]]:
        return status, html.encode("utf-8"), {"Content-Type": "text/html; charset=utf-8"}

    def close(self) -> None:
        self.agent.close()
        self.memory.close()
        close = getattr(self.cache, "close", None)
        if callable(close):
            close()


class _Handler(BaseHTTPRequestHandler):
    server_version = "NovelDramaSearch/1.0"
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt: str, *args) -> None:  # 静默访问日志，审计走 audit.jsonl
        return

    def _dispatch(self, method: str) -> None:
        parsed = urlsplit(self.path)
        length = int(self.headers.get("Content-Length") or 0)
        if length > _MAX_BODY:
            self.send_response(413)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.end_headers()
            self.wfile.write(b'{"error":{"code":"payload_too_large"}}')
            return
        body = self.rfile.read(length) if length else b""
        headers = {k.lower(): v for k, v in self.headers.items()}
        status, payload, extra = self.server.app.handle(  # type: ignore[attr-defined]
            method, parsed.path, parse_qs(parsed.query), body, headers)
        self.send_response(status)
        for key, value in extra.items():
            self.send_header(key, value)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Headers", "Content-Type, X-Admin-Token")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, DELETE, OPTIONS")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        if method != "HEAD":
            self.wfile.write(payload)

    def do_GET(self) -> None:      # noqa: N802
        self._dispatch("GET")

    def do_HEAD(self) -> None:     # noqa: N802
        self._dispatch("HEAD")

    def do_POST(self) -> None:     # noqa: N802
        self._dispatch("POST")

    def do_DELETE(self) -> None:   # noqa: N802
        self._dispatch("DELETE")

    def do_OPTIONS(self) -> None:  # noqa: N802
        self.send_response(204)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Headers", "Content-Type, X-Admin-Token")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, DELETE, OPTIONS")
        self.send_header("Content-Length", "0")
        self.end_headers()


class NdsServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True

    def __init__(self, address: tuple[str, int], app: NdsApp):
        self.app = app
        super().__init__(address, _Handler)


def serve(settings: Settings | None = None) -> None:
    settings = settings or Settings.load()
    app = NdsApp(settings)
    server = NdsServer((settings.host, settings.port), app)
    url = f"http://{settings.host}:{settings.port}"
    print(f"[nds] 语料：{app.corpus.stats()}")
    print(f"[nds] UI: {url}   API: {url}/api/health")
    try:
        server.serve_forever(poll_interval=0.2)
    except KeyboardInterrupt:
        print("\n[nds] 正在关闭 …")
    finally:
        server.shutdown()
        server.server_close()
        app.close()
