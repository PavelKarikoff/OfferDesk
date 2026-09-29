"""Добавляет в README.md блок про апгрейд v1.1.0. Идемпотентно."""
from pathlib import Path

README = Path(__file__).resolve().parents[1] / "README.md"
text = README.read_text(encoding="utf-8")

BLOCK_V11 = """

### AI-квалификатор лидов (v1.1.0, новый слой)

Поверх существующей CRM добавлен LLM-слой для квалификации заявок:

**Бизнес-процесс:** Заявка (сайт / Авито / Циан) -> LLM-разбор -> JSON-схема -> валидация
-> эскалация -> lead scoring A/B/C -> CRM-сделка -> уведомление менеджера

**LLM-извлечение:** модель структурирует «сырую» заявку в JSON
(client / object / deal / sales_signals / confidence / missing_fields).

**Контроль качества:**
- валидация: бюджет >= 3 млн, площадь 50-500 м², форматы, цена/м² >= 60k;
- эскалация: суд/юрид в objections ИЛИ в сыром тексте -> юрист + руководитель;
  негатив + низкий etalon_score -> руководитель;
- аудит: audit_log — deal_id, source, confidence, etalon_score, lead_grade,
  escalation.

**Lead scoring A/B/C** по 6 факторам (бюджет, срок, участок, objections,
sentiment, decision_maker). A >= 0.7, B >= 0.4, C < 0.4.

**Отказоустойчивость:** LLM недоступен -> regex-fallback (проверено вживую,
exact etalon_score 53.3%).

**См. также:**
- [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) — схема процесса;
- [`docs/ROI.md`](docs/ROI.md) — окупаемость;
- [`docs/OFFER.md`](docs/OFFER.md) — оффер для фриланса.
"""

if "### AI-квалификатор лидов (v1.1.0, новый слой)" not in text:
    marker = "\n### Рабочее место менеджера (веб-CRM)"
    if marker in text:
        text = text.replace(marker, BLOCK_V11 + marker, 1)
        README.write_text(text, encoding="utf-8")
        print("Вставлен блок AI-квалификатор")
    else:
        print("Маркер не найден — пришли grep по '### Рабочее место'")
else:
    print("Блок уже есть — пропуск")
