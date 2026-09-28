"""Прогон golden set и расчёт метрик качества извлечения.

Запуск:
    python3 scripts/run_golden_set.py
    python3 scripts/run_golden_set.py --force-regex
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from extraction.pipeline import extract  # noqa: E402
from extraction.schema import DealExtraction  # noqa: E402

GOLDEN = ROOT / "tests" / "golden_set"
REPORT = ROOT / "reports" / "extraction_metrics.md"

FIELDS = [
    ("phone", "client.phone"),
    ("email", "client.email"),
    ("plot", "object.plot"),
    ("area_m2", "object.area_m2"),
    ("material", "object.material"),
    ("start_date", "deal.start_date"),
    ("financing", "deal.financing"),
    ("budget_rub", "deal.budget_rub"),
    ("tone", "sales_signals.tone"),
    ("sentiment", "sales_signals.sentiment"),
]

# Regex не извлекает тон и тональность — дефолты схемы не должны попадать в метрики.
LLM_ONLY_FIELDS = {"tone", "sentiment"}


def _get(obj, path: str):
    cur = obj
    for part in path.split("."):
        cur = getattr(cur, part, None)
    return cur


def _norm(v):
    if v in (None, "", "не указано", [], {}):
        return None
    if isinstance(v, str):
        return v.strip().lower()
    return v


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--force-regex", action="store_true")
    args = parser.parse_args()

    pairs = sorted(GOLDEN.glob("*.txt"))
    if not pairs:
        print(f"Нет кейсов в {GOLDEN}")
        return 1

    stats = {name: {"tp": 0, "fp": 0, "fn": 0} for name, _ in FIELDS}
    exact_match = 0
    sources = {"llm": 0, "regex": 0, "merged": 0}
    processed = 0

    for txt_path in pairs:
        json_path = txt_path.with_suffix(".json")
        if not json_path.exists():
            print(f"SKIP {txt_path.name}: нет эталонного JSON")
            continue

        transcript = txt_path.read_text(encoding="utf-8")
        expected = DealExtraction.model_validate_json(
            json_path.read_text(encoding="utf-8")
        )

        try:
            pred, source = extract(transcript, force_regex=args.force_regex)
        except Exception as e:
            print(f"FAIL {txt_path.name}: {type(e).__name__}: {e}")
            continue

        processed += 1
        sources[source] = sources.get(source, 0) + 1

        for name, path in FIELDS:
            if source == "regex" and name in LLM_ONLY_FIELDS:
                continue
            exp = _norm(_get(expected, path))
            got = _norm(_get(pred, path))
            if exp is None and got is None:
                continue
            if exp is not None and got is not None:
                stats[name]["tp"] += 1
            elif got is not None:
                stats[name]["fp"] += 1
            else:
                stats[name]["fn"] += 1

        if pred.etalon_score() == expected.etalon_score():
            exact_match += 1

    lines = [
        "# Метрики качества извлечения (golden set)",
        "",
        f"Кейсов обработано: {processed}",
        f"Источники: LLM={sources.get('llm',0)}, "
        f"regex={sources.get('regex',0)}, merged={sources.get('merged',0)}",
        f"Exact etalon_score: {exact_match}/{processed} "
        f"({100*exact_match/processed:.1f}%)" if processed else "Exact etalon_score: n/a",
        "",
        "| Поле | Precision | Recall | F1 | TP | FP | FN |",
        "|---|---|---|---|---|---|---|",
    ]
    if args.force_regex:
        lines.insert(
            4,
            "Исключены из regex-прогона (только LLM): "
            + ", ".join(sorted(LLM_ONLY_FIELDS)),
        )
    report_fields = [
        (name, path)
        for name, path in FIELDS
        if not (args.force_regex and name in LLM_ONLY_FIELDS)
    ]
    for name, _ in report_fields:
        s = stats[name]
        p = s["tp"] / (s["tp"] + s["fp"]) if (s["tp"] + s["fp"]) else 0
        r = s["tp"] / (s["tp"] + s["fn"]) if (s["tp"] + s["fn"]) else 0
        f1 = 2 * p * r / (p + r) if (p + r) else 0
        lines.append(
            f"| {name} | {p:.2f} | {r:.2f} | {f1:.2f} | "
            f"{s['tp']} | {s['fp']} | {s['fn']} |"
        )

    report = "\n".join(lines)
    REPORT.parent.mkdir(exist_ok=True)
    REPORT.write_text(report, encoding="utf-8")
    print(report)
    print(f"\nОтчёт сохранён: {REPORT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
