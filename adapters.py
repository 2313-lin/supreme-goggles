"""平台适配器：把"某平台的一次召回"抽象成一个调用。

演示模式用 LocalCorpusAdapter（读本地语料，按平台隔离）；
接真实平台时把 config/platforms.json 的 adapter 改成 "http_json" 并填 base_url，
遵循下面的响应契约即可零改代码接入，或用 `register_adapter(name, factory)` 注册自定义适配器。
"""
from __future__ import annotations

import json
import urllib.parse
import urllib.request
from typing import Callable, Protocol

from .cache import CachedAdapter, TTLCache
from .channels import DRAMA_CHANNELS, NOVEL_CHANNELS, recall
from .corpus import Corpus
from .models import Candidate, Evidence, Sentence, SourceRef, Work
from .text import strip_punct
from .understanding import Understanding


class AdapterError(RuntimeError):
    pass


class Adapter(Protocol):
    name: str

    def search(self, uq: Understanding, kind: str) -> list[Candidate]: ...

    def health(self) -> bool: ...

    def detail(self, work_id: str) -> dict | None: ...


class LocalCorpusAdapter:
    """按平台隔离的本地语料召回（演示 / 测试 / 离线降级）。"""

    name = "local_corpus"

    def __init__(self, corpus: Corpus, platform_id: str):
        self.corpus = corpus
        self.platform_id = platform_id

    def search(self, uq: Understanding, kind: str) -> list[Candidate]:
        return recall(self.corpus, uq, kind, platform=self.platform_id)

    def health(self) -> bool:
        return any(w.source_for(self.platform_id) for w in self.corpus.works)

    def detail(self, work_id: str) -> dict | None:
        work = self.corpus.get(work_id)
        if not work or not work.source_for(self.platform_id):
            return None
        return work.to_card()


class HttpJsonAdapter:
    """通用 HTTP 适配器：零改代码接入"按约定返回 JSON"的平台或聚合服务。

    请求：GET {base_url}/search?q=<原始查询>&type=novel|drama&limit=20
    响应：
      {
        "works": [{
          "id": "book-1",                  # 可选，缺省用标题生成
          "title": "长夜将明",
          "author": "沈砚",
          "summary": "…",
          "themes": ["悬疑", "复仇"],
          "tags": ["双强"],
          "url": "https://…",
          "tier": "primary",               # 可选
          "content_level": "L2",           # 可选
          "channel": "character",          # 可选，命中通道；缺省 title_fuzzy/name_fuzzy
          "score": 0.8,                    # 可选，0–0.99
          "sentences": [                   # 可选，L3 句子/台词（用于证据展示）
            {"text": "她说她从来没有恨过我。", "type": "dialogue",
             "position": "第3章", "start_time": null, "end_time": null}
          ]
        }]
      }

    契约故意保持"最小": 平台只负责返回作品与可选正文片段，
    分层、排序、个性化仍由本服务统一决定，避免各平台各自排序破坏全局优先级。
    """

    name = "http_json"

    def __init__(self, platform_id: str = "", base_url: str = "", timeout_ms: int = 800,
                 tier: str = "primary", content_level: str = "L1",
                 api_key: str = "", api_key_header: str = "",
                 headers: dict[str, str] | None = None,
                 page_size: int = 20, max_pages: int = 1):
        self.platform_id = platform_id
        self.base_url = (base_url or "").rstrip("/")
        self.timeout_s = max(0.1, timeout_ms / 1000)
        self.tier = tier
        self.content_level = content_level
        self.api_key = api_key or ""
        self.api_key_header = api_key_header or ""
        self.headers = dict(headers or {})
        self.page_size = max(1, int(page_size))
        self.max_pages = max(1, int(max_pages))

    def _headers(self) -> dict[str, str]:
        headers = {"Accept": "application/json", "User-Agent": "nds-agent/1.0"}
        headers.update(self.headers)
        if self.api_key and self.api_key_header:
            headers[self.api_key_header] = self.api_key
        return headers

    def _params(self, params: dict) -> dict:
        if self.api_key and not self.api_key_header:
            merged = dict(params)
            merged["key"] = self.api_key
            return merged
        return params

    def _get(self, path: str, params: dict) -> dict:
        if not self.base_url:
            raise AdapterError("adapter_not_configured: 未配置 base_url")
        url = f"{self.base_url}{path}?{urllib.parse.urlencode(self._params(params))}"
        req = urllib.request.Request(url, headers=self._headers())
        try:
            with urllib.request.urlopen(req, timeout=self.timeout_s) as resp:
                return json.loads(resp.read().decode("utf-8", "replace"))
        except Exception as exc:  # 网络/解析/非 2xx 统一归一为 AdapterError
            message = str(exc)
            if self.api_key:
                message = message.replace(self.api_key, "***")
            raise AdapterError(f"http_error: {type(exc).__name__}: {message}") from exc

    # ------------------------------------------------------------------ API
    def search(self, uq: Understanding, kind: str) -> list[Candidate]:
        """按契约召回，支持分页。

        请求追加 `page`（1 起）与 `limit`（page_size）。响应可含：
        `works`（或 `data` / `results`）数组，以及可选 `total` / `has_more` / `next_page`。
        有 `total` 时按已收数量终止；否则靠 `has_more` / `next_page` 判定，最多 `max_pages` 页。
        """
        collected: list[dict] = []
        page = 1
        total: int | None = None
        while page <= self.max_pages:
            data = self._get("/search", {
                "q": uq.raw,
                "type": "novel" if kind == "novel" else "drama",
                "limit": str(self.page_size),
                "page": str(page),
            })
            works = data.get("works")
            if not isinstance(works, list):
                works = data.get("data")
            if not isinstance(works, list):
                works = data.get("results")
            if not isinstance(works, list):
                raise AdapterError("bad_response: 缺少 works 数组")
            collected.extend(works)
            if total is None and isinstance(data.get("total"), int):
                total = int(data["total"])
            if total is not None and len(collected) >= total:
                break
            if not (data.get("has_more") or data.get("next_page")):
                break
            page += 1

        out: list[Candidate] = []
        seen: set[str] = set()
        for row in collected:
            cand = self._to_candidate(row, kind, uq)
            if cand is not None and cand.work.id not in seen:
                seen.add(cand.work.id)
                out.append(cand)
        return out

    def health(self) -> bool:
        try:
            self._get("/health", {})
            return True
        except AdapterError:
            return False

    def detail(self, work_id: str) -> dict | None:
        try:
            data = self._get(f"/works/{urllib.parse.quote(work_id)}", {})
        except AdapterError:
            return None
        return data.get("work")

    # -------------------------------------------------------------- mapping
    def _to_candidate(self, row: dict, kind: str, uq: Understanding) -> Candidate | None:
        if not isinstance(row, dict):
            return None
        title = str(row.get("title") or "").strip()
        if not title:
            return None
        work_type = "novel" if kind == "novel" else "short_drama"
        work_id = str(row.get("id") or f"{self.platform_id}:{strip_punct(title)[:24]}")
        sentences = []
        for index, item in enumerate(row.get("sentences") or [], start=1):
            if not isinstance(item, dict) or not str(item.get("text") or "").strip():
                continue
            sentences.append(Sentence(
                id=f"{work_id}:s{index}",
                work_id=work_id,
                type=str(item.get("type") or ("subtitle" if work_type == "short_drama" else "narration")),
                text=str(item["text"]).strip(),
                position=str(item.get("position") or ""),
                start_time=item.get("start_time"),
                end_time=item.get("end_time"),
            ))
        work = Work(
            id=work_id,
            type=work_type,
            title=title,
            aliases=[str(a) for a in row.get("aliases") or []],
            author=str(row.get("author") or ""),
            summary=str(row.get("summary") or ""),
            themes=[str(t) for t in row.get("themes") or []],
            tags=[str(t) for t in row.get("tags") or []],
            year=row.get("year"),
            popularity=float(row.get("popularity") or 0.5),
            sentences=sentences,
            sources=[SourceRef(
                platform=self.platform_id or "http",
                url=str(row.get("url") or ""),
                tier=str(row.get("tier") or self.tier),
                content_level=str(row.get("content_level") or self.content_level),
            )],
        )
        allowed = NOVEL_CHANNELS if kind == "novel" else DRAMA_CHANNELS
        default_channel = "title_fuzzy" if kind == "novel" else "name_fuzzy"
        channel = str(row.get("channel") or default_channel)
        if channel not in allowed:
            channel = default_channel
        try:
            score = max(0.0, min(0.99, float(row.get("score", 0.7))))
        except (TypeError, ValueError):
            score = 0.7

        evidence: list[Evidence] = []
        if sentences:
            first = sentences[0]
            evidence.append(Evidence(
                kind="line" if first.type == "subtitle" else "sentence",
                text=first.text, position=first.position,
                matched_query=uq.raw, score=score, verified=False,
            ))
        else:
            evidence.append(Evidence(kind="title", text=title, position="远程平台标题",
                                     matched_query=uq.raw, score=score, verified=False))
        return Candidate(work=work, channel=channel, score=score,
                         evidence=evidence, platforms={self.platform_id} if self.platform_id else set())


AdapterFactory = Callable[[str, object], Adapter]


def _build_icofun(name: str, defn) -> Adapter:
    # 延迟导入：IcofunShortDramaAdapter 反向引用本模块的 AdapterError
    from .adapters_icofun import IcofunShortDramaAdapter
    return IcofunShortDramaAdapter(
        platform_id=name,
        base_url=getattr(defn, "base_url", ""),
        timeout_ms=getattr(defn, "timeout_ms", 4000),
        tier=getattr(defn, "tier", "aggregator"),
        content_level=getattr(defn, "content_level", "L1"),
        api_key=getattr(defn, "api_key", ""),
        api_key_header=getattr(defn, "api_key_header", ""),
        headers=getattr(defn, "headers", None),
    )


def _build_dracin(name: str, defn) -> Adapter:
    from .adapters_dracin import DracinAdapter
    return DracinAdapter(
        platform_id=name,
        base_url=getattr(defn, "base_url", "https://dracinapi.com"),
        provider=getattr(defn, "provider", "dramabox"),
        api_key=getattr(defn, "api_key", ""),
        timeout_ms=getattr(defn, "timeout_ms", 8000),
        tier=getattr(defn, "tier", "aggregator"),
        content_level=getattr(defn, "content_level", "L1"),
        page_size=getattr(defn, "page_size", 20),
        max_pages=getattr(defn, "max_pages", 1),
    )


_HTTP_ADAPTERS = {"http_json", "icofun_shortdrama", "dracin"}


class AdapterHub:
    """适配器工厂注册表：平台配置里的 adapter 字段 → 实例。

    `cache` 非空时，HTTP 类适配器会被 `CachedAdapter` 包一层 TTL/LRU 缓存；
    `local_corpus` 不缓存（本机数据，无网络成本）。
    """

    def __init__(self, corpus: Corpus, cache: TTLCache | None = None):
        self.corpus = corpus
        self.cache = cache
        self._factories: dict[str, AdapterFactory] = {
            "local_corpus": lambda name, defn: LocalCorpusAdapter(self.corpus, name),
            "http_json": lambda name, defn: HttpJsonAdapter(
                platform_id=name,
                base_url=getattr(defn, "base_url", ""),
                timeout_ms=getattr(defn, "timeout_ms", 800),
                tier=getattr(defn, "tier", "primary"),
                content_level=getattr(defn, "content_level", "L1"),
                api_key=getattr(defn, "api_key", ""),
                api_key_header=getattr(defn, "api_key_header", ""),
                headers=getattr(defn, "headers", None),
                page_size=getattr(defn, "page_size", 20),
                max_pages=getattr(defn, "max_pages", 1),
            ),
            "icofun_shortdrama": _build_icofun,
            "dracin": _build_dracin,
        }
        self._instances: dict[str, Adapter] = {}

    def register(self, name: str, factory: AdapterFactory) -> None:
        self._factories[name] = factory

    def get(self, platform_id: str, defn) -> Adapter:
        adapter_name = getattr(defn, "adapter", "local_corpus")
        key = f"{platform_id}:{adapter_name}:{getattr(defn, 'base_url', '')}"
        if key not in self._instances:
            factory = self._factories.get(adapter_name)
            if factory is None:
                raise AdapterError(f"unknown_adapter: {adapter_name}")
            adapter = factory(platform_id, defn)
            if self.cache is not None and adapter_name in _HTTP_ADAPTERS:
                adapter = CachedAdapter(adapter, self.cache)
            self._instances[key] = adapter
        return self._instances[key]
