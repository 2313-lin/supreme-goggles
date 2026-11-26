"""领域数据模型（对应设计文档 §3 数据模型）。"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class SourceRef:
    """同一作品在某平台的上架记录。作品跨平台去重后保留全部来源。"""

    platform: str
    url: str = ""
    tier: str = "primary"          # primary | backup | aggregator
    content_level: str = "L1"      # L1 搜索层 / L2 详情层 / L3 内容层
    status: str = "healthy"        # healthy | degraded | unavailable

    def to_dict(self) -> dict[str, Any]:
        return {
            "platform": self.platform,
            "url": self.url,
            "tier": self.tier,
            "content_level": self.content_level,
            "status": self.status,
        }


@dataclass
class Character:
    id: str
    work_id: str
    name: str
    aliases: list[str] = field(default_factory=list)
    role: str = "配角"              # 主角 | 配角 | 反派 | 其他
    description: str = ""
    importance_score: float = 0.5
    pinyin: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "work_id": self.work_id,
            "name": self.name,
            "aliases": list(self.aliases),
            "role": self.role,
            "description": self.description,
            "importance_score": self.importance_score,
        }


@dataclass
class Sentence:
    """原文句子（小说）或台词（短剧字幕/ASR）。"""

    id: str
    work_id: str
    type: str = "narration"        # narration | dialogue | subtitle
    text: str = ""
    position: str = ""             # 章节 / 段落 / 镜号
    scene_id: str | None = None
    start_time: float | None = None
    end_time: float | None = None
    keywords: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "work_id": self.work_id,
            "type": self.type,
            "text": self.text,
            "position": self.position,
            "scene_id": self.scene_id,
            "start_time": self.start_time,
            "end_time": self.end_time,
            "keywords": list(self.keywords),
        }


@dataclass
class Work:
    id: str
    type: str                       # novel | short_drama
    title: str
    aliases: list[str] = field(default_factory=list)
    author: str = ""
    summary: str = ""
    themes: list[str] = field(default_factory=list)
    tags: list[str] = field(default_factory=list)
    cover: str = ""
    year: int | None = None
    episodes: int | None = None
    popularity: float = 0.0
    characters: list[Character] = field(default_factory=list)
    sentences: list[Sentence] = field(default_factory=list)
    sources: list[SourceRef] = field(default_factory=list)
    pinyin: str = ""

    @property
    def platform_ids(self) -> list[str]:
        return [s.platform for s in self.sources]

    def source_for(self, platform: str) -> SourceRef | None:
        for s in self.sources:
            if s.platform == platform:
                return s
        return None

    def characters_named(self) -> list[Character]:
        return [c for c in self.characters if c.name]

    def to_card(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "type": self.type,
            "title": self.title,
            "aliases": list(self.aliases),
            "author": self.author,
            "summary": self.summary,
            "themes": list(self.themes),
            "tags": list(self.tags),
            "cover": self.cover,
            "year": self.year,
            "episodes": self.episodes,
            "popularity": self.popularity,
            "characters": [c.to_dict() for c in self.characters],
            "sources": [s.to_dict() for s in self.sources],
            "platforms": self.platform_ids,
        }


@dataclass
class Evidence:
    """命中证据：用户看到的"为什么返回这条"。"""

    kind: str                       # sentence | line | character | theme | title
    text: str = ""
    position: str = ""
    matched_query: str = ""
    score: float = 0.0
    verified: bool = False
    channel: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "text": self.text,
            "position": self.position,
            "matched_query": self.matched_query,
            "score": round(self.score, 3),
            "verified": self.verified,
            "channel": self.channel,
        }


@dataclass
class Candidate:
    """某一路召回产生的候选。"""

    work: Work
    channel: str
    score: float
    evidence: list[Evidence] = field(default_factory=list)
    platforms: set[str] = field(default_factory=set)


@dataclass
class PlatformCall:
    platform: str
    state: str = "healthy"          # healthy | degraded | circuit_open | unavailable | skipped
    used: bool = False
    latency_ms: int = 0
    candidates: int = 0
    reason: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "platform": self.platform,
            "state": self.state,
            "used": self.used,
            "latency_ms": self.latency_ms,
            "candidates": self.candidates,
            "reason": self.reason,
        }
