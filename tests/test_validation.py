"""Тесты бизнес-правил и эскалации."""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from extraction.schema import DealExtraction  # noqa: E402
from extraction.validation.rules import validate  # noqa: E402
from extraction.validation.escalation import escalate  # noqa: E402


def _mk(**kwargs) -> DealExtraction:
    return DealExtraction.model_validate(kwargs)


class TestValidationRules(unittest.TestCase):
    def test_full_deal_ok(self):
        e = _mk(
            client={"phone": "+79161234567", "email": "a@b.ru"},
            object={"plot": "12 соток", "area_m2": 150, "material": "газобетон"},
            deal={"budget_rub": 9000000, "financing": "ипотека", "start_date": "ноябрь 2026"},
        )
        self.assertTrue(validate(e).ok)

    def test_budget_below_min(self):
        e = _mk(deal={"budget_rub": 2000000})
        r = validate(e)
        self.assertFalse(r.ok)
        self.assertTrue(any("budget_below_min" in i for i in r.issues))

    def test_area_out_of_range(self):
        e = _mk(object={"area_m2": 1000})
        r = validate(e)
        self.assertFalse(r.ok)
        self.assertTrue(any("area_out_of_range" in i for i in r.issues))

    def test_phone_format_bad(self):
        e = _mk(client={"phone": "9161234567"})
        r = validate(e)
        self.assertFalse(r.ok)
        self.assertTrue(any("phone_format" in i for i in r.issues))

    def test_email_format_bad(self):
        e = _mk(client={"email": "no-at-sign"})
        r = validate(e)
        self.assertFalse(r.ok)
        self.assertTrue(any("email_format" in i for i in r.issues))

    def test_below_company_price_warning(self):
        e = _mk(object={"area_m2": 200}, deal={"budget_rub": 5000000})
        r = validate(e)
        self.assertTrue(any("below_company_price" in w for w in r.warnings))


class TestEscalation(unittest.TestCase):
    def test_no_escalation_for_full_deal(self):
        e = _mk(
            client={"phone": "+79161234567", "email": "a@b.ru"},
            object={"plot": "12 соток", "area_m2": 150, "material": "газобетон"},
            deal={"budget_rub": 9000000, "financing": "ипотека", "start_date": "ноябрь 2026"},
            confidence={"overall": 0.9},
        )
        self.assertIsNone(escalate(e, validate(e)))

    def test_low_confidence_escalates(self):
        e = _mk(confidence={"overall": 0.3})
        d = escalate(e)
        self.assertIsNotNone(d)
        self.assertTrue(any("low_confidence" in r for r in d.reasons))

    def test_legal_risk_escalates(self):
        e = _mk(
            sales_signals={"objections": ["опыт суда с подрядчиком"]},
            confidence={"overall": 0.9},
        )
        d = escalate(e)
        self.assertIsNotNone(d)
        self.assertIn("legal_risk", d.reasons)
        self.assertIn("юрист", d.target)

    def test_negative_and_low_score_escalates(self):
        e = _mk(sales_signals={"sentiment": "негативный"})
        d = escalate(e)
        self.assertIsNotNone(d)
        self.assertTrue(any("negative_and_low_score" in r for r in d.reasons))

    def test_validation_failed_escalates(self):
        e = _mk(deal={"budget_rub": 2000000}, confidence={"overall": 0.9})
        v = validate(e)
        d = escalate(e, v)
        self.assertIsNotNone(d)
        self.assertIn("validation_failed", d.reasons)


if __name__ == "__main__":
    unittest.main()
