"""Прогон 10 тестовых входов через item_pipeline. Отчёт в reports/item_tests.md.

Соответствует ТЗ варианта 1: 10 тестовых входов
(4 задачи, 4 заметки, 1 шумный, 1 плохой).
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from extraction.item_pipeline import extract_item  # noqa: E402

TESTS_DIR = ROOT / "knowledge_base" / "item_tests"
REPORT = ROOT / "reports" / "item_tests.md"


def main() -> int:
    files = sorted(TESTS_DIR.glob("*.txt"))
    if not files:
        print(f"Нет файлов в {TESTS_DIR}")
        return 1

    rows = []
    counts: dict[str, int] = {}
    review_count = 0
    type_counts = {"task": 0, "note": 0}

    for f in files:
        text = f.read_text(encoding="utf-8").strip()
        item, source, meta = extract_item(text)
        counts[source] = counts.get(source, 0) + 1
        type_counts[item.item_type] = type_counts.get(item.item_type, 0) + 1
        if item.needs_review:
            review_count += 1

        rows.append({
            "file": f.name,
            "source": source,
            "item_type": item.item_type,
            "title": (item.title or "")[:60],
            "priority": item.priority,
            "due_date": item.due_date.isoformat() if item.due_date else "",
            "confidence": f"{item.confidence:.2f}",
            "needs_review": "да" if item.needs_review else "нет",
            "review_reason": item.review_reason or "",
        })

    lines = [
        "# Тестовые входы — 10 кейсов",
        "",
        f"Прогон: {len(files)} входов.",
        f"Типы: task={type_counts['task']}, note={type_counts['note']}",
        "Источники: " + ", ".join(f"{k}={v}" for k, v in sorted(counts.items())),
        f"needs_review: {review_count}",
        "",
        "| Файл | Source | Тип | Заголовок | Приоритет | Срок | Conf | Review | Причина |",
        "|---|---|---|---|---|---|---|---|---|",
    ]
    for r in rows:
        lines.append(
            f"| {r['file']} | {r['source']} | {r['item_type']} | {r['title']} | "
            f"{r['priority']} | {r['due_date'] or '—'} | {r['confidence']} | "
            f"{r['needs_review']} | {r['review_reason'] or '—'} |"
        )

    report = "\n".join(lines)
    REPORT.parent.mkdir(exist_ok=True)
    REPORT.write_text(report, encoding="utf-8")
    print(report)
    print(f"\nОтчёт: {REPORT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
