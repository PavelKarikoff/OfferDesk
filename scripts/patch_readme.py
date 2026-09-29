"""Вставляет блоки в README.md OfferDesk. Идемпотентно."""
from pathlib import Path

README = Path(__file__).resolve().parents[1] / "README.md"
text = README.read_text(encoding="utf-8")

BLOCK_QUALITY_ROI = """

### Качество извлечения (golden set)

15 синтетических транскрибаций с эталонными JSON — `tests/golden_set/`.

**Baseline regex** (проверено, `python3 scripts/run_golden_set.py --force-regex`):

| Поле | Precision | Recall | F1 |
|---|---|---|---|
| phone | 1.00 | 1.00 | 1.00 |
| email | 1.00 | 1.00 | 1.00 |
| plot | 0.91 | 1.00 | 0.95 |
| material | 1.00 | 1.00 | 1.00 |
| area_m2 | 0.70 | 0.54 | 0.61 |
| financing | 1.00 | 0.50 | 0.67 |
| start_date | 0.60 | 0.30 | 0.40 |
| budget_rub | 0.00 | 0.00 | 0.00 |

Exact `etalon_score`: 8/15 (53.3%).

**Что это значит:** regex надёжен на телефоне, почте, материале, участке.
Теряет бюджет (0.00 recall), срок старта (0.30), финансирование (0.50)
и площадь (0.54). Закрытие этих полей — задача LLM-слоя с structured
output и few-shot (промпт v2 готов, `extraction/prompts/extractor_v2.md`).

### Окупаемость

При чеке 8 млн ₽ и марже 15% внедрение окупается **от 1 недели до 1 месяца**:
- высвобождение времени менеджеров: ~83 ч/мес (5 менеджеров × 20 КП × 50 мин);
- рост конверсии в замер на 10–15%: +1–2 сделки/мес.

Затраты: OpenAI API ~15–30 ₽/сделка, VPS ~700 ₽/мес,
подписка 30–50 тыс. ₽/мес.

Полный расчёт и ограничения — в [`docs/ROI.md`](docs/ROI.md).
"""

BLOCK_ARCH = """
### Архитектура

Полная схема процесса, таблица слоёв `extraction/`, контур отказа —
в [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md).

Кратко: транскрибация → `extract()` → `llm`/`regex`/`merged` →
`DealExtraction` → валидация + эскалация + lead scoring → CRM-словарь
→ `audit_log`.

Контур отказа: при недоступности LLM автоматически включается
regex-fallback. Проверено вживую (прокси OpenAI недоступен —
53.3% exact etalon_score).
"""

STACK_ROWS = """| LLM-извлечение | `extraction/` (Pydantic + `utils.chat_json` + regex-fallback) |
| Валидация / эскалация | `extraction/validation/` |
| Lead scoring | `extraction/scoring/lead_score.py` |
| Аудит | `extraction/audit/logger.py` → `audit_log` (SQLite) |
"""

OFFER_LINK = "🎯 **Оффер для фриланса:** `docs/OFFER.md`  \n"

changed = []

# 1. Блок «Качество + Окупаемость» — перед ## Стек
if "### Качество извлечения (golden set)" not in text:
    marker = "\n## Стек"
    if marker in text:
        text = text.replace(marker, BLOCK_QUALITY_ROI + marker, 1)
        changed.append("Возможности: качество + окупаемость")

# 2. Строки в «Стек» — после последней строки таблицы
if "| LLM-извлечение |" not in text:
    lines = text.split("\n")
    insert_at = None
    in_stack = False
    for i, line in enumerate(lines):
        if line.strip() == "## Стек":
            in_stack = True
            continue
        if in_stack and line.startswith("## "):
            break
        if in_stack and line.startswith("|"):
            insert_at = i + 1
    if insert_at:
        for row in reversed(STACK_ROWS.rstrip().split("\n")):
            lines.insert(insert_at, row)
        text = "\n".join(lines)
        changed.append("Стек: 4 строки")

# 3. Блок «Архитектура» — перед ## Возможное развитие
if "### Архитектура" not in text:
    marker = "\n## Возможное развитие"
    if marker in text:
        text = text.replace(marker, BLOCK_ARCH + marker, 1)
        changed.append("Архитектура")

# 4. Ссылка на OFFER — в шапку, после строки с «Документация:»
if "Оффер для фриланса" not in text:
    idx = text.find("📚 **Документация:**")
    if idx != -1:
        end_of_line = text.find("\n", idx)
        text = text[:end_of_line + 1] + OFFER_LINK + text[end_of_line + 1:]
        changed.append("Ссылка на OFFER")

README.write_text(text, encoding="utf-8")
print("Изменения:", changed if changed else "нет (всё уже вставлено)")
