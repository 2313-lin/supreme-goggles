"""分层排序与置顶规则测试（§1.3 全局原则）。"""
from __future__ import annotations

import unittest

from nds.corpus import Corpus
from nds.models import Candidate, Evidence
from nds.ranking import personalization_boost, rank


def cand(work, channel, score, text=""):
    return Candidate(work=work, channel=channel, score=score,
                     evidence=[Evidence(kind="sentence", text=text, score=score, verified=True)],
                     platforms={work.platform_ids[0]})


PRIORITY = {"fanqie": 10, "qimao": 20, "shuqi": 30, "midu": 40, "lianshang": 41,
            "hongguo": 10, "hema": 20, "aikan": 30, "agg_drama": 60}


class RankingTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.corpus = Corpus.load()

    def test_sentence_hit_beats_character_and_theme(self):
        sentence_work = self.corpus.get("n_changye")
        character_work = self.corpus.get("n_tuihun")
        theme_work = self.corpus.get("n_houfu")
        results = rank([
            cand(character_work, "character", 0.95),
            cand(theme_work, "theme", 0.99),
            cand(sentence_work, "sentence_exact", 0.80),
        ], "novel", None, PRIORITY, 10)
        self.assertEqual(results[0]["work"]["id"], "n_changye")
        self.assertEqual(results[0]["layer"], 0)
        self.assertEqual(results[0]["layer_label"], "原文句子命中")

    def test_drama_name_beats_line(self):
        name_work = self.corpus.get("d_tishen")
        line_work = self.corpus.get("d_shanhun")
        results = rank([
            cand(name_work, "name_exact", 0.80),
            cand(line_work, "line_exact", 1.0),
        ], "short_drama", None, PRIORITY, 10)
        self.assertEqual(results[0]["work"]["id"], "d_tishen")
        self.assertEqual(results[0]["layer"], 0)

    def test_line_is_promoted_when_no_name_or_theme(self):
        line_work = self.corpus.get("d_shanhun")
        results = rank([cand(line_work, "line_exact", 1.0)], "short_drama", None, PRIORITY, 10)
        self.assertEqual(results[0]["layer"], 0)
        self.assertEqual(results[0]["layer_label"], "台词精确命中")

    def test_title_promoted_only_without_strong_signals(self):
        only_title = self.corpus.get("n_houfu")
        results = rank([cand(only_title, "title_exact", 1.0)], "novel", None, PRIORITY, 10)
        self.assertEqual(results[0]["layer"], 0)
        self.assertEqual(results[0]["layer_label"], "标题精确命中")

        with_theme = self.corpus.get("n_tuihun")
        results = rank([
            cand(only_title, "title_exact", 1.0),
            cand(with_theme, "theme", 0.60),
        ], "novel", None, PRIORITY, 10)
        self.assertEqual(results[0]["work"]["id"], "n_tuihun")
        self.assertEqual(results[1]["layer"], 4)

    def test_personalization_never_crosses_layer(self):
        low = self.corpus.get("n_tuihun")     # 主题层
        high = self.corpus.get("n_changye")   # 句子层
        profile = {
            "preferred_themes": ["重生", "爽文", "都市"],
            "preferred_authors": ["顾南枝"],
            "preferred_characters": ["苏晚"],
            "clicked_works": {"n_tuihun": 3},
            "preferred_types": ["novel"],
        }
        results = rank([
            cand(low, "theme", 0.60),
            cand(high, "sentence_exact", 0.60),
        ], "novel", profile, PRIORITY, 10)
        self.assertEqual(results[0]["work"]["id"], "n_changye")
        self.assertGreater(results[1]["score"], 0.60)
        self.assertLessEqual(personalization_boost(low, profile), 0.05)

    def test_duplicate_works_are_merged_with_evidence(self):
        work = self.corpus.get("n_changye")
        results = rank([
            cand(work, "sentence_exact", 1.0, "她说她从来没有恨过我"),
            cand(work, "character", 0.7, "林昭"),
        ], "novel", None, PRIORITY, 10)
        self.assertEqual(len(results), 1)
        self.assertIn("sentence_exact", results[0]["matched_channels"])
        self.assertIn("character", results[0]["matched_channels"])

    def test_platform_priority_breaks_ties(self):
        work = self.corpus.get("n_beicheng")   # qimao + shuqi
        results = rank([cand(work, "theme", 0.7)], "novel", None, PRIORITY, 10)
        self.assertEqual(results[0]["platform_primary"], "qimao")
        self.assertIn("shuqi", results[0]["also_on"])


if __name__ == "__main__":
    unittest.main()
