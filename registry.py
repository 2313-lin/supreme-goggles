"""平台注册表 + 熔断器 + 健康评分 + 降级链（§4.2 / §7）。

新增平台只改 config/platforms.json，不改代码：注册表热插拔。
"""
from __future__ import annotations

import json
import os
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path

from .config import CONFIG_DIR


@dataclass
class PlatformDef:
    id: str
    type: str                       # novel | drama
    role: str                       # primary | backup | aggregator
    stage: int                      # 降级链层级：1 主源，2 第一备用 ...
    adapter: str
    tier: str = "L1"                # L1 | L2 | L3 | L1_L3
    timeout_ms: int = 800
    priority: int = 100             # 越小越优先（同层排序 / 同分时的来源偏好）
    content_level: str = "L1"
    enabled: bool = True
    base_url: str = ""
    notes: str = ""
    breaker_threshold: int = 3      # 连续失败多少次打开熔断
    breaker_cooldown_s: float = 20.0  # 打开后冷却多久进入半开
    api_key: str = ""               # 需要鉴权的聚合源（建议用环境变量注入）
    api_key_header: str = ""        # 非空则放 header；为空则作为 query 参数 key=
    headers: dict[str, str] = field(default_factory=dict)
    page_size: int = 20             # http_json 契约：每页条数
    max_pages: int = 1              # http_json 契约：最多拉取页数
    provider: str = ""              # 多租户聚合源（如 DracinAPI）的 provider slug


class CircuitBreaker:
    """三态熔断器：closed → open → half_open → closed。

    - 连续失败达到阈值即打开，冷却期内直接跳过，不再消耗预算；
    - 冷却结束后进入 half_open，**同一时刻只放行一个试探**（并发安全）；
    - 试探成功恢复 closed，失败立即重新打开。

    并发语义：`can_try()` 是"申请名额"（会占用试探位），`available()` 只读探测、
    不占名额，用于状态展示与跳过标注。
    """

    def __init__(self, fail_threshold: int = 3, cooldown_s: float = 20.0,
                 trial_timeout_s: float = 5.0):
        self.fail_threshold = fail_threshold
        self.cooldown_s = cooldown_s
        self.trial_timeout_s = trial_timeout_s
        self.state = "closed"
        self.failures = 0
        self.opened_at = 0.0
        self.last_error = ""
        self._trial_in_flight = False
        self._trial_started_at = 0.0
        self._lock = threading.Lock()

    def can_try(self) -> bool:
        """申请一次调用许可；半开时只让一个请求拿到试探名额。"""
        with self._lock:
            if self.state == "closed":
                return True
            if self.state == "open":
                if time.time() - self.opened_at >= self.cooldown_s:
                    self.state = "half_open"
                    self._trial_in_flight = True
                    self._trial_started_at = time.time()
                    return True
                return False
            # half_open
            if self._trial_in_flight and time.time() - self._trial_started_at < self.trial_timeout_s:
                return False
            self._trial_in_flight = True
            self._trial_started_at = time.time()
            return True

    def available(self) -> bool:
        """只读探测：不改变状态、不占用试探名额。"""
        with self._lock:
            if self.state == "closed":
                return True
            if self.state == "open":
                return time.time() - self.opened_at >= self.cooldown_s
            return not self._trial_in_flight

    def record_success(self) -> None:
        with self._lock:
            self.state = "closed"
            self.failures = 0
            self.last_error = ""
            self._trial_in_flight = False
            self._trial_started_at = 0.0

    def record_failure(self, error: str = "") -> None:
        with self._lock:
            self.failures += 1
            self.last_error = error
            self._trial_in_flight = False
            self._trial_started_at = 0.0
            if self.state == "half_open" or self.failures >= self.fail_threshold:
                self.state = "open"
                self.opened_at = time.time()

    def reset(self) -> None:
        with self._lock:
            self.state = "closed"
            self.failures = 0
            self.opened_at = 0.0
            self.last_error = ""
            self._trial_in_flight = False
            self._trial_started_at = 0.0

    def to_dict(self) -> dict:
        return {
            "state": self.state,
            "failures": self.failures,
            "fail_threshold": self.fail_threshold,
            "cooldown_s": self.cooldown_s,
            "last_error": self.last_error,
            "trial_in_flight": self._trial_in_flight,
        }


@dataclass
class PlatformRuntime:
    defn: PlatformDef
    breaker: CircuitBreaker | None = None
    forced_down: bool = False
    simulated_latency_ms: int = 0
    total_calls: int = 0
    total_failures: int = 0
    last_latency_ms: int = 0
    last_error: str = ""

    def __post_init__(self) -> None:
        if self.breaker is None:
            self.breaker = CircuitBreaker(self.defn.breaker_threshold, self.defn.breaker_cooldown_s)

    @property
    def id(self) -> str:
        return self.defn.id

    def can_try(self) -> bool:
        if not self.defn.enabled or self.forced_down:
            return False
        return self.breaker.can_try()

    def available(self) -> bool:
        """只读判断（不占用半开试探名额），用于展示与跳过标注。"""
        if not self.defn.enabled or self.forced_down:
            return False
        return self.breaker.available()

    def health_score(self) -> float:
        if not self.defn.enabled or self.forced_down:
            return 0.0
        if self.breaker.state == "open":
            return 0.05
        score = 1.0
        score -= 0.15 * self.breaker.failures
        if self.last_latency_ms > self.defn.timeout_ms * 0.8:
            score -= 0.2
        return max(0.0, min(1.0, score))

    def state_label(self) -> str:
        if not self.defn.enabled:
            return "disabled"
        if self.forced_down:
            return "unavailable"
        if self.breaker.state == "open":
            return "circuit_open"
        if self.breaker.state == "half_open":
            return "half_open"
        if self.breaker.failures:
            return "degraded"
        return "healthy"

    def report_success(self, latency_ms: int) -> None:
        self.total_calls += 1
        self.last_latency_ms = latency_ms
        self.breaker.record_success()

    def report_failure(self, reason: str) -> None:
        self.total_calls += 1
        self.total_failures += 1
        self.last_error = reason
        self.breaker.record_failure(reason)

    def to_dict(self) -> dict:
        return {
            "id": self.defn.id,
            "type": self.defn.type,
            "role": self.defn.role,
            "stage": self.defn.stage,
            "tier": self.defn.tier,
            "content_level": self.defn.content_level,
            "timeout_ms": self.defn.timeout_ms,
            "priority": self.defn.priority,
            "enabled": self.defn.enabled,
            "state": self.state_label(),
            "health_score": round(self.health_score(), 2),
            "latency_ms": self.last_latency_ms,
            "calls": self.total_calls,
            "failures": self.total_failures,
            "last_error": self.last_error,
            "breaker": self.breaker.to_dict(),
            "simulated_latency_ms": self.simulated_latency_ms,
            "forced_down": self.forced_down,
            "adapter": self.defn.adapter,
        }


class PlatformRegistry:
    def __init__(self, platforms: list[PlatformRuntime]):
        self.platforms = {p.id: p for p in platforms}
        self._lock = threading.Lock()

    @classmethod
    def load(cls, path: Path | None = None) -> "PlatformRegistry":
        path = path or (CONFIG_DIR / "platforms.json")
        raw = json.loads(Path(path).read_text(encoding="utf-8"))
        runtimes = []
        for item in raw["platforms"]:
            defn = PlatformDef(
                id=item["id"],
                type=item["type"],
                role=item.get("role", "backup"),
                stage=int(item.get("stage", 2)),
                adapter=item.get("adapter", "local_corpus"),
                tier=item.get("tier", "L1"),
                timeout_ms=int(item.get("timeout_ms", 800)),
                priority=int(item.get("priority", 100)),
                content_level=item.get("content_level", "L1"),
                enabled=bool(item.get("enabled", True)),
                base_url=item.get("base_url", ""),
                notes=item.get("notes", ""),
                breaker_threshold=int(item.get("breaker_threshold", 3)),
                breaker_cooldown_s=float(item.get("breaker_cooldown_s", 20.0)),
                api_key=os.environ.get(f"NDS_API_KEY_{item['id'].upper()}", item.get("api_key", "")),
                api_key_header=item.get("api_key_header", ""),
                headers=dict(item.get("headers") or {}),
                page_size=int(item.get("page_size", 20)),
                max_pages=int(item.get("max_pages", 1)),
                provider=item.get("provider", ""),
            )
            runtimes.append(PlatformRuntime(defn=defn))
        return cls(runtimes)

    # ------------------------------------------------------------------ query
    def get(self, platform_id: str) -> PlatformRuntime | None:
        return self.platforms.get(platform_id)

    def by_type(self, kind: str) -> list[PlatformRuntime]:
        return [p for p in self.platforms.values() if p.defn.type == kind]

    def plan(self, kind: str) -> list[list[str]]:
        """返回降级链：[[主源...], [备用1...], [备用2...], ...]。

        kind 使用作品类型（novel / short_drama），平台配置里短剧平台记为 drama。
        """
        platform_type = {"novel": "novel", "short_drama": "drama", "drama": "drama"}.get(kind, kind)
        items = [p for p in self.platforms.values() if p.defn.type == platform_type]
        items.sort(key=lambda p: (p.defn.stage, p.defn.priority))
        levels: dict[int, list[str]] = {}
        for p in items:
            levels.setdefault(p.defn.stage, []).append(p.id)
        return [sorted(ids, key=lambda pid: self.platforms[pid].defn.priority) for _, ids in sorted(levels.items())]

    def snapshot(self) -> list[dict]:
        order = {"novel": 0, "drama": 1}
        return [p.to_dict() for p in sorted(
            self.platforms.values(),
            key=lambda p: (order.get(p.defn.type, 9), p.defn.stage, p.defn.priority))]

    # -------------------------------------------------------------- mutations
    def register(self, defn: PlatformDef) -> PlatformRuntime:
        with self._lock:
            rt = PlatformRuntime(defn=defn)
            self.platforms[defn.id] = rt
            return rt

    def reload(self, registry: "PlatformRegistry") -> None:
        """热插拔：保留已有运行时状态（熔断计数），替换配置。"""
        with self._lock:
            for pid, new_rt in registry.platforms.items():
                old = self.platforms.get(pid)
                if old:
                    new_rt.breaker = old.breaker
                    new_rt.total_calls = old.total_calls
                    new_rt.total_failures = old.total_failures
                    new_rt.last_latency_ms = old.last_latency_ms
                    new_rt.last_error = old.last_error
                    new_rt.forced_down = old.forced_down
                    new_rt.simulated_latency_ms = old.simulated_latency_ms
            self.platforms = registry.platforms

    def simulate(self, platform_id: str, down: bool | None = None, latency_ms: int | None = None,
                 reset_breaker: bool = False) -> PlatformRuntime:
        rt = self.platforms[platform_id]
        if down is not None:
            rt.forced_down = down
        if latency_ms is not None:
            rt.simulated_latency_ms = max(0, latency_ms)
        if reset_breaker:
            rt.breaker.reset()
        return rt
