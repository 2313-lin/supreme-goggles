"""文本工具测试。"""
from __future__ import annotations

import unittest

from nds.text import (
    BM25Index,
    bigrams,
    containment,
    extract_quoted,
    normalize,
    pinyin_initials,
    similarity,
    split_sentences,
    strip_punct,
)


class TextTests(unittest.TestCase):
    def test_normalize_fullwidth_and_space(self):
        self.assertEqual(normalize("我 爱 你！"), "我爱你!")
        self.assertEqual(normalize("ＡＢＣ"), "abc")

    def test_normalize_traditional(self):
        self.assertIn("爱", normalize("愛"))
        self.assertEqual(strip_punct("她說：「再見」"), "她说再见")

    def test_bigrams(self):
        self.assertEqual(bigrams("长夜将明"), ["长夜", "夜将", "将明"])
        self.assertEqual(bigrams("ab12"), ["ab12"])

    def test_similarity_and_containment(self):
        self.assertGreater(similarity("她说她从来没有恨过我", "她说她从来没有恨过我，只是不想再见到我"), 0.8)
        self.assertGreater(containment("她说她从来没有恨过我", "她说她从来没有恨过我，只是不想再见到我。"), 0.9)
        self.assertEqual(similarity("", "abc"), 0.0)

    def test_bm25_ranks_matching_doc_first(self):
        idx = BM25Index([
            ("a", bigrams("长夜将明，有些人却永远留在了黑里")),
            ("b", bigrams("苏晚把婚书撕成两半")),
        ])
        scores = idx.scores(bigrams("长夜将明"))
        self.assertIn("a", scores)
        self.assertGreater(scores["a"], scores.get("b", 0))

    def test_extract_quoted(self):
        found = extract_quoted("《长夜将明》里那句「她说她从来没有恨过我」")
        kinds = {k for k, _ in found}
        self.assertIn("title", kinds)
        self.assertIn("sentence", kinds)

    def test_split_sentences(self):
        parts = split_sentences("第一句。第二句！第三句？")
        self.assertEqual(len(parts), 3)

    def test_pinyin_initials(self):
        self.assertEqual(pinyin_initials("chang ye jiang ming"), "cyjm")


if __name__ == "__main__":
    unittest.main()
