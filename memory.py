"""长记忆（§9）：用户画像 / 会话记忆 / 实体记忆 / 程序记忆，落地 SQLite。

写入是 fire-and-forget（编排层放线程池），失败只记日志，绝不影响搜索主链路。
"""
from __future__ import annotations

import json
import sqlite3
import threading
import time
from pathlib import Path
from typing import Any

_SCHEMA = """
CREATE TABLE IF NOT EXISTS profiles (
    user_id     TEXT PRIMARY KEY,
    data        TEXT NOT NULL,
    updated_at  REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS sessions (
    session_id  TEXT PRIMARY KEY,
    user_id     TEXT NOT NULL,
    recent_queries TEXT NOT NULL,
    clicked_works  TEXT NOT NULL,
    summary     TEXT NOT NULL DEFAULT '',
    updated_at  REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS entities (
    user_id     TEXT NOT NULL,
    entity_type TEXT NOT NULL,
    entity_id   TEXT NOT NULL,
    weight      REAL NOT NULL DEFAULT 0,
    last_at     REAL NOT NULL,
    PRIMARY KEY (user_id, entity_type, entity_id)
);
CREATE TABLE IF NOT EXISTS procedures (
    pattern     TEXT PRIMARY KEY,
    route_stage INTEGER NOT NULL DEFAULT 1,
    rewrite     TEXT NOT NULL DEFAULT '',
    hits        INTEGER NOT NULL DEFAULT 0,
    updated_at  REAL NOT NULL
);
"""

_DEFAULT_PROFILE = {
    "preferred_types": [],
    "preferred_themes": [],
    "preferred_authors": [],
    "preferred_characters": [],
    "blocked_topics": [],
    "clicked_works": {},
}


class MemoryStore:
    def __init__(self, path: str, session_ttl_days: int = 7):
        self.path = path
        self.ttl_days = session_ttl_days
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._conn = sqlite3.connect(path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        with self._lock:
            self._conn.executescript(_SCHEMA)
            self._conn.commit()

    # ------------------------------------------------------------- profiles
    def get_profile(self, user_id: str) -> dict[str, Any]:
        with self._lock:
            row = self._conn.execute("SELECT data FROM profiles WHERE user_id=?", (user_id,)).fetchone()
        data = dict(_DEFAULT_PROFILE)
        if row:
            try:
                data.update(json.loads(row["data"]))
            except json.JSONDecodeError:
                pass
        data["user_id"] = user_id
        return data

    def _save_profile(self, user_id: str, data: dict[str, Any]) -> None:
        with self._lock:
            self._conn.execute(
                "INSERT INTO profiles(user_id,data,updated_at) VALUES(?,?,?) "
                "ON CONFLICT(user_id) DO UPDATE SET data=excluded.data, updated_at=excluded.updated_at",
                (user_id, json.dumps(data, ensure_ascii=False), time.time()),
            )
            self._conn.commit()

    def set_blocked_topics(self, user_id: str, topics: list[str]) -> dict[str, Any]:
        data = self.get_profile(user_id)
        data["blocked_topics"] = [t.strip() for t in topics if t and t.strip()][:50]
        self._save_profile(user_id, data)
        return data

    def _bump(self, user_id: str, entity_type: str, entity_id: str, weight: float) -> None:
        if not entity_id:
            return
        now = time.time()
        with self._lock:
            self._conn.execute(
                "INSERT INTO entities(user_id,entity_type,entity_id,weight,last_at) VALUES(?,?,?,?,?) "
                "ON CONFLICT(user_id,entity_type,entity_id) DO UPDATE SET "
                "weight = MIN(10.0, entities.weight + excluded.weight), last_at = excluded.last_at",
                (user_id, entity_type, entity_id, weight, now),
            )
            self._conn.commit()

    def _refresh_preferences(self, user_id: str) -> None:
        data = self.get_profile(user_id)
        with self._lock:
            rows = self._conn.execute(
                "SELECT entity_type, entity_id, weight FROM entities WHERE user_id=? "
                "ORDER BY weight DESC LIMIT 120",
                (user_id,),
            ).fetchall()
        by_type: dict[str, list[str]] = {}
        thresholds = {"theme": 0.5, "author": 0.3, "character": 0.2, "work": 0.5, "type": 0.05}
        for row in rows:
            if row["weight"] >= thresholds.get(row["entity_type"], 0.5):
                by_type.setdefault(row["entity_type"], []).append(row["entity_id"])
        data["preferred_themes"] = by_type.get("theme", [])[:6]
        data["preferred_authors"] = by_type.get("author", [])[:5]
        data["preferred_characters"] = by_type.get("character", [])[:5]
        data["clicked_works"] = {k: int(v) for k, v in
                                 ((r["entity_id"], r["weight"]) for r in rows if r["entity_type"] == "work")}
        types = by_type.get("type", [])
        data["preferred_types"] = types[:2]
        self._save_profile(user_id, data)

    # ------------------------------------------------------------- sessions
    def record_search(self, user_id: str, session_id: str, query: str,
                      themes: list[str], kinds: list[str]) -> None:
        now = time.time()
        with self._lock:
            row = self._conn.execute("SELECT recent_queries, clicked_works FROM sessions WHERE session_id=?",
                                     (session_id,)).fetchone()
            recents = json.loads(row["recent_queries"]) if row else []
            recents = ([query] + [q for q in recents if q != query])[:10]
            clicked = json.loads(row["clicked_works"]) if row else []
            summary = f"最近检索：{'；'.join(recents[:5])}"
            self._conn.execute(
                "INSERT INTO sessions(session_id,user_id,recent_queries,clicked_works,summary,updated_at) "
                "VALUES(?,?,?,?,?,?) ON CONFLICT(session_id) DO UPDATE SET "
                "recent_queries=excluded.recent_queries, clicked_works=excluded.clicked_works, "
                "summary=excluded.summary, updated_at=excluded.updated_at",
                (session_id, user_id, json.dumps(recents, ensure_ascii=False),
                 json.dumps(clicked, ensure_ascii=False), summary, now),
            )
            self._conn.commit()
        for theme in themes[:3]:
            self._bump(user_id, "theme", theme, 0.1)
        for kind in kinds[:1]:
            self._bump(user_id, "type", kind, 0.05)
        self._refresh_preferences(user_id)

    def get_session(self, session_id: str) -> dict[str, Any]:
        with self._lock:
            row = self._conn.execute("SELECT * FROM sessions WHERE session_id=?", (session_id,)).fetchone()
        if not row:
            return {"session_id": session_id, "recent_queries": [], "clicked_works": [], "summary": ""}
        return {
            "session_id": session_id,
            "recent_queries": json.loads(row["recent_queries"] or "[]"),
            "clicked_works": json.loads(row["clicked_works"] or "[]"),
            "summary": row["summary"],
            "updated_at": row["updated_at"],
        }

    # ---------------------------------------------------------------- clicks
    def record_click(self, user_id: str, session_id: str, work_id: str, title: str,
                     themes: list[str], author: str, characters: list[str]) -> None:
        self._bump(user_id, "work", work_id, 1.0)
        for theme in (themes or [])[:3]:
            self._bump(user_id, "theme", theme, 0.4)
        if author:
            self._bump(user_id, "author", author, 0.3)
        for name in (characters or [])[:2]:
            self._bump(user_id, "character", name, 0.2)
        with self._lock:
            row = self._conn.execute("SELECT clicked_works FROM sessions WHERE session_id=?", (session_id,)).fetchone()
            clicked = json.loads(row["clicked_works"]) if row else []
            if work_id not in clicked:
                clicked = [work_id] + clicked[:19]
                self._conn.execute(
                    "INSERT INTO sessions(session_id,user_id,recent_queries,clicked_works,summary,updated_at) "
                    "VALUES(?,?,?,?,?,?) ON CONFLICT(session_id) DO UPDATE SET clicked_works=excluded.clicked_works",
                    (session_id, user_id, "[]", json.dumps(clicked, ensure_ascii=False),
                     f"点击了《{title}》", time.time()),
                )
                self._conn.commit()
        self._refresh_preferences(user_id)

    # ------------------------------------------------------------ procedures
    def procedure_stage(self, pattern: str) -> int | None:
        with self._lock:
            row = self._conn.execute("SELECT route_stage FROM procedures WHERE pattern=?", (pattern,)).fetchone()
        return int(row["route_stage"]) if row else None

    def record_procedure(self, pattern: str, route_stage: int, rewrite: str = "") -> None:
        with self._lock:
            self._conn.execute(
                "INSERT INTO procedures(pattern,route_stage,rewrite,hits,updated_at) VALUES(?,?,?,1,?) "
                "ON CONFLICT(pattern) DO UPDATE SET route_stage=excluded.route_stage, "
                "rewrite=excluded.rewrite, hits=procedures.hits+1, updated_at=excluded.updated_at",
                (pattern, route_stage, rewrite, time.time()),
            )
            self._conn.commit()

    def procedures(self, limit: int = 20) -> list[dict]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT pattern, route_stage, rewrite, hits FROM procedures ORDER BY hits DESC LIMIT ?",
                (limit,),
            ).fetchall()
        return [dict(r) for r in rows]

    # ----------------------------------------------------------------- misc
    def export(self, user_id: str) -> dict[str, Any]:
        profile = self.get_profile(user_id)
        with self._lock:
            entities = [dict(r) for r in self._conn.execute(
                "SELECT entity_type, entity_id, weight, last_at FROM entities WHERE user_id=? "
                "ORDER BY weight DESC LIMIT 50", (user_id,)).fetchall()]
            sessions = [dict(r) for r in self._conn.execute(
                "SELECT session_id, summary, updated_at FROM sessions WHERE user_id=? "
                "ORDER BY updated_at DESC LIMIT 10", (user_id,)).fetchall()]
        return {"profile": profile, "entities": entities, "sessions": sessions}

    def clear(self, user_id: str) -> None:
        with self._lock:
            self._conn.execute("DELETE FROM entities WHERE user_id=?", (user_id,))
            self._conn.execute("DELETE FROM sessions WHERE user_id=?", (user_id,))
            self._conn.execute("DELETE FROM profiles WHERE user_id=?", (user_id,))
            self._conn.commit()

    def purge_expired_sessions(self, now: float | None = None) -> int:
        cutoff = (now or time.time()) - self.ttl_days * 86400
        with self._lock:
            cur = self._conn.execute("DELETE FROM sessions WHERE updated_at < ?", (cutoff,))
            self._conn.commit()
        return cur.rowcount

    def close(self) -> None:
        with self._lock:
            self._conn.close()
