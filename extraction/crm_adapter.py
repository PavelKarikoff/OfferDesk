"""Адаптер между extraction.pipeline и CRM-словарём.

CRM (web_app/routes_deals.py) работает со строками и CRM-ключами
(client_name, client_phone, plot, budget, area, material, timeline,
funding_source, catalog_project), а DealExtraction — с типизированными
полями (client.name, object.area_m2, deal.budget_rub, enum'ами).

Функция to_crm_dict решает три задачи:
  1) маппинг DealExtraction → CRM-ключи;
  2) форматирование чисел обратно в текст (budget_rub → «7.5 млн»);
  3) наложение overrides менеджера поверх (приоритет у overrides).

source передаётся отдельно: DealExtraction не хранит канал разбора.
«не указано» в material/financing становится пустой строкой — иначе
etalon_match_score засчитает enum-заглушку как заполненное поле.
"""
from __future__ import annotations

from typing import Any

from .schema import DealExtraction

_EMPTY_ENUM = "не указано"


def to_crm_dict(
    extraction: DealExtraction,
    overrides: dict | None = None,
    source: str = "",
) -> dict:
    """Конвертирует DealExtraction в словарь со строковыми CRM-ключами.

    Args:
        extraction: результат extraction.pipeline.extract.
        overrides: значения из формы менеджера (приоритет выше extract).
        source: канал разбора, llm | regex | merged.

    Returns:
        dict с ключами: client_name, client_phone, client_email,
        client_telegram, plot, area, material, catalog_project,
        budget, timeline, funding_source, completion_percent,
        missing_fields, is_complete, extraction_source.
    """
    crm = {
        "client_name": extraction.client.name or "",
        "client_phone": extraction.client.phone or "",
        "client_email": extraction.client.email or "",
        "client_telegram": extraction.client.telegram or "",
        "plot": extraction.object.plot or "",
        "area": _format_area(extraction.object.area_m2),
        "material": _enum_text(extraction.object.material),
        "catalog_project": extraction.object.catalog_project or "",
        "budget": _format_budget(extraction.deal.budget_rub),
        "timeline": extraction.deal.start_date or "",
        "funding_source": _enum_text(extraction.deal.financing),
        "completion_percent": extraction.etalon_score(),
        "missing_fields": list(extraction.missing_fields),
        "is_complete": extraction.etalon_score() >= 80,
        "extraction_source": source or "",
    }

    # Накладываем overrides менеджера: приоритет у значений формы
    if overrides:
        for key, value in overrides.items():
            if value not in (None, ""):
                crm[key] = value

    return crm


def _enum_text(value: Any) -> str:
    """Пустая заглушка enum не должна выглядеть как заполненное поле CRM."""
    text = "" if value is None else str(value).strip()
    if text == _EMPTY_ENUM:
        return ""
    return text


def _format_area(value) -> str:
    """130.0 → '130'; 87.5 → '87.5'; None → ''."""
    if value is None:
        return ""
    if float(value).is_integer():
        return str(int(value))
    return f"{value:g}"


def _format_budget(value) -> str:
    """7_500_000.0 → '7.5 млн'; 500_000.0 → '500 тыс'.

    CRM хранит бюджет текстом. Возвращаем компактный формат,
    совместимый с тем, как менеджер вводит его вручную.
    """
    if value is None:
        return ""
    v = float(value)
    if v >= 1_000_000:
        millions = v / 1_000_000
        if millions.is_integer():
            return f"{int(millions)} млн"
        return f"{millions:g} млн"
    if v >= 1_000:
        thousands = v / 1_000
        if thousands.is_integer():
            return f"{int(thousands)} тыс"
        return f"{thousands:g} тыс"
    return _format_area(v)
