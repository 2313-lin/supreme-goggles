"""演示语料：加载 data/corpus.json，建立实体 / 句子 / 台词索引。

真实接入时，这里换成"平台适配器返回 Work 列表 + L3 按需拉取"，
索引结构（BM25 / 实体字典）保持不变。
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

from .config import DATA_DIR
from .models import Character, Sentence, SourceRef, Work
from .text import BM25Index, bigrams, normalize, pinyin_initials, strip_punct


@dataclass
class ThemeDef:
    id: str
    name: str
    aliases: list[str] = field(default_factory=list)
    category: str = ""


class Corpus:
    def __init__(self, works: list[Work], themes: list[ThemeDef]):
        self.works = works
        self.themes = themes
        self.by_id = {w.id: w for w in works}
        self.novels = [w for w in works if w.type == "novel"]
        self.dramas = [w for w in works if w.type == "short_drama"]

        self.title_index: dict[str, list[Work]] = {}
        self.char_index: dict[str, list[tuple[Work, Character]]] = {}
        self.theme_index: dict[str, str] = {}      # 别名 -> 规范主题名
        self.theme_names: list[str] = []
        self.pinyin_index: dict[str, list[Work]] = {}
        self.char_pinyin_index: dict[str, list[tuple[Work, Character]]] = {}

        self.sentences: list[tuple[Work, Sentence]] = []
        self.lines: list[tuple[Work, Sentence]] = []
        self._sentence_by_id: dict[str, tuple[Work, Sentence]] = {}

        self._build()

    # ------------------------------------------------------------------ build
    def _build(self) -> None:
        for theme in self.themes:
            self.theme_names.append(theme.name)
            self.theme_index[normalize(theme.name)] = theme.name
            for alias in theme.aliases:
                self.theme_index[normalize(alias)] = theme.name

        for work in self.works:
            for name in [work.title, *work.aliases]:
                self.title_index.setdefault(normalize(name), []).append(work)
            if work.pinyin:
                self.pinyin_index.setdefault(pinyin_initials(work.pinyin), []).append(work)
                self.pinyin_index.setdefault(strip_punct(work.pinyin).replace(" ", ""), []).append(work)
            for ch in work.characters:
                for name in [ch.name, *ch.aliases]:
                    self.char_index.setdefault(normalize(name), []).append((work, ch))
                if ch.pinyin:
                    self.char_pinyin_index.setdefault(pinyin_initials(ch.pinyin), []).append((work, ch))
            for idx, sentence in enumerate(work.sentences, start=1):
                if not sentence.id:
                    sentence.id = f"{work.id}:s{idx}"
                self.sentences.append((work, sentence))
                self._sentence_by_id[sentence.id] = (work, sentence)
                if work.type == "short_drama" and sentence.type == "subtitle":
                    self.lines.append((work, sentence))

        self.sentence_bm25 = BM25Index([(s.id, bigrams(s.text)) for _, s in self.sentences])
        self.line_bm25 = BM25Index([(s.id, bigrams(s.text)) for _, s in self.lines])

    # ------------------------------------------------------------------ load
    @classmethod
    def load(cls, path: Path | None = None) -> "Corpus":
        path = path or (DATA_DIR / "corpus.json")
        raw = json.loads(Path(path).read_text(encoding="utf-8"))
        themes = [
            ThemeDef(
                id=t.get("id", ""),
                name=t["name"],
                aliases=list(t.get("aliases", [])),
                category=t.get("category", ""),
            )
            for t in raw.get("themes", [])
        ]
        works: list[Work] = []
        for item in raw.get("works", []):
            work = Work(
                id=item["id"],
                type=item.get("type", "novel"),
                title=item["title"],
                aliases=list(item.get("aliases", [])),
                author=item.get("author", ""),
                summary=item.get("summary", ""),
                themes=list(item.get("themes", [])),
                tags=list(item.get("tags", [])),
                cover=item.get("cover", ""),
                year=item.get("year"),
                popularity=float(item.get("popularity", 0.0)),
                pinyin=item.get("pinyin", ""),
                sources=[
                    SourceRef(
                        platform=s["platform"],
                        url=s.get("url", ""),
                        tier=s.get("tier", "primary"),
                        content_level=s.get("content_level", "L1"),
                        status=s.get("status", "healthy"),
                    )
                    for s in item.get("sources", [])
                ],
                characters=[
                    Character(
                        id=c.get("id") or f"{item['id']}:c{i}",
                        work_id=item["id"],
                        name=c["name"],
                        aliases=list(c.get("aliases", [])),
                        role=c.get("role", "配角"),
                        description=c.get("description", ""),
                        importance_score=float(c.get("importance", 0.5)),
                        pinyin=c.get("pinyin", ""),
                    )
                    for i, c in enumerate(item.get("characters", []), start=1)
                ],
                sentences=[
                    Sentence(
                        id=s.get("id") or f"{item['id']}:s{i}",
                        work_id=item["id"],
                        type=s.get("type", "narration"),
                        text=s["text"],
                        position=s.get("position", ""),
                        scene_id=s.get("scene_id"),
                        start_time=s.get("start_time"),
                        end_time=s.get("end_time"),
                        keywords=list(s.get("keywords", [])),
                    )
                    for i, s in enumerate(item.get("sentences", []), start=1)
                ],
            )
            works.append(work)
        return cls(works, themes)

    # --------------------------------------------------------------- helpers
    def works_on(self, platform: str) -> list[Work]:
        return [w for w in self.works if w.source_for(platform)]

    def platform_work_ids(self, platform: str) -> set[str]:
        return {w.id for w in self.works if w.source_for(platform)}

    def get(self, work_id: str) -> Work | None:
        return self.by_id.get(work_id)

    def sentence(self, sentence_id: str) -> tuple[Work, Sentence] | None:
        return self._sentence_by_id.get(sentence_id)

    def entity_names(self) -> dict[str, list[tuple[str, str]]]:
        """供 Query 理解使用的实体词典：名称 -> [(类型, 规范名)]。"""
        table: dict[str, list[tuple[str, str]]] = {}
        for work in self.works:
            kind = "novel_title" if work.type == "novel" else "drama_title"
            for name in [work.title, *work.aliases]:
                table.setdefault(name, []).append((kind, work.title))
        for work in self.works:
            for ch in work.characters:
                for name in [ch.name, *ch.aliases]:
                    table.setdefault(name, []).append(("character", ch.name))
        for name in self.theme_names:
            table.setdefault(name, []).append(("theme", name))
        return table

    def stats(self) -> dict[str, int]:
        return {
            "works": len(self.works),
            "novels": len(self.novels),
            "dramas": len(self.dramas),
            "sentences": len(self.sentences),
            "lines": len(self.lines),
            "characters": sum(len(w.characters) for w in self.works),
            "themes": len(self.theme_names),
            "platforms": len({s.platform for w in self.works for s in w.sources}),
        }


_DEFAULT: Corpus | None = None


def default_corpus() -> Corpus:
    global _DEFAULT
    if _DEFAULT is None:
        _DEFAULT = Corpus.load()
    return _DEFAULT
