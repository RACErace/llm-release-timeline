import unittest

import catalog


class CatalogScopeTests(unittest.TestCase):
    def test_historical_source_applies_only_to_the_original_event(self):
        paper = {
            "date": "2018-10", "org": "Google", "name": "BERT", "kind": "研究公开",
            "note": "公开双向预训练语言模型。", "url": "https://arxiv.org/abs/1810.04805",
            "source": "作者技术报告",
        }
        corrected = dict(paper, date="2018-10-11")
        catalog.validate_changes([paper], [corrected])
        with self.assertRaises(ValueError):
            catalog.validate_changes([paper], [paper, corrected])
        with self.assertRaises(ValueError):
            catalog.validate_changes([paper], [dict(paper, name="BERT-LoRA")])
