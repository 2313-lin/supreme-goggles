"""真实聚合源适配器：小齐 API「短剧搜索与排名榜」。

接口：GET https://api.icofun.cn/API/djpxb.php?msg=<关键词>&num=<条数>
公开访问、无需密钥（文档标注"公开访问（无需密钥）"，有 429 限流）。

返回（UTF-8 JSON，键名中文）：
    {"状态":"成功","提示信息":"…","总数量":50,"当前返回数量":5,"数量限制":5,
     "数据列表":[{"选择序号":1,"ID":12333,"短剧名":"闪婚 急诊",
                 "封面图链接":"https://…","总集数":103}]}

职责边界（与设计文档一致）：
- 只取 L1 元数据（剧名、ID、封面、集数），不请求 `n` 参数（视频链接），
  不存储/分发内容，不提供播放地址；
- 只负责"召回候选"，分层、排序、个性化仍由本服务统一决定。
"""
from __future__ import annotations

import json
import urllib.parse
import urllib.request

from .adapters import AdapterError
from .models import Candidate, Evidence, SourceRef, Work
from .text import strip_punct
from .understanding import Understanding

_STATUS_OK = {"成功", "success", "ok"}


class IcofunShortDramaAdapter:
    name = "icofun_shortdrama"

    def __init__(self, platform_id: str = "agg_drama", base_url: str = "https://api.icofun.cn",
                 timeout_ms: int = 4000, tier: str = "aggregator", content_level: str = "L1",
                 num: int = 5, api_key: str = "", api_key_header: str = "",
                 headers: dict[str, str] | None = None):
        self.platform_id = platform_id
        self.base_url = (base_url or "").rstrip("/")
        self.timeout_s = max(0.2, timeout_ms / 1000)
        self.tier = tier
        self.content_level = content_level
        self.num = max(1, min(20, num))
        self.api_key = api_key or ""
        self.api_key_header = api_key_header or ""
        self.headers = dict(headers or {})

    def _headers(self) -> dict[str, str]:
        headers = {"Accept": "application/json", "User-Agent": "nds-agent/1.0 (+https://github.com/)"}
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

    # ------------------------------------------------------------------ HTTP
    def _get(self, params: dict) -> dict:
        if not self.base_url:
            raise AdapterError("adapter_not_configured: 未配置 base_url")
        url = f"{self.base_url}/API/djpxb.php?{urllib.parse.urlencode(self._params(params))}"
        req = urllib.request.Request(url, headers=self._headers())
        try:
            with urllib.request.urlopen(req, timeout=self.timeout_s) as resp:
                raw = resp.read()
        except Exception as exc:  # 429 / 5xx / 超时 / DNS 统一归一
            message = str(exc)
            if self.api_key:
                message = message.replace(self.api_key, "***")
            raise AdapterError(f"http_error: {type(exc).__name__}: {message}") from exc
        try:
            data = json.loads(raw.decode("utf-8", "replace"))
        except json.JSONDecodeError as exc:
            raise AdapterError("bad_response: 返回不是合法 JSON") from exc
        if not isinstance(data, dict):
            raise AdapterError("bad_response: 顶层不是对象")
        status = str(data.get("状态") or "")
        if status and status not in _STATUS_OK:
            raise AdapterError(f"api_error: {status} {data.get('提示信息', '')}".strip())
        return data

    def health(self) -> bool:
        try:
            data = self._get({"msg": "闪婚", "num": "1"})
            return isinstance(data.get("数据列表"), list)
        except AdapterError:
            return False

    def detail(self, work_id: str) -> dict | None:
        return None

    # ---------------------------------------------------------------- search
    def search(self, uq: Understanding, kind: str) -> list[Candidate]:
        if kind != "short_drama":
            return []
        # L1 元数据只能按"剧名关键词"检索：句子/台词查不到，交给有字幕的源
        if uq.intent == "sentence":
            return []
        keyword = self._keyword(uq)
        if not keyword:
            return []
        data = self._get({"msg": keyword, "num": str(self.num)})
        rows = data.get("数据列表")
        if not isinstance(rows, list):
            return []
        out: list[Candidate] = []
        seen: set[str] = set()
        for row in rows:
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
        title = str(row.get("短剧名") or "").strip()
        if not title:
            return None
        raw_id = str(row.get("ID") or "").strip()
        work_id = f"icofun:{raw_id}" if raw_id else f"icofun:{strip_punct(title)[:24]}"
        episodes = None
        try:
            value = int(row.get("总集数") or 0)
            episodes = value if value > 0 else None
        except (TypeError, ValueError):
            episodes = None
        cover = str(row.get("封面图链接") or "")
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
            channel, score = "name_fuzzy", 0.58

        work = Work(
            id=work_id,
            type="short_drama",
            title=title,
            author="聚合平台",
            summary=f"聚合接口返回的短剧元数据（共 {episodes} 集）。" if episodes else "聚合接口返回的短剧元数据。",
            themes=[],
            tags=tags,
            cover=cover,
            episodes=episodes,
            popularity=0.5,
            sources=[SourceRef(platform=self.platform_id, url="", tier=self.tier,
                               content_level=self.content_level)],
        )
        evidence = Evidence(kind="title", text=title, position="聚合接口（仅元数据）",
                            matched_query=uq.raw, score=score, verified=False)
        return Candidate(work=work, channel=channel, score=score,
                         evidence=[evidence], platforms={self.platform_id})
