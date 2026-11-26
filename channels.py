"""多路召回通道（§2 "多路召回" / §6 检索策略）。

每个通道只做一件事，输出 Candidate（含证据与原始分）；
分层与置顶规则放在 ranking.py，方便独立测试"优先级不被破坏"。
"""
from __future__ import annotations

from .corpus import Corpus
from .models import Candidate, Evidence, Work
from .text import (
    containment,
    normalize,
    pinyin_initials,
    similarity,
    strip_punct,
)
from .understanding import Understanding

# 通道 → 分层（数字越小越靠前）
NOVEL_LAYERS = {
    "sentence_exact": 0,
    "sentence_normalized": 0,
    "sentence_fuzzy": 1,
    "character": 2,
    "theme": 3,
    "title_exact": 4,
    "title_fuzzy": 4,
    "summary_fuzzy": 5,
}

DRAMA_LAYERS = {
    "name_exact": 0,
    "name_alias": 0,
    "name_pinyin": 0,
    "name_fuzzy": 1,
    "theme": 2,
    "theme_weak": 3,
    "line_exact": 4,     # 无名称/主题强命中时被提升为 0
    "line_fuzzy": 5,
    "cast": 6,
    "summary_fuzzy": 6,
}

NOVEL_CHANNELS = tuple(NOVEL_LAYERS)
DRAMA_CHANNELS = tuple(DRAMA_LAYERS)


def layer_of(channel: str, kind: str) -> int:
    table = NOVEL_LAYERS if kind == "novel" else DRAMA_LAYERS
    return table.get(channel, 9)


def _on_platform(work: Work, platform: str | None) -> bool:
    return platform is None or work.source_for(platform) is not None


def _add(bucket: dict[str, Candidate], work: Work, channel: str, score: float, evidence: Evidence,
         platform: str | None) -> None:
    key = f"{work.id}|{channel}"
    ev = evidence
    ev.channel = channel
    if key in bucket:
        existing = bucket[key]
        existing.score = max(existing.score, score)
        existing.evidence.append(ev)
        if platform:
            existing.platforms.add(platform)
        return
    bucket[key] = Candidate(
        work=work,
        channel=channel,
        score=score,
        evidence=[ev],
        platforms={platform} if platform else {s.platform for s in work.sources},
    )


# --------------------------------------------------------------------- novels
def _novel_sentence_channels(corpus: Corpus, uq: Understanding, platform: str | None,
                             bucket: dict[str, Candidate]) -> None:
    queries = [q for q in uq.sentence_queries if strip_punct(q)]
    if not queries:
        return
    for work, sentence in corpus.sentences:
        if work.type != "novel" or not _on_platform(work, platform):
            continue
        plain_sent = strip_punct(sentence.text)
        for q in queries:
            plain_q = strip_punct(q)
            if not plain_q:
                continue
            # 去标点后完全一致：用户少打/多打标点不影响"可信命中"
            if plain_q == plain_sent:
                _add(bucket, work, "sentence_exact", 1.0,
                     Evidence(kind="sentence", text=sentence.text, position=sentence.position,
                              matched_query=q, score=1.0, verified=True), platform)
                continue
            # 引号/片段来自原文：连续 ≥10 字，或覆盖原句 ≥60%
            fragment_ratio = len(plain_q) / max(1, len(plain_sent))
            if plain_q in plain_sent and (len(plain_q) >= 10 or fragment_ratio >= 0.6):
                score = 0.90 + 0.08 * fragment_ratio
                _add(bucket, work, "sentence_exact", score,
                     Evidence(kind="sentence", text=sentence.text, position=sentence.position,
                              matched_query=q, score=score, verified=True), platform)
                continue
            if len(plain_q) < 4:
                continue
            cover = containment(q, sentence.text)
            if cover < 0.55:
                continue
            score = 0.35 + 0.55 * (0.6 * cover + 0.4 * similarity(q, sentence.text))
            _add(bucket, work, "sentence_fuzzy", min(score, 0.92),
                 Evidence(kind="sentence", text=sentence.text, position=sentence.position,
                          matched_query=q, score=min(score, 0.92), verified=False), platform)


def _novel_entity_channels(corpus: Corpus, uq: Understanding, platform: str | None,
                           bucket: dict[str, Candidate]) -> None:
    # 角色：名字 / 别名 / 拼音
    for name in uq.characters:
        for work, ch in corpus.char_index.get(normalize(name), []):
            if work.type != "novel" or not _on_platform(work, platform):
                continue
            score = 0.65 + 0.3 * max(0.0, min(1.0, ch.importance_score))
            _add(bucket, work, "character", score,
                 Evidence(kind="character", text=f"{ch.name}（{ch.role}）：{ch.description}",
                          position="人物设定", matched_query=name, score=score, verified=True), platform)
    # 主题
    for theme in uq.themes:
        for work in corpus.novels:
            if not _on_platform(work, platform):
                continue
            matched = [t for t in work.themes if t == theme]
            if not matched:
                matched = [t for t in work.tags if t == theme]
            if not matched:
                continue
            score = 0.55 + 0.25 * work.popularity
            _add(bucket, work, "theme", score,
                 Evidence(kind="theme", text=f"主题：{' / '.join(work.themes)}",
                          position="作品标签", matched_query=theme, score=score, verified=True), platform)
    # 标题 / 别名 / 拼音
    spelling = uq.rewrites
    title_queries = list(uq.titles)
    if not title_queries and len(strip_punct(uq.raw)) <= 16:
        title_queries.append(uq.raw)
    for name in title_queries:
        _novel_title_hits(corpus, name, platform, bucket)
    for variant in spelling[1:]:
        norm_v = normalize(variant)
        if norm_v and norm_v in corpus.pinyin_index:
            for work in corpus.pinyin_index[norm_v]:
                if work.type == "novel" and _on_platform(work, platform):
                    _add(bucket, work, "title_exact", 0.95,
                         Evidence(kind="title", text=work.title, position="拼音匹配",
                                  matched_query=variant, score=0.95, verified=True), platform)
    # 整串查询与完整标题一致 → 直接置顶
    raw_norm = normalize(uq.raw)
    for work in corpus.novels:
        if not _on_platform(work, platform):
            continue
        if raw_norm and raw_norm in {normalize(work.title), *[normalize(a) for a in work.aliases]}:
            _add(bucket, work, "title_exact", 1.0,
                 Evidence(kind="title", text=work.title, position="完整标题命中",
                          matched_query=uq.raw, score=1.0, verified=True), platform)


def _novel_title_hits(corpus: Corpus, query: str, platform: str | None,
                      bucket: dict[str, Candidate]) -> None:
    norm_q = normalize(query)
    plain_q = strip_punct(query)
    if len(plain_q) < 2:
        return
    for work in corpus.novels:
        if not _on_platform(work, platform):
            continue
        titles = [work.title, *work.aliases]
        if norm_q in {normalize(t) for t in titles} or plain_q in {strip_punct(t) for t in titles}:
            _add(bucket, work, "title_exact", 1.0,
                 Evidence(kind="title", text=work.title, position="标题精确命中",
                          matched_query=query, score=1.0, verified=True), platform)
            continue
        # 拼音 / 首字母（支持前缀：cyj → 长夜将明）
        if work.pinyin and norm_q.isascii() and len(norm_q) >= 2:
            plain_py = strip_punct(work.pinyin).replace(" ", "")
            initials = pinyin_initials(work.pinyin)
            if norm_q in (plain_py, initials) or (len(norm_q) >= 3 and
                                                 (plain_py.startswith(norm_q) or initials.startswith(norm_q))):
                _add(bucket, work, "title_exact", 0.95,
                     Evidence(kind="title", text=work.title, position="拼音/首字母命中",
                              matched_query=query, score=0.95, verified=True), platform)
                continue
        best = 0.0
        for t in titles:
            nt = normalize(t)
            if norm_q and (norm_q in nt or nt in norm_q):
                best = max(best, 0.78)
            best = max(best, 0.55 * similarity(query, t))
        if best >= 0.5:
            _add(bucket, work, "title_fuzzy", min(0.95, best),
                 Evidence(kind="title", text=work.title, position="标题模糊命中",
                          matched_query=query, score=min(0.95, best), verified=False), platform)


def _novel_summary_channel(corpus: Corpus, uq: Understanding, platform: str | None,
                           bucket: dict[str, Candidate]) -> None:
    query = strip_punct(uq.raw)
    if len(query) < 2:
        return
    for work in corpus.novels:
        if not _on_platform(work, platform):
            continue
        haystack = " ".join([work.title, work.summary, " ".join(work.themes), " ".join(work.tags)])
        cover = containment(uq.raw, haystack)
        if cover < 0.4:
            continue
        score = 0.3 + 0.25 * cover
        _add(bucket, work, "summary_fuzzy", score,
             Evidence(kind="theme", text=work.summary[:80], position="简介匹配",
                      matched_query=uq.raw, score=score, verified=False), platform)


# --------------------------------------------------------------------- dramas
def _title_hits(corpus: Corpus, work_filter, query: str, bucket: dict[str, Candidate],
                platform: str | None) -> None:
    norm_q = normalize(query)
    plain_q = strip_punct(query)
    if len(plain_q) < 2:
        return
    for work in corpus.works:
        if not work_filter(work):
            continue
        titles = [work.title, *work.aliases]
        norm_titles = [normalize(t) for t in titles]
        if norm_q in norm_titles:
            _add(bucket, work, "name_exact", 1.0,
                 Evidence(kind="title", text=work.title, position="剧名精确命中",
                          matched_query=query, score=1.0, verified=True), platform)
            continue
        if any(plain_q == strip_punct(t) for t in titles):
            _add(bucket, work, "name_exact", 0.98,
                 Evidence(kind="title", text=work.title, position="剧名去标点命中",
                          matched_query=query, score=0.98, verified=True), platform)
            continue
        for t, nt in zip(titles, norm_titles):
            if t != work.title and (norm_q == nt or plain_q == strip_punct(t)):
                _add(bucket, work, "name_alias", 0.96,
                     Evidence(kind="title", text=f"{work.title}（别名：{t}）", position="别名命中",
                              matched_query=query, score=0.96, verified=True), platform)
                break
        if work.pinyin and norm_q.isascii():
            py, ini = strip_punct(work.pinyin).replace(" ", ""), pinyin_initials(work.pinyin)
            if norm_q in (py, ini) or (len(norm_q) >= 3 and
                                       (py.startswith(norm_q) or ini.startswith(norm_q))):
                _add(bucket, work, "name_pinyin", 0.92,
                     Evidence(kind="title", text=work.title, position="拼音/首字母命中",
                              matched_query=query, score=0.92, verified=True), platform)
                continue
        # 部分命中：剧名包含查询或查询包含剧名
        partial = 0.0
        for t in titles:
            nt = normalize(t)
            if norm_q and (norm_q in nt or nt in norm_q):
                partial = max(partial, 0.72 + 0.2 * (len(norm_q) / max(1, len(nt))))
            partial = max(partial, 0.5 * similarity(query, t))
        if partial >= 0.5:
            _add(bucket, work, "name_fuzzy", min(0.95, partial),
                 Evidence(kind="title", text=work.title, position="剧名模糊命中",
                          matched_query=query, score=min(0.95, partial), verified=False), platform)


def _drama_channels(corpus: Corpus, uq: Understanding, platform: str | None,
                    bucket: dict[str, Candidate]) -> None:
    drama_filter = lambda w: w.type == "short_drama" and _on_platform(w, platform)  # noqa: E731
    for name in uq.titles:
        _title_hits(corpus, drama_filter, name, bucket, platform)
    if not uq.titles and len(strip_punct(uq.raw)) <= 16:
        # 短查询可能是剧名的一部分（如"闪婚""傅先生"）
        _title_hits(corpus, drama_filter, uq.raw, bucket, platform)

    # 主题
    for theme in uq.themes:
        for work in corpus.dramas:
            if not _on_platform(work, platform):
                continue
            if theme in work.themes or theme in work.tags:
                score = 0.72 + 0.22 * work.popularity
                _add(bucket, work, "theme", score,
                     Evidence(kind="theme", text=f"主题：{' / '.join(work.themes)}",
                              position="短剧标签", matched_query=theme, score=score, verified=True), platform)
            else:
                # 弱主题：简介里出现主题词
                if theme and theme in work.summary:
                    score = 0.45
                    _add(bucket, work, "theme_weak", score,
                         Evidence(kind="theme", text=work.summary[:80], position="简介含主题词",
                                  matched_query=theme, score=score, verified=False), platform)

    # 台词（字幕）
    queries = [q for q in uq.sentence_queries if strip_punct(q)]
    for work, line in corpus.lines:
        if not _on_platform(work, platform):
            continue
        norm_line, plain_line = normalize(line.text), strip_punct(line.text)
        for q in queries:
            plain_q = strip_punct(q)
            if not plain_q:
                continue
            if plain_q == plain_line or (
                len(plain_q) >= 8 and plain_q in plain_line and len(plain_q) / max(1, len(plain_line)) >= 0.6
            ):
                _add(bucket, work, "line_exact", 1.0,
                     Evidence(kind="line", text=line.text, position=line.position or "台词",
                              matched_query=q, score=1.0, verified=True), platform)
                continue
            if len(plain_q) < 4:
                continue
            score = 0.0
            if plain_q in plain_line:
                score = 0.85 + 0.1 * (len(plain_q) / max(1, len(plain_line)))
            else:
                cover = containment(q, line.text)
                if cover >= 0.5:
                    score = 0.45 + 0.4 * (0.6 * cover + 0.4 * similarity(q, line.text))
            if score:
                _add(bucket, work, "line_fuzzy", min(score, 0.94),
                     Evidence(kind="line", text=line.text, position=line.position or "台词",
                              matched_query=q, score=min(score, 0.94), verified=False), platform)

    # 主角 / 演员名
    for name in uq.characters:
        for work, ch in corpus.char_index.get(normalize(name), []):
            if work.type != "short_drama" or not _on_platform(work, platform):
                continue
            score = 0.5 + 0.3 * ch.importance_score
            _add(bucket, work, "cast", score,
                 Evidence(kind="character", text=f"{ch.name}（{ch.role}）：{ch.description}",
                          position="角色/演员", matched_query=name, score=score, verified=True), platform)


def recall(corpus: Corpus, uq: Understanding, kind: str, platform: str | None = None) -> list[Candidate]:
    """在单个平台（或全量，platform=None）上执行全部通道。"""
    bucket: dict[str, Candidate] = {}
    if kind == "novel":
        _novel_sentence_channels(corpus, uq, platform, bucket)
        _novel_entity_channels(corpus, uq, platform, bucket)
        _novel_summary_channel(corpus, uq, platform, bucket)
    else:
        _drama_channels(corpus, uq, platform, bucket)
    return list(bucket.values())
