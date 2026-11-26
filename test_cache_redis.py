"""Redis 缓存后端测试：用进程内 RESP 服务器验证协议、TTL、多实例共享、回退。"""
from __future__ import annotations

import socketserver
import threading
import time
import types
import unittest

from nds.cache import build_cache, CachedAdapter
from nds.cache_redis import RedisCache, RedisClient


class _FakeRedis:
    """RESP2 子集服务器：够 RedisClient 用，且能被多个客户端共享。"""

    def __init__(self):
        self.store: dict[str, list] = {}

    def now(self):
        return time.time()

    def handle(self, conn):
        try:
            while True:
                args = self._read(conn)
                if args is None:
                    return
                self._dispatch(conn, args)
        except (ConnectionError, OSError):
            return

    def _read_line(self, conn):
        buf = b""
        while not buf.endswith(b"\r\n"):
            ch = conn.recv(1)
            if not ch:
                return None
            buf += ch
        return buf[:-2]

    def _read(self, conn):
        line = self._read_line(conn)
        if line is None or line[:1] != b"*":
            return None
        count = int(line[1:])
        args = []
        for _ in range(count):
            head = self._read_line(conn)
            if head is None or head[:1] != b"$":
                return None
            length = int(head[1:])
            data = b""
            while len(data) < length:
                data += conn.recv(length - len(data))
            conn.recv(2)
            args.append(data)
        return args

    def _ok(self, conn):
        conn.sendall(b"+OK\r\n")

    def _bulk(self, conn, data):
        if data is None:
            conn.sendall(b"$-1\r\n")
        else:
            conn.sendall(b"$" + str(len(data)).encode() + b"\r\n" + data + b"\r\n")

    def _int(self, conn, value):
        conn.sendall(b":" + str(value).encode() + b"\r\n")

    def _dispatch(self, conn, args):
        cmd = args[0].upper()
        if cmd == b"PING":
            conn.sendall(b"+PONG\r\n")
        elif cmd in (b"AUTH", b"SELECT"):
            self._ok(conn)
        elif cmd == b"SET":
            self.store[args[1].decode()] = [args[2], None]
            self._ok(conn)
        elif cmd == b"SETEX":
            seconds = int(args[2])
            self.store[args[1].decode()] = [args[3], self.now() + seconds]
            self._ok(conn)
        elif cmd == b"GET":
            item = self.store.get(args[1].decode())
            if item is None:
                self._bulk(conn, None)
            elif item[1] and self.now() > item[1]:
                del self.store[args[1].decode()]
                self._bulk(conn, None)
            else:
                self._bulk(conn, item[0])
        elif cmd == b"DEL":
            removed = sum(1 for a in args[1:] if self.store.pop(a.decode(), None) is not None)
            self._int(conn, removed)
        elif cmd == b"DBSIZE":
            self._int(conn, len(self.store))
        elif cmd == b"SCAN":
            pattern = args[3].decode()
            prefix = pattern[:-1] if pattern.endswith("*") else pattern
            keys = [k for k in self.store if k.startswith(prefix)]
            body = b"*2\r\n$1\r\n0\r\n" + b"*" + str(len(keys)).encode() + b"\r\n"
            for key in keys:
                body += b"$" + str(len(key.encode())).encode() + b"\r\n" + key.encode() + b"\r\n"
            conn.sendall(body)
        else:
            conn.sendall(b"-ERR unknown command\r\n")


class _Server(socketserver.ThreadingTCPServer):
    daemon_threads = True
    allow_reuse_address = True


class _Handler(socketserver.BaseRequestHandler):
    def handle(self):
        self.server.fake.handle(self.request)


class _FakeRedisServer:
    def __init__(self):
        self.fake = _FakeRedis()
        self.server = _Server(("127.0.0.1", 0), _Handler)
        self.server.fake = self.fake
        self.port = self.server.server_address[1]
        self.thread = threading.Thread(target=self.server.serve_forever, kwargs={"poll_interval": 0.05},
                                       daemon=True)
        self.thread.start()

    def stop(self):
        self.server.shutdown()
        self.server.server_close()


class RedisCacheTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.srv = _FakeRedisServer()
        cls.url = f"redis://127.0.0.1:{cls.srv.port}/0"

    @classmethod
    def tearDownClass(cls):
        cls.srv.stop()

    def cache(self, **kwargs):
        return RedisCache(self.url, ttl_s=kwargs.get("ttl_s", 30))

    def test_set_get_and_stats(self):
        cache = self.cache()
        cache.set(("p", "drama", "闪婚"), ["row"])
        self.assertEqual(["row"], cache.get(("p", "drama", "闪婚")))
        stats = cache.stats()
        self.assertEqual("redis", stats["backend"])
        self.assertEqual(1, stats["hits"])
        self.assertEqual(0, stats["misses"])

    def test_two_instances_share(self):
        a = self.cache()
        b = self.cache()
        key = ("p", "drama", "重生")
        a.set(key, [{"title": "重生"}])
        self.assertEqual([{"title": "重生"}], b.get(key))   # 跨实例命中

    def test_ttl_expiry(self):
        cache = self.cache(ttl_s=1)
        cache.set("k", "v")
        self.assertEqual("v", cache.get("k"))
        time.sleep(1.1)
        self.assertIsNone(cache.get("k"))
        self.assertEqual(1, cache.stats()["misses"])

    def test_clear_by_prefix(self):
        cache = self.cache()
        cache.set("a", 1)
        cache.set("b", 2)
        cache.clear()
        self.assertIsNone(cache.get("a"))
        self.assertIsNone(cache.get("b"))

    def test_binary_safe_values(self):
        cache = self.cache()
        value = {"blob": b"\x00\r\n\xff"}
        cache.set("bin", value)
        self.assertEqual(value, cache.get("bin"))


class BuildCacheTests(unittest.TestCase):
    def test_redis_backend_when_available(self):
        srv = _FakeRedisServer()
        try:
            settings = types.SimpleNamespace(cache_backend="redis",
                                             redis_url=f"redis://127.0.0.1:{srv.port}/0",
                                             redis_prefix="nds:", cache_ttl_s=30, cache_max_entries=8)
            cache, reason = build_cache(settings)
            self.assertEqual("redis", getattr(cache, "backend", ""))
            self.assertEqual("", reason)
        finally:
            srv.stop()

    def test_falls_back_to_memory_when_redis_down(self):
        settings = types.SimpleNamespace(cache_backend="redis",
                                         redis_url="redis://127.0.0.1:1/0",
                                         redis_prefix="nds:", cache_ttl_s=30, cache_max_entries=8)
        cache, reason = build_cache(settings)
        self.assertEqual("memory", getattr(cache, "backend", ""))
        self.assertIn("redis_unavailable", reason)

    def test_memory_backend_default(self):
        settings = types.SimpleNamespace(cache_backend="memory", redis_url="", redis_prefix="",
                                         cache_ttl_s=30, cache_max_entries=8)
        cache, reason = build_cache(settings)
        self.assertEqual("memory", getattr(cache, "backend", ""))
        self.assertEqual("", reason)


class RedisClientTests(unittest.TestCase):
    def test_url_parsing(self):
        client = RedisClient.from_url("redis://:secret@example.com:6380/2")
        self.assertEqual("example.com", client.host)
        self.assertEqual(6380, client.port)
        self.assertEqual(2, client.db)
        self.assertEqual("secret", client.password)


if __name__ == "__main__":
    unittest.main()
