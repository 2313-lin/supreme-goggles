"""DracinAPI 适配器：42 平台短剧聚合（需 API key）。

鉴权：`Authorization: Bearer <api_key>`（SDK 官方实现）。
搜索：GET {base_url}/{provider}/api/v1/search?keyword=<kw>[&page=&limit=]
返回：官方 SDK 直接 `resp.json()`——通常是 JSON 数组（单页），元素含
`title / id / episodes / desc / cover / genre` 等；若返回对象且含 `data/results`
与 `has_more / total`，则按分页逐页拉取。

合规：只取 L1 元数据；不请求 `play` / `episodes` 等播放与内容接口。
"""
from __future__ import annotations

import json
import urllib.parse
import urllib.request

from .adapters import AdapterError
from .models import Candidate, Evidence, SourceRef, Work
from .text import strip_punct
from .understanding import Understanding


class DracinAdapter:
    name = "dracin"

    def __init__(self, platform_id: str = "dracin", base_url: str = "https://dracinapi.com",
                 provider: str = "dramabox", api_key: str = "", timeout_ms: int = 8000,
                 tier: str = "aggregator", content_level: str = "L1",
                 page_size: int = 20, max_pages: int = 1):
        self.platform_id = platform_id
        self.base_url = (base_url or "").rstrip("/")
        self.provider = provider or "dramabox"
        self.api_key = api_key or ""
        self.timeout_s = max(0.5, timeout_ms / 1000)
        self.tier = tier
        self.content_level = content_level
        self.page_size = max(1, int(page_size))
        self.max_pages = max(1, int(max_pages))

    # ------------------------------------------------------------------ HTTP
    def _get(self, params: dict):
        if not self.api_key:
            raise AdapterError("auth_required: 缺少 API key（设置 NDS_API_KEY_DRACIN）")
        url = (f"{self.base_url}/{self.provider}/api/v1/search"
               f"?{urllib.parse.urlencode(params)}")
        req = urllib.request.Request(url, headers={
            "Authorization": f"Bearer {self.api_key}",
            "Accept": "application/json",
            "User-Agent": "nds-agent/1.0",
        })
        try:
            with urllib.request.urlopen(req, timeout=self.timeout_s) as resp:
                raw = resp.read()
        except Exception as exc:
            message = str(exc).replace(self.api_key, "***")
            raise AdapterError(f"http_error: {type(exc).__name__}: {message}") from exc
        try:
            return json.loads(raw.decode("utf-8", "replace"))
        except json.JSONDecodeError as exc:
            raise AdapterError("bad_response: 返回不是合法 JSON") from exc

    def health(self) -> bool:
        try:
            return isinstance(self._get({"keyword": "测试", "limit": "1"}), (list, dict))
        except AdapterError:
            return False

    def detail(self, work_id: str) -> dict | None:
        return None

    # ---------------------------------------------------------------- search
    def search(self, uq: Understanding, kind: str) -> list[Candidate]:
        if kind != "short_drama":
            return []
        if uq.intent == "sentence":
            return []                      # L1 元数据只按剧名检索
        keyword = self._keyword(uq)
        if not keyword:
            return []

        collected: list[dict] = []
        page = 1
        while page <= self.max_pages:
            data = self._get({"keyword": keyword, "page": str(page), "limit": str(self.page_size)})
            if isinstance(data, list):
                collected.extend(data)
                break                            # 官方契约：裸数组 = 单页
            if isinstance(data, dict):
                items = data.get("data") or data.get("results") or data.get("works")
                if not isinstance(items, list) or not items:
                    break
                collected.extend(items)
                if not (data.get("has_more") or data.get("next_page")):
                    break
            else:
                break
            page += 1

        out: list[Candidate] = []
        seen: set[str] = set()
        for row in collected:
            cand = self._to_candidate(row, uq)
            if cand is not None and cand.work.id not in seen:
                seen.add(cand.work.id)
                out.append(cand)
        return out

    @staticmethod
    def _keyword(uq: Understanding) -> str:
        for value in [*uq.titles, *uq.themes]:
            if value and strip_punct(value):
                return value
        return uq.raw.strip()

    def _to_candidate(self, row: dict, uq: Understanding) -> Candidate | None:
        if not isinstance(row, dict):
            return None
        title = str(row.get("title") or row.get("name") or "").strip()
        if not title:
            return None
        raw_id = str(row.get("id") or row.get("drama_id") or "").strip()
        work_id = f"dracin:{raw_id}" if raw_id else f"dracin:{strip_punct(title)[:24]}"
        episodes = _as_int(row.get("episodes") or row.get("episode_count"))
        cover = str(row.get("cover") or row.get("poster") or row.get("thumbnail") or "")
        summary = str(row.get("desc") or row.get("description") or row.get("synopsis") or "")
        themes = _as_str_list(row.get("genre") or row.get("genres") or row.get("tags"))
        tags = [f"共{episodes}集"] if episodes else []

        norm_query = strip_punct(uq.raw)
        norm_title = strip_punct(title)
        if norm_query and norm_title == norm_query:
            channel, score = "name_exact", 0.98
        elif norm_query and norm_query in norm_title:
            channel, score = "name_fuzzy", 0.84
        elif norm_query and norm_title in norm_query:
            channel, score = "name_fuzzy", 0.80
        else:
            channel, score = "name_fuzzy", 0.6

        work = Work(
            id=work_id, type="short_drama", title=title, author="DracinAPI",
            summary=summary or "DracinAPI 聚合短剧（仅 L1 元数据）。",
            themes=themes, tags=tags, cover=cover, episodes=episodes, popularity=0.5,
            sources=[SourceRef(platform=self.platform_id, url="", tier=self.tier,
                               content_level=self.content_level)],
        )
        evidence = Evidence(kind="title", text=title, position=f"DracinAPI/{self.provider}",
                            matched_query=uq.raw, score=score, verified=False)
        return Candidate(work=work, channel=channel, score=score,
                         evidence=[evidence], platforms={self.platform_id})


def _as_int(value) -> int | None:
    try:
        number = int(value)
        return number if number > 0 else None
    except (TypeError, ValueError):
        return None


def _as_str_list(value) -> list[str]:
    if not value:
        return []
    if isinstance(value, str):
        return [value]
    if isinstance(value, list):
        out = []
        for item in value:
            if isinstance(item, str):
                out.append(item)
            elif isinstance(item, dict) and item.get("name"):
                out.append(str(item["name"]))
        return out
    return []
