"""Тесты формального inbox-контракта (ActionEnvelope)."""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from extraction.actions import build_envelope
from extraction.schema import DealExtraction
from extraction.scoring.lead_score import score
from extraction.validation.escalation import escalate
from extraction.validation.rules import validate


def _envelope(source="llm", raw_text="", llm_error=None, **kwargs):
    e = DealExtraction.model_validate(kwargs)
    v = validate(e)
    esc = escalate(e, v, raw_text=raw_text, source=source, llm_error=llm_error)
    lead = score(e)
    return build_envelope(
        e, v, esc, lead, source=source,
        pipeline_meta={"llm_error": llm_error, "llm_attempted": llm_error is not None or source == "llm"},
    )


class TestActionEnvelope(unittest.TestCase):
    def test_full_deal_intent_quote(self):
        env = _envelope(
            client={"phone": "+79161234567", "email": "a@b.ru"},
            object={"plot": "12 соток", "area_m2": 150, "material": "газобетон"},
            deal={"budget_rub": 9_000_000, "financing": "ипотека", "start_date": "ноябрь 2026"},
            confidence={"overall": 0.9},
        )
        self.assertEqual(env.intent, "quote_request")
        self.assertEqual(env.priority, "high")
        self.assertEqual(env.confidence, "high")
        self.assertFalse(env.escalate)

    def test_legal_risk_escalate(self):
        env = _envelope(
            sales_signals={"objections": ["опыт суда с подрядчиком"], "sentiment": "негативный"},
            confidence={"overall": 0.4},
        )
        self.assertEqual(env.intent, "escalate")
        self.assertTrue(env.escalate)
        self.assertEqual(env.priority, "high")
        self.assertIn("юрист", env.next_action)

    def test_empty_reject(self):
        env = _envelope(deal={"budget_rub": 2_000_000}, confidence={"overall": 0.2})
        self.assertIn(env.intent, ("reject", "escalate"))
        self.assertTrue(env.escalate)  # budget_below_min → validation_failed

    def test_missing_fields_qualify(self):
        env = _envelope(
            client={"phone": "+79161234567", "email": "a@b.ru"},
            object={"plot": "12 соток", "area_m2": 150},
            confidence={"overall": 0.75},
        )
        self.assertEqual(env.intent, "qualify")
        self.assertEqual(env.confidence, "high")
        self.assertFalse(env.escalate)

    def test_insufficient_data_qualifies_soft(self):
        env = _envelope(
            client={"phone": "+79161234567"},
            object={"area_m2": 150},
            confidence={"overall": 0.9},
        )
        self.assertEqual(env.intent, "qualify")
        self.assertTrue(env.escalate)
        self.assertIn("мало данных", env.next_action)

    def test_llm_low_confidence_escalates_to_manager(self):
        env = _envelope(
            client={"phone": "+79161234567", "email": "a@b.ru"},
            object={"plot": "12 соток", "area_m2": 150, "material": "газобетон"},
            deal={"budget_rub": 9_000_000, "financing": "ипотека", "start_date": "ноябрь 2026"},
            confidence={"overall": 0.4},
        )
        self.assertEqual(env.intent, "escalate")
        self.assertTrue(env.escalate)
        self.assertIn("низкая уверенность", env.next_action)

    def test_regex_source_low_confidence(self):
        e = DealExtraction.model_validate({
            "client": {"phone": "+79161234567", "email": "a@b.ru"},
            "object": {"plot": "12 соток", "area_m2": 150, "material": "газобетон"},
            "deal": {"budget_rub": 9_000_000, "financing": "ипотека", "start_date": "ноябрь 2026"},
        })
        v = validate(e)
        env = build_envelope(e, v, None, score(e), source="regex")
        self.assertEqual(env.confidence, "low")
        self.assertFalse(env.escalate)

    def test_llm_failed_escalates_to_manager(self):
        env = _envelope(
            source="regex",
            llm_error="ValidationError: schema",
            client={"phone": "+79161234567", "email": "a@b.ru"},
            object={"plot": "12 соток", "area_m2": 150, "material": "газобетон"},
            deal={"budget_rub": 9_000_000, "financing": "ипотека", "start_date": "ноябрь 2026"},
            confidence={"overall": 0.3},
        )
        self.assertEqual(env.intent, "escalate")
        self.assertTrue(env.escalate)
        self.assertIn("модель не разобрала", env.next_action)
        self.assertIn("llm_failed", env.meta["reasons"])
        self.assertEqual(env.meta["llm_error"], "ValidationError: schema")


if __name__ == "__main__":
    unittest.main()
