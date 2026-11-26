"""Query 理解 Agent：意图识别、实体抽取、查询改写（§5）。

纯规则实现，可解释、可测试；接 LLM 时只替换 `understand()` 的输出，
下游通道与排序完全不变。
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

from .corpus import Corpus
from .text import extract_quoted, normalize, pinyin_initials, strip_punct

# 句子意图的特征词（中文里很常见、几乎不会出现在"主题词"里的虚词）
_SENTENCE_HINTS = (
    "的", "了", "是", "我", "你", "他", "她", "它", "们", "不", "就", "也",
    "还", "又", "把", "被", "让", "给", "在", "说", "想", "要", "会", "能",
    "吗", "呢", "吧", "啊", "呀", "着", "过", "很", "都", "只", "才",
)

# 常见错别字 / 同音字纠正（可扩展，也可由 LLM 生成候选）
_TYPO_MAP = {
    "总栽": "总裁",
    "闪混": "闪婚",
    "替生": "替身",
    "重声": "重生",
    "副少": "傅少",
    "候府": "侯府",
    "候爷": "侯爷",
    "末曰": "末日",
    "囤物": "囤物",
    "团崇": "团宠",
    "马甲线": "马甲",
}

# 口语 / 同义表达 → 主题词
_SYNONYM_MAP = {
    "回到过去": "重生",
    "重活一世": "重生",
    "先婚后爱": "闪婚",
    "契约婚姻": "闪婚",
    "假结婚": "闪婚",
    "打脸": "爽文",
    "扮猪吃虎": "马甲",
    "追妻": "追妻火葬场",
    "虐心": "虐恋",
    "都市言情": "都市",
    "古装言情": "古装",
    "末世求生": "末世",
    "种田文": "种田",
    "悬疑推理": "悬疑",
}

_TYPE_HINTS_NOVEL = ("小说", "原著", "章节", "第几章", "书")
_TYPE_HINTS_DRAMA = ("短剧", "剧", "台词", "演员", "剧情", "第几集", "集")


@dataclass
class Entity:
    kind: str          # sentence | title | character | theme
    value: str         # 原始片段
    canonical: str = ""  # 规范名（如主题"重生"）
    types: list[str] = field(default_factory=list)  # novel | short_drama | both

    def to_dict(self) -> dict:
        return {
            "kind": self.kind,
            "value": self.value,
            "canonical": self.canonical or self.value,
            "types": self.types or ["novel", "short_drama"],
        }


@dataclass
class Understanding:
    raw: str
    normalized: str
    intent: str = "unknown"          # sentence | title | character | theme | mixed | unknown
    types: list[str] = field(default_factory=lambda: ["novel", "short_drama"])
    entities: list[Entity] = field(default_factory=list)
    quoted_sentences: list[str] = field(default_factory=list)
    quoted_titles: list[str] = field(default_factory=list)
    rewrites: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    @property
    def sentence_queries(self) -> list[str]:
        return list(dict.fromkeys([e.value for e in self.entities if e.kind == "sentence"] + self.quoted_sentences))

    @property
    def titles(self) -> list[str]:
        return list(dict.fromkeys([e.value for e in self.entities if e.kind == "title"] + self.quoted_titles))

    @property
    def characters(self) -> list[str]:
        return [e.value for e in self.entities if e.kind == "character"]

    @property
    def themes(self) -> list[str]:
        return [e.canonical or e.value for e in self.entities if e.kind == "theme"]

    def to_dict(self) -> dict:
        return {
            "intent": self.intent,
            "types": self.types,
            "entities": [e.to_dict() for e in self.entities],
            "sentence_queries": self.sentence_queries,
            "titles": self.titles,
            "characters": self.characters,
            "themes": self.themes,
            "rewrites": self.rewrites,
            "notes": self.notes,
        }


def _scan_dictionary(query: str, dictionary: dict[str, list[tuple[str, str]]]) -> list[tuple[int, int, str, str, str]]:
    """最长优先、互不重叠的实体扫描，返回 (start, end, kind, surface, canonical)。"""
    keys = sorted(dictionary, key=len, reverse=True)
    occupied = [False] * len(query)
    found: list[tuple[int, int, str, str, str]] = []
    for key in keys:
        if len(key) < 2:
            continue
        start = 0
        while True:
            idx = query.find(key, start)
            if idx < 0:
                break
            end = idx + len(key)
            if not any(occupied[idx:end]):
                for i in range(idx, end):
                    occupied[i] = True
                for kind, canonical in dictionary[key]:
                    found.append((idx, end, kind, key, canonical))
            start = idx + 1
    return found


def _looks_like_sentence(text: str) -> bool:
    plain = strip_punct(text)
    if len(plain) < 8:
        return False
    if re.search(r"[。！？!?…，,]", text):
        return True
    hints = sum(1 for h in _SENTENCE_HINTS if h in plain)
    return hints >= 2


def _types_for_kind(kind: str) -> list[str]:
    if kind == "novel_title":
        return ["novel"]
    if kind == "drama_title":
        return ["short_drama"]
    return ["novel", "short_drama"]


def understand(raw: str, corpus: Corpus, tab: str = "all") -> Understanding:
    raw = raw or ""
    tab = {"drama": "short_drama", "all": "all"}.get(tab, tab)
    norm = normalize(raw)
    uq = Understanding(raw=raw, normalized=norm)
    dictionary = corpus.entity_names()

    # 1) 引号内容：句子 / 标题最高优先，直接成为实体
    for kind, value in extract_quoted(raw):
        if kind == "title":
            uq.quoted_titles.append(value)
        else:
            uq.quoted_sentences.append(value)

    # 2) 词典扫描（作品名 / 别名 / 角色 / 主题）
    plain_query = strip_punct(raw)
    hits = _scan_dictionary(plain_query, dictionary)
    for start, end, kind, surface, canonical in hits:
        if kind in ("novel_title", "drama_title"):
            uq.entities.append(Entity(kind="title", value=surface, canonical=canonical, types=_types_for_kind(kind)))
        elif kind == "character":
            uq.entities.append(Entity(kind="character", value=surface, canonical=canonical))
        elif kind == "theme":
            uq.entities.append(Entity(kind="theme", value=surface, canonical=canonical))

    # 3) 引号句子作为实体
    for value in uq.quoted_sentences:
        uq.entities.append(Entity(kind="sentence", value=value))

    # 4) 像句子 → 整句同时作为"原文句子"查询（即使已命中主题/角色，多路召回更稳）
    if _looks_like_sentence(raw) and not uq.quoted_sentences:
        candidate = raw.strip().strip('"“”「」\'')
        if strip_punct(candidate):
            uq.entities.append(Entity(kind="sentence", value=candidate, canonical=candidate))
            uq.notes.append("整体识别为原文句子")

    # 5) 同义 / 主题补充：查询里出现同义表达时追加主题实体
    for phrase, theme in _SYNONYM_MAP.items():
        if phrase in plain_query and theme not in [e.canonical for e in uq.entities if e.kind == "theme"]:
            if theme in corpus.theme_index:
                uq.entities.append(Entity(kind="theme", value=phrase, canonical=theme))
                uq.notes.append(f"同义扩展：{phrase} → {theme}")

    # 6) 纯拼音查询（如 cyj / changye）→ 记录，由通道做拼音匹配
    if re.fullmatch(r"[a-z]{2,24}", norm):
        uq.notes.append("拉丁/拼音查询")
        for work in corpus.works:
            if not work.pinyin:
                continue
            plain_py = strip_punct(work.pinyin).replace(" ", "")
            initials = pinyin_initials(work.pinyin)
            # 全拼/首字母完全一致，或 ≥3 位前缀（如 cyj → cyjm）
            hit = norm in (plain_py, initials) or (len(norm) >= 3 and
                                                   (initials.startswith(norm) or plain_py.startswith(norm)))
            if hit:
                uq.entities.append(
                    Entity(kind="title", value=raw, canonical=work.title,
                           types=["novel"] if work.type == "novel" else ["short_drama"])
                )
        if uq.entities:
            uq.notes.append("按拼音/首字母扩展")

    # 7) 意图判定：实体种类计数
    kinds = {e.kind for e in uq.entities}
    if len(kinds) > 1:
        uq.intent = "mixed"
    elif kinds == {"sentence"}:
        uq.intent = "sentence"
    elif kinds == {"title"}:
        uq.intent = "title"
    elif kinds == {"character"}:
        uq.intent = "character"
    elif kinds == {"theme"}:
        uq.intent = "theme"
    else:
        uq.intent = "unknown"

    # 8) 类型路由
    explicit = [t for t in (tab or "all").split(",") if t in ("novel", "short_drama")]
    inferred: set[str] = set()
    for e in uq.entities:
        if e.kind in ("title", "sentence") and e.types:
            inferred.update(e.types)
    lower = raw.lower()
    if any(h in lower for h in _TYPE_HINTS_DRAMA):
        inferred.add("short_drama")
    if any(h in lower for h in _TYPE_HINTS_NOVEL):
        inferred.add("novel")
    if tab in ("novel", "short_drama"):
        uq.types = [tab]
    elif inferred:
        uq.types = [t for t in ("novel", "short_drama") if t in inferred]
    else:
        uq.types = ["novel", "short_drama"]

    # 9) 查询改写：归一化、去标点、错别字、拼音
    variants: list[str] = [raw.strip()]
    for value in uq.sentence_queries + uq.titles:
        variants.append(value)
    fixed = raw
    for wrong, right in _TYPO_MAP.items():
        if wrong in fixed:
            fixed = fixed.replace(wrong, right)
            uq.notes.append(f"纠错：{wrong} → {right}")
    for phrase, theme in _SYNONYM_MAP.items():
        if phrase in fixed and theme not in fixed:
            fixed = fixed.replace(phrase, theme)
    for text in (fixed, strip_punct(fixed), norm):
        if text and text not in variants:
            variants.append(text)
    if pinyin_initials(norm) and norm.isascii():
        variants.append(pinyin_initials(norm))
    uq.rewrites = list(dict.fromkeys(v for v in variants if v))

    if uq.intent == "unknown":
        uq.notes.append("未识别到明确实体，按模糊标题/摘要检索")
    return uq
