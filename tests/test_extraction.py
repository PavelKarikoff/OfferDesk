"""Юнит-тесты для extraction/: схема, хелперы, regex-fallback, pipeline.

Запуск из корня проекта:
    python3 -m unittest tests.test_extraction -v
или через discover:
    python3 -m unittest discover -s tests -v
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest.mock import patch

# Пути: ROOT — корень проекта, чтобы import extraction.* и knowledge_base/ работали
ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from extraction.pipeline import extract  # noqa: E402
from extraction.regex_fallback import (  # noqa: E402
    extract_with_regex,
    _pick,
    _to_float,
    _normalize_material,
    _normalize_financing,
)
from extraction.schema import DealExtraction  # noqa: E402

KB = ROOT / "knowledge_base"


class TestSchema(unittest.TestCase):
    """Pydantic-модель DealExtraction: дефолты, etalon_score, required_filled."""

    def test_empty_schema_score_zero(self):
        self.assertEqual(DealExtraction().etalon_score(), 0)

    def test_full_schema_score_hundred(self):
        e = DealExtraction.model_validate({
            "client": {"phone": "+79990000000", "email": "a@b.ru"},
            "object": {"plot": "12 соток", "area_m2": 180, "material": "газобетон"},
            "deal": {"start_date": "весна 2026", "financing": "ипотека"},
        })
        self.assertEqual(e.etalon_score(), 100)

    def test_required_filled_flags(self):
        filled = DealExtraction().required_filled()
        self.assertEqual(set(filled.keys()), {
            "phone", "email", "plot", "area_m2", "material", "start_date", "financing",
        })
        self.assertTrue(all(v is False for v in filled.values()))


class TestHelpers(unittest.TestCase):
    """Хелперы regex_fallback: _to_float, _normalize_material, _normalize_financing, _pick."""

    def test_to_float_range(self):
        self.assertEqual(_to_float("120-140 м2"), 130.0)

    def test_to_float_millions(self):
        self.assertEqual(_to_float("7-8 млн руб."), 7_500_000.0)

    def test_to_float_simple(self):
        self.assertEqual(_to_float("180"), 180.0)

    def test_to_float_thousands(self):
        self.assertEqual(_to_float("500 тыс"), 500_000.0)

    def test_to_float_none_and_empty(self):
        self.assertIsNone(_to_float(None))
        self.assertIsNone(_to_float(""))

    def test_normalize_material_synonyms(self):
        self.assertEqual(_normalize_material("газобетонные блоки"), "газобетон")
        self.assertEqual(_normalize_material("клеёный брус"), "клееный брус")
        self.assertEqual(_normalize_material("кирпичный"), "кирпич")
        self.assertEqual(_normalize_material("каркасный дом"), "каркас")

    def test_normalize_material_unknown(self):
        self.assertEqual(_normalize_material("что-то"), "не указано")
        self.assertEqual(_normalize_material(None), "не указано")

    def test_normalize_financing_synonyms(self):
        self.assertEqual(_normalize_financing("собственные средства"), "наличные")
        self.assertEqual(_normalize_financing("ипотечный"), "ипотека")
        self.assertEqual(_normalize_financing("мат. капитал"), "маткапитал")
        self.assertEqual(_normalize_financing("в рассрочку"), "рассрочка")

    def test_normalize_financing_unknown(self):
        self.assertEqual(_normalize_financing("неизвестно"), "не указано")
        self.assertEqual(_normalize_financing(None), "не указано")

    def test_pick_skips_empty_alias(self):
        raw = {"client_phone": "+79990000000", "phone": ""}
        self.assertEqual(_pick(raw, "phone", "client_phone"), "+79990000000")

    def test_pick_none_when_all_empty(self):
        self.assertIsNone(_pick({"a": "", "b": None}, "a", "b"))


class TestRegexFallback(unittest.TestCase):
    """Интеграционные тесты regex_fallback на реальных протоколах."""

    def test_empty_raises(self):
        with self.assertRaises(ValueError):
            extract_with_regex("")

    def test_protocol_1_full(self):
        text = (KB / "demo_protocol_1.md").read_text(encoding="utf-8")
        r = extract_with_regex(text)
        self.assertEqual(r.etalon_score(), 100)
        self.assertEqual(r.missing_fields, [])
        self.assertEqual(r.confidence.overall, 0.3)

    def test_protocol_2_partial(self):
        text = (KB / "demo_protocol_2.md").read_text(encoding="utf-8")
        r = extract_with_regex(text)
        self.assertLess(r.etalon_score(), 50)
        self.assertGreater(len(r.missing_fields), 0)


class TestPipeline(unittest.TestCase):
    """Pipeline: ветки llm / regex / merged, обработка ошибок, merge-логика."""

    def test_empty_raises(self):
        with self.assertRaises(ValueError):
            extract("   ")

    def test_force_regex_skips_llm(self):
        # Если LLM вызовут — тест упадёт
        with patch(
            "extraction.pipeline.extract_with_llm",
            side_effect=AssertionError("LLM не должен вызываться при force_regex=True"),
        ):
            _, src = extract("любой текст", force_regex=True)
        self.assertEqual(src, "regex")

    def test_llm_error_falls_back_to_regex(self):
        with patch(
            "extraction.pipeline.extract_with_llm",
            side_effect=ConnectionError("нет сети"),
        ):
            _, src = extract("любой текст")
        self.assertEqual(src, "regex")

    def test_llm_high_confidence_uses_llm(self):
        fake_llm = DealExtraction.model_validate({
            "client": {"phone": "+79991112233"},
            "confidence": {"overall": 0.9},
        })
        with patch("extraction.pipeline.extract_with_llm", return_value=fake_llm):
            r, src = extract("любой текст")
        self.assertEqual(src, "llm")
        self.assertEqual(r.client.phone, "+79991112233")

    def test_merge_fills_empty_fields(self):
        # LLM с низким confidence и пустыми полями → merge с реальным regex
        text = (KB / "demo_protocol_1.md").read_text(encoding="utf-8")
        fake_llm = DealExtraction.model_validate({
            "confidence": {"overall": 0.2},
            "sales_signals": {"tone": "сомневающийся"},
        })
        with patch("extraction.pipeline.extract_with_llm", return_value=fake_llm):
            r, src = extract(text)
        self.assertEqual(src, "merged")
        # Поля, которых не было у LLM, добраны из regex
        self.assertIsNotNone(r.client.phone)
        # sales_signals остались от LLM (не затираются)
        self.assertEqual(r.sales_signals.tone, "сомневающийся")
        # confidence тоже остался от LLM (не занижен до 0.3)
        self.assertEqual(r.confidence.overall, 0.2)


if __name__ == "__main__":
    unittest.main()
