"""中文文本工具：归一化、句子切分、二元分词、BM25、相似度。

不依赖第三方库（无 jieba / numpy），足够支撑演示规模的召回与排序；
生产环境可替换为 jieba + 向量库，接口保持不变。
"""
from __future__ import annotations

import math
import re
import unicodedata
from difflib import SequenceMatcher
from typing import Iterable, Sequence

# 常见全角/异体引号归一，便于"原文句子"精确匹配
_QUOTE_MAP = {
    "“": '"', "”": '"', "„": '"', "‟": '"',
    "‘": "'", "’": "'", "‚": "'", "‛": "'",
    "「": '"', "」": '"', "『": '"', "』": '"',
    "《": "<", "》": ">", "〈": "<", "〉": ">",
    "—": "-", "–": "-", "―": "-", "…": "...",
}

_CJK = r"\u4e00-\u9fff\u3400-\u4dbf"

# 传统 → 简体（只收录演示语料里出现的高频字，避免引入大字典）
_TRAD_TO_SIMP = str.maketrans({
    "愛": "爱", "們": "们", "這": "这", "個": "个", "時": "时", "後": "后",
    "為": "为", "對": "对", "過": "过", "還": "还", "麼": "么",
    "樣": "样", "讓": "让", "見": "见", "覺": "觉", "開": "开", "關": "关",
    "東": "东", "紅": "红", "綠": "绿", "陰": "阴", "陽": "阳", "寶": "宝",
    "錢": "钱", "聲": "声", "聽": "听", "淚": "泪", "離": "离", "歸": "归",
    "夢": "梦", "憶": "忆", "說": "说", "記": "记", "誰": "谁", "無": "无", "與": "与",
    "妳": "你", "臺": "台", "葉": "叶", "陳": "陈", "張": "张", "劉": "刘",
})


def nfkc(text: str) -> str:
    return unicodedata.normalize("NFKC", text)


def normalize(text: str) -> str:
    """用于"原文句子"比对：全角转半角、引号统一、去空白、小写、繁转简。"""
    text = nfkc(text or "").translate(_TRAD_TO_SIMP)
    text = "".join(_QUOTE_MAP.get(ch, ch) for ch in text)
    text = text.lower()
    text = re.sub(r"\s+", "", text)
    return text


def strip_punct(text: str) -> str:
    """只保留汉字、字母、数字，用于相似度与分词，避免标点噪声。"""
    text = normalize(text)
    return re.sub(rf"[^0-9a-z{_CJK}]+", "", text)


def has_cjk(text: str) -> bool:
    return bool(re.search(rf"[{_CJK}]", text or ""))


_SENT_SPLIT = re.compile(rf"(?<=[。！？!?；;…])|(?<=[.](?=\s|$))|\n+")


def split_sentences(text: str) -> list[str]:
    parts = [p.strip() for p in _SENT_SPLIT.split(text or "") if p and p.strip()]
    return parts or ([text.strip()] if text and text.strip() else [])


def bigrams(text: str) -> list[str]:
    """汉字二元切分；单字保留，拉丁词整体保留。"""
    cleaned = strip_punct(text)
    tokens: list[str] = []
    latin = re.findall(r"[0-9a-z]+", cleaned)
    for word in latin:
        tokens.append(word)
    cjk = re.findall(rf"[{_CJK}]+", cleaned)
    for run in cjk:
        if len(run) == 1:
            tokens.append(run)
        else:
            tokens.extend(run[i : i + 2] for i in range(len(run) - 1))
    return tokens


def similarity(a: str, b: str) -> float:
    """0~1 相似度：短文本用编辑距离比率，长文本用查询片段覆盖率。"""
    a, b = strip_punct(a), strip_punct(b)
    if not a or not b:
        return 0.0
    if a == b:
        return 1.0
    if len(a) > len(b):
        a, b = b, a
    if len(a) >= 4 and a in b:
        return 0.86 + 0.14 * (len(a) / len(b))
    ratio = SequenceMatcher(None, a, b, autojunk=False).ratio()
    return ratio


def containment(query: str, text: str) -> float:
    """查询二元组在文本中的覆盖率，衡量"片段来自原文"的可能性。"""
    q = bigrams(query)
    if not q:
        return 0.0
    t = set(bigrams(text))
    if not t:
        return 0.0
    hit = sum(1 for tok in q if tok in t)
    return hit / len(q)


def best_match_span(query: str, text: str, width: int = 40) -> tuple[float, str]:
    """在长文本里找与查询最相似的片段，返回 (分数, 片段)。"""
    query = normalize(query)
    if not query:
        return 0.0, ""
    plain = strip_punct(text)
    plain_query = strip_punct(query)
    if not plain or not plain_query:
        return 0.0, text[:width]
    if plain_query in plain:
        pos = plain.index(plain_query)
        return 1.0, plain[max(0, pos - 8) : pos + len(plain_query) + 8]
    if len(plain) <= width:
        return similarity(query, text), text
    best_score, best_span = 0.0, plain[:width]
    step = max(1, width // 4)
    for start in range(0, max(1, len(plain) - width + 1), step):
        span = plain[start : start + width]
        score = similarity(query, span)
        if score > best_score:
            best_score, best_span = score, span
    return best_score, best_span


def tokenize_terms(text: str) -> list[str]:
    return bigrams(text)


class BM25Index:
    """极简 BM25，按字符串 key 索引。文档规模 < 10^4 时完全够用。"""

    def __init__(self, docs: Sequence[tuple[str, Sequence[str]]], k1: float = 1.5, b: float = 0.75):
        self.k1 = k1
        self.b = b
        self.doc_ids: list[str] = []
        self.doc_len: list[int] = []
        self.postings: dict[str, dict[int, int]] = {}
        total = 0
        for doc_id, tokens in docs:
            idx = len(self.doc_ids)
            self.doc_ids.append(doc_id)
            self.doc_len.append(len(tokens))
            total += len(tokens)
            tf: dict[str, int] = {}
            for tok in tokens:
                tf[tok] = tf.get(tok, 0) + 1
            for tok, n in tf.items():
                self.postings.setdefault(tok, {})[idx] = n
        self.n_docs = len(self.doc_ids)
        self.avgdl = (total / self.n_docs) if self.n_docs else 0.0

    def scores(self, query_tokens: Iterable[str]) -> dict[str, float]:
        q = [t for t in dict.fromkeys(query_tokens) if t in self.postings]
        if not q or not self.n_docs:
            return {}
        out: dict[str, float] = {}
        for tok in q:
            posting = self.postings[tok]
            df = len(posting)
            idf = math.log(1 + (self.n_docs - df + 0.5) / (df + 0.5))
            for idx, tf in posting.items():
                dl = self.doc_len[idx] or 1
                denom = tf + self.k1 * (1 - self.b + self.b * dl / (self.avgdl or 1))
                out[self.doc_ids[idx]] = out.get(self.doc_ids[idx], 0.0) + idf * (tf * (self.k1 + 1)) / denom
        return out


def normalize_scores(scores: dict[str, float]) -> dict[str, float]:
    if not scores:
        return {}
    top = max(scores.values())
    if top <= 0:
        return {k: 0.0 for k in scores}
    return {k: v / top for k, v in scores.items()}


def pinyin_initials(pinyin: str) -> str:
    """把 'jiang yu bai' / 'jiangyubai' 转成 'jyb'。"""
    return "".join(part[0] for part in re.split(r"[\s,;/\-]+", (pinyin or "").strip().lower()) if part)


def extract_quoted(text: str) -> list[tuple[str, str]]:
    """抽取引号内容，返回 (引号类型, 内容)。支持 "" '' 「」 『』 《》 。"""
    out: list[tuple[str, str]] = []
    for pattern, kind in (
        (r"《([^》]{1,60})》", "title"),
        (r"「([^」]{1,120})」", "sentence"),
        (r"『([^』]{1,120})』", "sentence"),
        (r'"([^"]{1,120})"', "sentence"),
        (r"“([^”]{1,120})”", "sentence"),
        (r"‘([^’]{1,120})’", "sentence"),
        (r"'([^']{1,120})'", "sentence"),
    ):
        for m in re.finditer(pattern, text):
            value = m.group(1).strip()
            if value:
                out.append((kind, value))
    return out


def highlight(text: str, query: str, radius: int = 18) -> str:
    """返回带 ... 的上下文片段（前端负责高亮）。"""
    plain_q = strip_punct(query)
    if not plain_q or not text:
        return (text or "")[: radius * 2]
    idx = text.find(query)
    if idx < 0:
        # 退化为去标点匹配的位置估算
        plain = strip_punct(text)
        pos = plain.find(plain_q)
        idx = pos if pos >= 0 else 0
    start = max(0, idx - radius)
    end = min(len(text), idx + len(query) + radius)
    prefix = "…" if start > 0 else ""
    suffix = "…" if end < len(text) else ""
    return f"{prefix}{text[start:end]}{suffix}"
