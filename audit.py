"""审计日志：JSONL 追加写，自动轮转；只记录脱敏后的字段。"""
from __future__ import annotations

import json
import os
import threading
import time
from pathlib import Path


class AuditLog:
    def __init__(self, path: str, max_bytes: int = 5 * 1024 * 1024):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.max_bytes = max_bytes
        self._lock = threading.Lock()

    def _rotate(self) -> None:
        try:
            if self.path.exists() and self.path.stat().st_size >= self.max_bytes:
                backup = self.path.with_suffix(self.path.suffix + ".1")
                if backup.exists():
                    backup.unlink()
                os.replace(self.path, backup)
        except OSError:
            pass

    def write(self, event: dict) -> None:
        record = {"ts": round(time.time(), 3), **event}
        line = json.dumps(record, ensure_ascii=False)
        with self._lock:
            self._rotate()
            try:
                with self.path.open("a", encoding="utf-8") as fh:
                    fh.write(line + "\n")
            except OSError:
                pass

    def tail(self, limit: int = 20) -> list[dict]:
        if not self.path.exists():
            return []
        try:
            lines = self.path.read_text(encoding="utf-8").splitlines()[-limit:]
        except OSError:
            return []
        out = []
        for line in lines:
            try:
                out.append(json.loads(line))
            except json.JSONDecodeError:
                continue
        return out
