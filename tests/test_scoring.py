"""Тесты lead scoring."""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from extraction.schema import DealExtraction  # noqa: E402
from extraction.scoring.lead_score import score  # noqa: E402


def _mk(**kwargs) -> DealExtraction:
    return DealExtraction.model_validate(kwargs)


class TestLeadScore(unittest.TestCase):
    def test_empty_is_c(self):
        s = score(_mk())
        self.assertEqual(s.grade, "C")
        self.assertEqual(s.score, 0.0)

    def test_full_is_a(self):
        e = _mk(
            object={"plot": "12 соток"},
            deal={"budget_rub": 9000000, "start_date": "ноябрь 2026"},
            sales_signals={"sentiment": "позитивный", "decision_maker": True},
        )
        s = score(e)
        self.assertEqual(s.grade, "A")
        self.assertGreaterEqual(s.score, 0.7)

    def test_budget_only_is_c(self):
        e = _mk(deal={"budget_rub": 9000000})
        s = score(e)
        self.assertEqual(s.grade, "C")
        self.assertAlmostEqual(s.score, 0.25, places=2)

    def test_budget_plus_plot_is_b_or_c(self):
        e = _mk(object={"plot": "10 соток"}, deal={"budget_rub": 9000000})
        s = score(e)
        self.assertIn(s.grade, ("B", "C"))

    def test_factors_decomposed(self):
        e = _mk(
            object={"plot": "10 соток"},
            deal={"budget_rub": 9000000, "start_date": "весна 2026"},
        )
        s = score(e)
        self.assertIn("budget", s.factors)
        self.assertIn("plot", s.factors)
        self.assertIn("start_date", s.factors)
        self.assertNotIn("decision_maker", s.factors)


if __name__ == "__main__":
    unittest.main()
