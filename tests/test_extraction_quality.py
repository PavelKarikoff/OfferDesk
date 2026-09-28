"""Пороговый тест качества извлечения: regex baseline не должен деградировать.

Пороги выставлены по факту baseline regex (28.09.2026). При улучшении
промпта/парсера пороги можно поднять.
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from extraction.pipeline import extract  # noqa: E402
from extraction.schema import DealExtraction  # noqa: E402

GOLDEN = ROOT / "tests" / "golden_set"

MIN_RECALL = {
    "phone": 0.9, "email": 0.9, "plot": 0.9,
    "area_m2": 0.7, "material": 0.9,
    "start_date": 0.4, "financing": 0.4, "budget_rub": 0.4,
}
MIN_PRECISION = {**MIN_RECALL, "plot": 0.8}
MIN_EXACT_ETALON = 0.4  # 6/15

FIELDS = [
    ("phone", "client.phone"), ("email", "client.email"),
    ("plot", "object.plot"), ("area_m2", "object.area_m2"),
    ("material", "object.material"), ("start_date", "deal.start_date"),
    ("financing", "deal.financing"), ("budget_rub", "deal.budget_rub"),
]


def _get(o, p):
    for k in p.split("."):
        o = getattr(o, k, None)
    return o


def _norm(v):
    if v in (None, "", "не указано", [], {}):
        return None
    if isinstance(v, str):
        return v.strip().lower()
    return v


class TestExtractionQuality(unittest.TestCase):
    def test_regex_baseline(self):
        stats = {n: {"tp": 0, "fp": 0, "fn": 0} for n, _ in FIELDS}
        exact, total = 0, 0

        for txt in sorted(GOLDEN.glob("*.txt")):
            jf = txt.with_suffix(".json")
            if not jf.exists():
                continue
            exp = DealExtraction.model_validate_json(jf.read_text(encoding="utf-8"))
            pred, _ = extract(txt.read_text(encoding="utf-8"), force_regex=True)
            total += 1
            for name, path in FIELDS:
                e, g = _norm(_get(exp, path)), _norm(_get(pred, path))
                if e is None and g is None:
                    continue
                if e is not None and g is not None:
                    stats[name]["tp"] += 1
                elif g is not None:
                    stats[name]["fp"] += 1
                else:
                    stats[name]["fn"] += 1
            if pred.etalon_score() == exp.etalon_score():
                exact += 1

        for name, _ in FIELDS:
            s = stats[name]
            p = s["tp"] / (s["tp"] + s["fp"]) if (s["tp"] + s["fp"]) else 1.0
            r = s["tp"] / (s["tp"] + s["fn"]) if (s["tp"] + s["fn"]) else 1.0
            with self.subTest(field=name):
                self.assertGreaterEqual(p, MIN_PRECISION[name],
                    f"{name}: precision {p:.2f} < {MIN_PRECISION[name]}")
                self.assertGreaterEqual(r, MIN_RECALL[name],
                    f"{name}: recall {r:.2f} < {MIN_RECALL[name]}")

        ratio = exact / total if total else 0
        self.assertGreaterEqual(ratio, MIN_EXACT_ETALON,
            f"exact etalon_score {exact}/{total} ({ratio:.2f}) < {MIN_EXACT_ETALON}")


if __name__ == "__main__":
    unittest.main()
