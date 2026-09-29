"""Пороговый тест качества извлечения: regex baseline не должен деградировать.

Пороги — value-match baseline regex (28.09.2026): TP только если
нормализованные значения равны. При улучшении парсера пороги можно поднять.
"""
from __future__ import annotations

import importlib.util
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from extraction.pipeline import extract  # noqa: E402
from extraction.schema import DealExtraction  # noqa: E402

_runner_spec = importlib.util.spec_from_file_location(
    "run_golden_set", ROOT / "scripts" / "run_golden_set.py"
)
_runner = importlib.util.module_from_spec(_runner_spec)
_runner_spec.loader.exec_module(_runner)
_norm_for_field = _runner._norm_for_field

GOLDEN = ROOT / "tests" / "golden_set"

MIN_RECALL = {
    "phone": 0.9, "email": 0.9, "plot": 0.9,
    "area_m2": 0.5, "material": 0.9,
    "start_date": 0.3, "financing": 0.5,
    "budget_rub": 0.0,  # regex не справляется; LLM v2 — в roadmap
}
MIN_PRECISION = {**MIN_RECALL, "plot": 0.8, "area_m2": 0.6, "start_date": 0.5}
MIN_EXACT_ETALON = 0.5  # 8/15 = 0.53

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


class TestExtractionQuality(unittest.TestCase):
    def test_regex_baseline(self):
        stats = {n: {"tp": 0, "fp": 0, "fn": 0} for n, _ in FIELDS}
        exact, total = 0, 0

        for txt in sorted(GOLDEN.glob("*.txt")):
            jf = txt.with_suffix(".json")
            if not jf.exists():
                continue
            exp = DealExtraction.model_validate_json(jf.read_text(encoding="utf-8"))
            pred, *_ = extract(txt.read_text(encoding="utf-8"), force_regex=True)
            total += 1
            for name, path in FIELDS:
                e = _norm_for_field(name, _get(exp, path))
                g = _norm_for_field(name, _get(pred, path))
                if e is None and g is None:
                    continue
                if e is not None and g is not None and e == g:
                    stats[name]["tp"] += 1
                elif e is None and g is not None:
                    stats[name]["fp"] += 1
                elif e is not None and g is None:
                    stats[name]["fn"] += 1
                else:
                    stats[name]["fp"] += 1
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
