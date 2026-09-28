"""Сводная таблица метрик: regex / LLM v1 / LLM v2."""
from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
REPORTS = ROOT / "reports"

RUNS = [
    ("regex", REPORTS / "metrics_regex.txt"),
    ("LLM v1", REPORTS / "metrics_llm_v1.txt"),
    ("LLM v2", REPORTS / "metrics_llm_v2.txt"),
]

FIELD_RE = re.compile(
    r"^\|\s*(\w+)\s*\|\s*([\d.]+)\s*\|\s*([\d.]+)\s*\|\s*([\d.]+)\s*\|",
    re.MULTILINE,
)


def parse(path: Path) -> dict[str, dict[str, float]]:
    if not path.exists():
        return {}
    out = {}
    for m in FIELD_RE.finditer(path.read_text(encoding="utf-8")):
        out[m.group(1)] = {
            "precision": float(m.group(2)),
            "recall": float(m.group(3)),
            "f1": float(m.group(4)),
        }
    return out


def main() -> int:
    runs = {name: parse(path) for name, path in RUNS}
    available = {n: d for n, d in runs.items() if d}
    if not available:
        print("Нет отчётов в reports/. Сначала запусти run_golden_set.py")
        return 1

    fields = sorted({f for d in available.values() for f in d})
    active = [n for n, _ in RUNS if runs[n]]
    header = ["Поле"] + active + ([f"Δ ({active[0]}→{active[-1]})"] if len(active) > 1 else [])

    lines = [
        "# Сводная таблица метрик (recall)",
        "",
        "| " + " | ".join(header) + " |",
        "|" + "---|" * len(header),
    ]
    for field in fields:
        row = [field]
        for n in active:
            row.append(f"{runs[n].get(field, {}).get('recall', 0):.2f}")
        if len(active) > 1:
            first = runs[active[0]].get(field, {}).get("recall", 0)
            last = runs[active[-1]].get(field, {}).get("recall", 0)
            row.append(f"{last - first:+.2f}")
        lines.append("| " + " | ".join(row) + " |")

    out_path = REPORTS / "extraction_comparison.md"
    out_path.write_text("\n".join(lines), encoding="utf-8")
    print("\n".join(lines))
    print(f"\nОтчёт: {out_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
