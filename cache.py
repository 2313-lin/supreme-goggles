"""L1 缓存：TTL + LRU，线程安全，进程内。

真实聚合源单次约 2s，同一关键词在短时间内的重复检索直接命中缓存，
把端到端耗时从秒级降到毫秒级；TTL 同时限制元数据的新鲜度。
"""
from __future__ import annotations

import threading
import time
from collections import OrderedDict
from typing import Any

from .text import strip_punct


class TTLCache:
    backend = "memory"

    def __init__(self, ttl_s: float = 30.0, max_entries: int = 256):
        self.ttl_s = max(0.0, float(ttl_s))
        self.max_entries = max(1, int(max_entries))
        self._data: OrderedDict[Any, tuple[float, Any]] = OrderedDict()
        self._lock = threading.Lock()
        self.hits = 0
        self.misses = 0
        self.evictions = 0

    def get(self, key: Any) -> Any | None:
        with self._lock:
            item = self._data.get(key)
            if item is None:
                self.misses += 1
                return None
            expires_at, value = item
            if time.time() > expires_at:
                del self._data[key]
                self.misses += 1
                return None
            self._data.move_to_end(key)
            self.hits += 1
            return value

    def set(self, key: Any, value: Any) -> None:
        with self._lock:
            self._data[key] = (time.time() + self.ttl_s, value)
            self._data.move_to_end(key)
            while len(self._data) > self.max_entries:
                self._data.popitem(last=False)
                self.evictions += 1

    def clear(self) -> None:
        with self._lock:
            self._data.clear()

    def __len__(self) -> int:
        with self._lock:
            return len(self._data)

    def stats(self) -> dict[str, Any]:
        with self._lock:
            total = self.hits + self.misses
            return {
                "backend": "memory",
                "ttl_s": self.ttl_s,
                "max_entries": self.max_entries,
                "entries": len(self._data),
                "hits": self.hits,
                "misses": self.misses,
                "hit_rate": round(self.hits / total, 3) if total else 0.0,
                "evictions": self.evictions,
            }


def build_cache(settings) -> tuple[Any, str]:
    """按 settings 构建缓存；返回 (cache, fallback_reason)。

    - backend == "redis"：尝试连 Redis，失败回退到内存并说明原因；
    - 其余：内存缓存。
    """
    ttl = float(getattr(settings, "cache_ttl_s", 30.0))
    max_entries = int(getattr(settings, "cache_max_entries", 256))
    backend = getattr(settings, "cache_backend", "memory")
    if backend == "redis":
        from .cache_redis import RedisCache, RedisError
        try:
            cache = RedisCache(getattr(settings, "redis_url", "redis://127.0.0.1:6379/0"),
                               getattr(settings, "redis_prefix", "nds:"), ttl)
            cache.client.ping()      # 立即可达才启用
            return cache, ""
        except (RedisError, OSError, ValueError) as exc:
            return TTLCache(ttl, max_entries), f"redis_unavailable: {exc}"
    return TTLCache(ttl, max_entries), ""


class CachedAdapter:
    """适配器装饰器：按 (平台, 类型, 归一化查询) 缓存召回结果。"""

    def __init__(self, inner, cache: TTLCache):
        self.inner = inner
        self.cache = cache
        self.name = f"{getattr(inner, 'name', 'adapter')}+cache"

    def _key(self, uq, kind: str):
        # base_url 必须参与 key：换源/热加载后不能命中旧源的缓存
        return (getattr(self.inner, "platform_id", ""),
                getattr(self.inner, "base_url", ""),
                kind, strip_punct(uq.raw))

    def search(self, uq, kind: str):
        key = self._key(uq, kind)
        cached = self.cache.get(key)
        if cached is not None:
            return cached
        data = self.inner.search(uq, kind)
        self.cache.set(key, data)
        return data

    def health(self) -> bool:
        return self.inner.health()

    def detail(self, work_id: str):
        return self.inner.detail(work_id)
