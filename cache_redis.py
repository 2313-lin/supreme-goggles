"""Redis 缓存后端：极简 RESP 客户端 + 与内存缓存同接口的 RedisCache。

零第三方依赖：直接实现 Redis 序列化协议（RESP2）的常用子集
（PING / AUTH / SELECT / GET / SET / SETEX / DEL / SCAN / DBSIZE）。

用途：多实例共享 L1 缓存。未配置 Redis 时 `build_cache()` 自动回退到内存缓存。

注意：缓存值用 pickle 序列化（仅内部可信数据），按 `prefix` 命名空间隔离；
不同进程用同一 `redis_url` 即共享命中。
"""
from __future__ import annotations

import hashlib
import pickle
import socket
import time
from typing import Any
from urllib.parse import unquote, urlparse


class RedisError(RuntimeError):
    pass


def _encode_command(*parts: bytes) -> bytes:
    out = bytearray(f"*{len(parts)}\r\n".encode())
    for part in parts:
        out += f"${len(part)}\r\n".encode()
        out += part
        out += b"\r\n"
    return bytes(out)


class RedisClient:
    """最小 RESP 客户端，按需重连。"""

    def __init__(self, host: str = "127.0.0.1", port: int = 6379, db: int = 0,
                 password: str | None = None, timeout: float = 3.0):
        self.host = host
        self.port = port
        self.db = db
        self.password = password
        self.timeout = timeout
        self._sock: socket.socket | None = None

    @classmethod
    def from_url(cls, url: str) -> "RedisClient":
        parsed = urlparse(url if "://" in url else f"redis://{url}")
        host = parsed.hostname or "127.0.0.1"
        port = parsed.port or 6379
        db = int(parsed.path.lstrip("/") or 0)
        password = unquote(parsed.password) if parsed.password else None
        return cls(host=host, port=port, db=db, password=password)

    # ------------------------------------------------------------------ io
    def _connect(self) -> socket.socket:
        sock = socket.create_connection((self.host, self.port), timeout=self.timeout)
        sock.settimeout(self.timeout)
        try:
            if self.password:
                self._command_on(sock, "AUTH", self.password.encode())
            if self.db:
                self._command_on(sock, "SELECT", str(self.db).encode())
        except Exception:
            sock.close()
            raise
        return sock

    @staticmethod
    def _read_line(sock: socket.socket) -> bytes:
        line = b""
        while not line.endswith(b"\r\n"):
            chunk = sock.recv(1)
            if not chunk:
                raise RedisError("connection closed by server")
            line += chunk
        return line[:-2]

    @staticmethod
    def _read_reply(sock: socket.socket) -> Any:
        line = RedisClient._read_line(sock)
        if not line:
            raise RedisError("empty reply")
        kind = line[:1]
        payload = line[1:]
        if kind == b"+":
            return payload.decode("utf-8", "replace")
        if kind == b"-":
            raise RedisError(payload.decode("utf-8", "replace"))
        if kind == b":":
            return int(payload)
        if kind == b"$":
            length = int(payload)
            if length == -1:
                return None
            data = sock.recv(length)
            while len(data) < length:
                chunk = sock.recv(length - len(data))
                if not chunk:
                    raise RedisError("connection closed mid-bulk")
                data += chunk
            sock.recv(2)  # 尾随 \r\n
            return data
        if kind == b"*":
            count = int(payload)
            if count == -1:
                return None
            return [RedisClient._read_reply(sock) for _ in range(count)]
        raise RedisError(f"unknown reply type: {kind!r}")

    def _command_on(self, sock: socket.socket, *parts: bytes) -> Any:
        sock.sendall(_encode_command(*parts))
        return self._read_reply(sock)

    def _command(self, *parts: bytes) -> Any:
        try:
            if self._sock is None:
                self._sock = self._connect()
            return self._command_on(self._sock, *parts)
        except (OSError, RedisError):
            if self._sock is not None:
                try:
                    self._sock.close()
                except OSError:
                    pass
                self._sock = None
            # 单次重连重试
            sock = self._connect()
            self._sock = sock
            return self._command_on(sock, *parts)

    # --------------------------------------------------------------- commands
    def ping(self) -> str:
        return self._command(b"PING")  # type: ignore[return-value]

    def get(self, key: str) -> bytes | None:
        return self._command(b"GET", key.encode())  # type: ignore[return-value]

    def set(self, key: str, value: bytes) -> None:
        self._command(b"SET", key.encode(), value)

    def setex(self, key: str, seconds: int, value: bytes) -> None:
        self._command(b"SETEX", key.encode(), str(seconds).encode(), value)

    def delete(self, *keys: str) -> int:
        if not keys:
            return 0
        return self._command(b"DEL", *[k.encode() for k in keys])  # type: ignore[return-value]

    def scan_keys(self, match: str) -> list[str]:
        """SCAN 遍历，返回所有匹配 `match` 的 key。"""
        found: list[str] = []
        cursor = b"0"
        while True:
            reply = self._command(b"SCAN", cursor, b"MATCH", match.encode(), b"COUNT", b"100")
            raw_cursor = reply[0]
            cursor = raw_cursor if isinstance(raw_cursor, bytes) else str(raw_cursor).encode()
            keys = [k.decode("utf-8", "replace") for k in reply[1]]
            found.extend(keys)
            if cursor == b"0":
                break
        return found

    def dbsize(self) -> int:
        return self._command(b"DBSIZE")  # type: ignore[return-value]

    def close(self) -> None:
        if self._sock is not None:
            try:
                self._sock.close()
            except OSError:
                pass
            self._sock = None


class RedisCache:
    backend = "redis"

    def __init__(self, redis_url: str = "redis://127.0.0.1:6379/0", prefix: str = "nds:",
                 ttl_s: float = 30.0):
        self.client = RedisClient.from_url(redis_url)
        self.redis_url = redis_url
        self.prefix = prefix
        self.ttl_s = max(0.0, float(ttl_s))
        self.hits = 0
        self.misses = 0
        self.errors = 0

    def _key(self, key: Any) -> str:
        digest = hashlib.sha1(repr(key).encode("utf-8")).hexdigest()
        return f"{self.prefix}{digest}"

    def get(self, key: Any) -> Any | None:
        try:
            raw = self.client.get(self._key(key))
        except (RedisError, OSError) as exc:
            self.errors += 1
            self.last_error = str(exc)
            return None
        if raw is None:
            self.misses += 1
            return None
        try:
            value = pickle.loads(raw)
        except Exception as exc:  # noqa: BLE001 损坏数据按未命中处理
            self.errors += 1
            self.last_error = f"unpickle: {exc}"
            return None
        self.hits += 1
        return value

    def set(self, key: Any, value: Any) -> None:
        try:
            payload = pickle.dumps(value, protocol=pickle.HIGHEST_PROTOCOL)
            if self.ttl_s > 0:
                self.client.setex(self._key(key), max(1, int(self.ttl_s)), payload)
            else:
                self.client.set(self._key(key), payload)
        except (RedisError, OSError) as exc:
            self.errors += 1
            self.last_error = str(exc)

    def clear(self) -> None:
        try:
            keys = self.client.scan_keys(f"{self.prefix}*")
            if keys:
                self.client.delete(*keys)
        except (RedisError, OSError) as exc:
            self.errors += 1
            self.last_error = str(exc)

    def stats(self) -> dict[str, Any]:
        total = self.hits + self.misses
        return {
            "backend": "redis",
            "redis_url": _safe_url(self.redis_url),
            "prefix": self.prefix,
            "ttl_s": self.ttl_s,
            "hits": self.hits,
            "misses": self.misses,
            "hit_rate": round(self.hits / total, 3) if total else 0.0,
            "errors": self.errors,
            "last_error": getattr(self, "last_error", ""),
        }

    def close(self) -> None:
        self.client.close()


def _safe_url(url: str) -> str:
    parsed = urlparse(url)
    if parsed.password:
        return url.replace(parsed.password, "***")
    return url
