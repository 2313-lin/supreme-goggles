"""配置：路径解析与运行时参数（settings.json + 环境变量覆盖）。"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
CONFIG_DIR = ROOT / "config"
DATA_DIR = ROOT / "data"
WEB_DIR = ROOT / "web"

_LIVE_AGG = {
    "id": "agg_drama", "type": "drama", "role": "aggregator", "stage": 4,
    "adapter": "icofun_shortdrama", "tier": "L1", "content_level": "L1",
    "timeout_ms": 4000, "priority": 60, "enabled": True,
    "base_url": "https://api.icofun.cn",
    "notes": "小齐API「短剧搜索与排名榜」：公开访问、无需密钥，仅取 L1 元数据",
}


def ensure_live_config() -> Path:
    """生成并返回 config/platforms.live.json（默认演示源 + 真实短剧聚合源）。"""
    live = CONFIG_DIR / "platforms.live.json"
    if not live.exists():
        data = json.loads((CONFIG_DIR / "platforms.json").read_text(encoding="utf-8"))
        data["_comment"] = ("联调配置：默认演示源 + 真实短剧聚合源（小齐API，公开/无需密钥）。"
                            "启用方式：NDS_PLATFORMS=config/platforms.live.json")
        platforms = data["platforms"]
        for index, platform in enumerate(platforms):
            if platform["id"] == "agg_drama":
                platforms[index] = dict(_LIVE_AGG)
                break
        else:
            platforms.append(dict(_LIVE_AGG))
        live.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return live


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, default))
    except (TypeError, ValueError):
        return default


@dataclass
class Settings:
    """全局参数。默认值与设计文档 §4 / §7 / §8 对齐。"""

    host: str = "127.0.0.1"
    port: int = 8765

    # 平台注册表文件（联调时可用 NDS_PLATFORMS=config/platforms.live.json）
    platforms_config: str = field(
        default_factory=lambda: os.environ.get("NDS_PLATFORMS", str(CONFIG_DIR / "platforms.json")))

    # 单次搜索总预算（毫秒）。超时必有结果：到点即返回已到部分。§1.3-6
    budget_ms: int = 1500
    # 少于该值视为"结果不足"，触发降级拓展。§1.3-5
    min_results: int = 3
    # 单类结果上限
    max_results_per_group: int = 10
    # 全部 tab 时每类上限
    max_results_all: int = 8

    # L1 缓存（真实聚合源单次约 2s，缓存后同查询毫秒级返回）
    cache_ttl_s: float = 30.0
    cache_max_entries: int = 256
    cache_backend: str = "memory"       # memory | redis（多实例共享）
    redis_url: str = "redis://127.0.0.1:6379/0"
    redis_prefix: str = "nds:"

    # 安全护栏
    max_query_chars: int = 200
    rate_limit_per_min: int = 30
    # 高危（写）操作需要人工确认；只读检索不需要。§1.3-7
    admin_token: str = field(default_factory=lambda: os.environ.get("NDS_ADMIN_TOKEN", "dev-admin-token"))

    # 记忆
    memory_db: str = field(default_factory=lambda: os.environ.get("NDS_MEMORY_DB", str(DATA_DIR / "memory.db")))
    session_ttl_days: int = 7
    personalization_max_boost: float = 0.05

    # 审计
    audit_log: str = field(default_factory=lambda: os.environ.get("NDS_AUDIT_LOG", str(DATA_DIR / "audit.jsonl")))

    # 可选 LLM 重排（无 key 时自动跳过，不阻塞主链路）§2 "LLM 重排可超时跳过"
    llm_rerank: bool = field(default_factory=lambda: os.environ.get("NDS_LLM_RERANK", "0") == "1")
    llm_timeout_ms: int = 600

    # 演示/测试用：平台故障注入与延迟模拟
    simulate_latency_ms: int = 0

    @classmethod
    def load(cls) -> "Settings":
        path = CONFIG_DIR / "settings.json"
        data: dict[str, Any] = {}
        if path.exists():
            data = json.loads(path.read_text(encoding="utf-8"))
        known = {f for f in cls.__dataclass_fields__}  # type: ignore[attr-defined]
        kwargs = {k: v for k, v in data.items() if k in known}
        obj = cls(**kwargs)
        # 环境变量优先级最高
        obj.host = os.environ.get("NDS_HOST", obj.host)
        obj.port = _env_int("NDS_PORT", obj.port)
        obj.budget_ms = _env_int("NDS_BUDGET_MS", obj.budget_ms)
        obj.rate_limit_per_min = _env_int("NDS_RATE_LIMIT", obj.rate_limit_per_min)
        obj.audit_log = os.environ.get("NDS_AUDIT_LOG", obj.audit_log)
        obj.memory_db = os.environ.get("NDS_MEMORY_DB", obj.memory_db)
        obj.admin_token = os.environ.get("NDS_ADMIN_TOKEN", obj.admin_token)
        obj.platforms_config = os.environ.get("NDS_PLATFORMS", obj.platforms_config)
        obj.cache_ttl_s = float(os.environ.get("NDS_CACHE_TTL", obj.cache_ttl_s))
        obj.cache_max_entries = _env_int("NDS_CACHE_MAX", obj.cache_max_entries)
        obj.cache_backend = os.environ.get("NDS_CACHE_BACKEND", obj.cache_backend)
        obj.redis_url = os.environ.get("NDS_REDIS_URL", obj.redis_url)
        obj.redis_prefix = os.environ.get("NDS_REDIS_PREFIX", obj.redis_prefix)
        if "NDS_LLM_RERANK" in os.environ:
            obj.llm_rerank = os.environ["NDS_LLM_RERANK"] == "1"
        return obj
