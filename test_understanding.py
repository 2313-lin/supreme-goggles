"""Query 理解测试。"""
from __future__ import annotations

import unittest

from nds.corpus import Corpus
from nds.understanding import understand


class UnderstandingTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.corpus = Corpus.load()

    def test_quoted_sentence_has_top_priority(self):
        uq = understand("帮我找「她说她从来没有恨过我，只是不想再见到我」这句话", self.corpus, "all")
        self.assertEqual(uq.intent, "sentence")
        self.assertIn("句子", "".join(uq.notes) if uq.notes else "句子")
        self.assertTrue(uq.quoted_sentences)
        self.assertEqual(uq.entities[0].kind, "sentence")

    def test_sentence_without_quotes(self):
        uq = understand("苏晚把婚书撕成两半，转身走进了雨里", self.corpus, "all")
        self.assertIn("sentence", {e.kind for e in uq.entities})

    def test_title_book_marks(self):
        uq = understand("《长夜将明》好看吗", self.corpus, "all")
        self.assertEqual(uq.intent, "title")
        self.assertEqual(uq.types, ["novel"])

    def test_character_intent_routes_novel(self):
        uq = understand("林昭", self.corpus, "novel")
        self.assertEqual(uq.intent, "character")
        self.assertEqual(uq.types, ["novel"])

    def test_drama_tab_restricts_types(self):
        uq = understand("顾晚晚", self.corpus, "drama")
        self.assertEqual(uq.types, ["short_drama"])

    def test_theme_and_synonym(self):
        uq = understand("先婚后爱", self.corpus, "drama")
        self.assertIn("闪婚", uq.themes)

    def test_typo_rewrite(self):
        uq = understand("总栽的替身新娘", self.corpus, "all")
        self.assertTrue(any("总裁" in r for r in uq.rewrites))

    def test_pinyin_query(self):
        uq = understand("cyj", self.corpus, "all")
        self.assertTrue(uq.entities)
        self.assertEqual(uq.types, ["novel"])

    def test_long_sentence_also_keeps_theme(self):
        uq = understand("重生之后我把婚书撕了", self.corpus, "novel")
        kinds = {e.kind for e in uq.entities}
        self.assertIn("theme", kinds)
        self.assertIn("sentence", kinds)


if __name__ == "__main__":
    unittest.main()
