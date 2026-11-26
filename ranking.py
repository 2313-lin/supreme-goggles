"""融合排序：类型内分层置顶 + 个性化（同层内）+ 结果卡片（§1.3 / §6）。

铁律：
1. 小说"原文句子可信命中"永远在第 0 层；
2. 短剧"剧名/主题强命中"压过台词；只有完全没有剧名/主题强命中时，
   台词精确命中才提升到第 0 层；
3. 个性化 boost 只作用于同层内部，绝不跨层。
"""
from __future__ import annotations

from typing import Iterable

from .channels import layer_of
from .models import Candidate, Work

NOVEL_LABELS = {
    0: "原文句子命中",
    1: "原文句子近似",
    2: "主角命中",
    3: "主题命中",
    4: "标题命中",
    5: "简介近似",
}
DRAMA_LABELS = {
    0: "剧名强命中",
    1: "剧名近似",
    2: "主题强命中",
    3: "主题弱命中",
    4: "台词精确命中",
    5: "台词近似",
    6: "角色/演员命中",
}

# 通道 → 展示标签（比"层级名"更准确，尤其是被置顶提升过的标题/台词）
CHANNEL_LABELS = {
    "sentence_exact": "原文句子命中",
    "sentence_normalized": "原文句子命中",
    "sentence_fuzzy": "原文句子近似",
    "character": "主角/角色命中",
    "theme": "主题命中",
    "theme_weak": "主题弱命中",
    "title_exact": "标题精确命中",
    "title_fuzzy": "标题近似",
    "summary_fuzzy": "简介近似",
    "name_exact": "剧名精确命中",
    "name_alias": "剧名别名命中",
    "name_pinyin": "剧名拼音命中",
    "name_fuzzy": "剧名近似",
    "line_exact": "台词精确命中",
    "line_fuzzy": "台词近似",
    "cast": "角色/演员命中",
}


def _labels(kind: str) -> dict[int, str]:
    return NOVEL_LABELS if kind == "novel" else DRAMA_LABELS


def _confidence(layer: int, score: float) -> float:
    base = max(0.3, 0.95 - 0.11 * layer)
    return round(min(0.99, base * 0.72 + score * 0.28), 2)


def _confidence_label(value: float) -> str:
    if value >= 0.85:
        return "高"
    if value >= 0.65:
        return "中"
    return "低"


def _merge(candidates: Iterable[Candidate]) -> list[dict]:
    """同一作品多条通道命中 → 合并成一张卡（保留全部证据）。"""
    merged: dict[str, dict] = {}
    for cand in candidates:
        work = cand.work
        entry = merged.get(work.id)
        if entry is None:
            entry = {
                "work": work,
                "layer": layer_of(cand.channel, work.type),
                "channel": cand.channel,
                "score": cand.score,
                "evidence": [],
                "channels": [],
                "platforms": set(),
            }
            merged[work.id] = entry
        candidate_layer = layer_of(cand.channel, work.type)
        if candidate_layer < entry["layer"] or (
            candidate_layer == entry["layer"] and cand.score > entry["score"]
        ):
            entry["layer"] = candidate_layer
            entry["channel"] = cand.channel
        entry["score"] = max(entry["score"], cand.score)
        entry["platforms"] |= cand.platforms
        entry["channels"].append(cand.channel)
        seen = {(e.kind, e.text) for e in entry["evidence"]}
        for ev in cand.evidence:
            if (ev.kind, ev.text) not in seen:
                entry["evidence"].append(ev)
                seen.add((ev.kind, ev.text))
    return list(merged.values())


def _promote(entries: list[dict], kind: str) -> None:
    """置顶提升规则。"""
    if not entries:
        return
    if kind == "novel":
        # 没有任何句子/角色/主题命中时，完整标题命中提到第 0 层
        strong = [e for e in entries if e["layer"] <= 3]
        if not strong:
            for e in entries:
                if e["channel"] == "title_exact" and e["score"] >= 0.95:
                    e["layer"] = 0
    else:
        strong = [e for e in entries if e["layer"] <= 3]
        if not strong:
            for e in entries:
                if e["channel"] == "line_exact":
                    e["layer"] = 0


def personalization_boost(work: Work, profile: dict | None) -> float:
    if not profile:
        return 0.0
    boost = 0.0
    themes = set(work.themes) | set(work.tags)
    for theme in profile.get("preferred_themes", [])[:5]:
        if theme in themes:
            boost += 0.012
    for author in profile.get("preferred_authors", [])[:5]:
        if author and author == work.author:
            boost += 0.015
    names = {c.name for c in work.characters}
    for name in profile.get("preferred_characters", [])[:5]:
        if name in names:
            boost += 0.008
    clicked = profile.get("clicked_works", {})
    if work.id in clicked:
        boost += 0.01 * min(3, clicked[work.id])
    preferred_types = profile.get("preferred_types", [])
    if preferred_types and work.type in preferred_types:
        boost += 0.004
    return min(0.05, boost)


def rank(candidates: list[Candidate], kind: str, profile: dict | None,
         platform_priority: dict[str, int], limit: int = 10) -> list[dict]:
    entries = _merge([c for c in candidates if c.work.type == kind])
    _promote(entries, kind)
    labels = _labels(kind)

    for entry in entries:
        work: Work = entry["work"]
        entry["score"] = round(min(1.0, entry["score"] + personalization_boost(work, profile)), 4)
        priority = min(
            [platform_priority.get(p, 999) for p in entry["platforms"]] or [999]
        )
        entry["_sort"] = (entry["layer"], -entry["score"], priority, -work.popularity, work.title)

    entries.sort(key=lambda e: e["_sort"])
    results = []
    for entry in entries[:limit]:
        work: Work = entry["work"]
        evidence = sorted(entry["evidence"], key=lambda e: (not e.verified, -e.score))[:4]
        verified = any(e.verified for e in evidence) or any(
            e.kind in ("title", "character") and e.verified for e in evidence
        )
        primary = min(entry["platforms"], key=lambda p: platform_priority.get(p, 999)) if entry["platforms"] else ""
        also_on = sorted((p for p in work.platform_ids if p != primary),
                         key=lambda p: platform_priority.get(p, 999))
        confidence = _confidence(entry["layer"], entry["score"])
        results.append({
            "work": work.to_card(),
            "layer": entry["layer"],
            "layer_label": CHANNEL_LABELS.get(entry["channel"], labels.get(entry["layer"], "其他")),
            "primary_channel": entry["channel"],
            "matched_channels": sorted(set(entry["channels"])),
            "score": round(entry["score"], 3),
            "confidence": confidence,
            "confidence_label": _confidence_label(confidence),
            "evidence": [e.to_dict() for e in evidence],
            "verified": bool(verified),
            "platform_primary": primary,
            "also_on": also_on,
            "source_tier": next((s.tier for s in work.sources if s.platform == primary), ""),
        })
    return results


def group_counts(candidates: list[Candidate]) -> dict[str, int]:
    return {
        "novel": len({c.work.id for c in candidates if c.work.type == "novel"}),
        "short_drama": len({c.work.id for c in candidates if c.work.type == "short_drama"}),
    }
