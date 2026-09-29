"""Оценка соответствия сделки эталонному протоколу (knowledge_base)."""

from __future__ import annotations

import os
from typing import Any, Mapping

# Порог заполнения для генерации КП (по умолчанию 80%)
KP_THRESHOLD = int(os.getenv("ETALON_KP_THRESHOLD", "80"))

# Обязательные поля эталона → колонки сделки
# Бюджет клиента — необязателен; ориентир цены — «Стоимость ТК».
# Telegram — опциональный канал; приоритет отправки КП — email (наравне с телефоном).
ETALON_FIELDS: tuple[tuple[str, str], ...] = (
    ("client_phone", "Телефон"),
    ("client_email", "Email"),
    ("plot", "Участок"),
    ("area", "Площадь"),
    ("material", "Материал стен"),
    ("timeline", "Сроки старта"),
    ("funding_source", "Финансирование"),
)

# Доп. поле эталона для домов из клееного бруса (проект каталога)
TIMBER_ETALON_EXTRA: tuple[tuple[str, str], ...] = (
    ("catalog_project", "Проект каталога"),
)

# Подсказки менеджеру: какие вопросы задать клиенту по недостающему полю
FIELD_QUESTIONS: dict[str, str] = {
    "client_phone": "Подскажите, пожалуйста, удобный номер телефона для связи?",
    "client_email": "На какой email отправить коммерческое предложение? (основной канал)",
    "client_telegram": "Есть ли Telegram? Необязательно — КП уйдёт на email.",
    "plot": "Участок уже есть? Какой размер (сотки) и где расположен?",
    "area": "Какую площадь дома планируете (м²)?",
    "material": "Подтверждаем стены из газобетона (стандарт «Дом-Мастер»)?",
    "timeline": "Когда планируете стартовать строительство (месяц/сезон и год)?",
    "funding_source": "Как планируете финансировать: свои средства, ипотека, маткапитал?",
    "catalog_project": (
        "Какой проект из каталога рассматриваем "
        "(Сириус 2.0, Альба, Оптимус… или индивидуальный)?"
    ),
}

EMPTY_MARKERS = {"", "—", "-", "None", "null", "none"}


def _is_timber_deal(get) -> bool:
    try:
        from pricing import is_timber_material
    except ImportError:  # pragma: no cover
        def is_timber_material(raw):  # type: ignore[misc]
            text = str(raw or "").lower()
            return "клеен" in text or "брус" in text

    return is_timber_material(get("material")) or is_timber_material(get("transcript"))


def etalon_fields_for(deal: Mapping[str, Any] | Any | None = None) -> tuple[tuple[str, str], ...]:
    """7 полей «Дом-Мастер»; для клееного бруса + проект из каталога."""
    if deal is None:
        return ETALON_FIELDS
    get = deal.get if isinstance(deal, Mapping) else lambda k, d=None: deal[k] if k in deal.keys() else d
    if _is_timber_deal(get):
        return ETALON_FIELDS + TIMBER_ETALON_EXTRA
    return ETALON_FIELDS


def _filled(value: Any) -> bool:
    if value is None:
        return False
    text = str(value).strip()
    return bool(text) and text not in EMPTY_MARKERS


def etalon_match_score(deal: Mapping[str, Any] | Any) -> dict[str, Any]:
    """
    Считает % соответствия эталону по заполненным полям парсинга.
    Возвращает score (0–100), grade (high|mid|low), missing и questions.
    """
    get = deal.get if isinstance(deal, Mapping) else lambda k, d=None: deal[k] if k in deal.keys() else d

    present: list[str] = []
    missing: list[str] = []
    missing_keys: list[str] = []
    questions: list[str] = []

    for key, label in etalon_fields_for(deal):
        try:
            value = get(key)
        except (KeyError, IndexError, TypeError):
            value = None
        if _filled(value):
            present.append(label)
        else:
            missing.append(label)
            missing_keys.append(key)
            question = FIELD_QUESTIONS.get(key)
            if question:
                questions.append(question)

    fields = etalon_fields_for(deal)
    total = len(fields)
    filled = len(present)
    score = int(round(100 * filled / total)) if total else 0

    if score >= KP_THRESHOLD:
        grade = "high"
    elif score >= 50:
        grade = "mid"
    else:
        grade = "low"

    catalog_ok = True
    if _is_timber_deal(get) and not _filled(get("catalog_project")):
        catalog_ok = False

    return {
        "score": score,
        "grade": grade,
        "filled": filled,
        "total": total,
        "present": present,
        "missing": missing,
        "missing_keys": missing_keys,
        "questions": questions,
        "can_generate_kp": score >= KP_THRESHOLD and catalog_ok,
        "is_complete": score >= 100,
        "threshold": KP_THRESHOLD,
    }


def can_generate_kp(deal_or_score: Mapping[str, Any] | int | float) -> bool:
    """True, если заполнение ≥ порога генерации КП."""
    if isinstance(deal_or_score, (int, float)):
        return int(deal_or_score) >= KP_THRESHOLD
    match = etalon_match_score(deal_or_score)
    return bool(match["can_generate_kp"])
