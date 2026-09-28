"""Fallback: локальный парсер проекта → DealExtraction.

Источник — web_app.transcript_parser_local.parse_transcript_local
(обёртка над TranscriptParser().parse_text).

Особенности источника:
  - значения — строки, пустой алиас — "" (не None);
  - есть CRM-ключи (client_name, client_phone, plot, area, material,
    budget, timeline, funding_source) и алиасы (name, phone, plot_size,
    deadline, financing, email);
  - material/financing — свободный текст, не enum;
  - budget/area — текст («7-8 млн руб.», «120-140 м2»), а не числа;
  - missing_fields в CRM-ключах — не используем, пересчитываем сами;
  - completion_percent / is_complete / work_scope / status — игнорируем.

Используется, когда LLM недоступен (сеть, таймаут, невалидный JSON,
ValidationError) или вернул низкий confidence.
confidence.overall всегда 0.3: это грубая оценка, не точный разбор.
Точность — задача LLM-слоя.

Known issue (не чиним здесь): transcript_parser_local из «6.5 млн»
возвращает «5 млн руб» — regex старого парсера теряет дробную часть.
Маппер только переводит уже полученную строку в число.
"""
from __future__ import annotations

import logging
import re

from .schema import DealExtraction, Client, Object, Deal, Confidence

logger = logging.getLogger(__name__)

REGEX_CONFIDENCE = 0.3


def extract_with_regex(transcript: str) -> DealExtraction:
    """Извлекает данные локальным парсером и маппит в DealExtraction.

    Raises:
        ValueError: пустая транскрибация.
    """
    if not transcript or not transcript.strip():
        raise ValueError("Пустая транскрибация")

    # Импорт внутри функции — не тянем web_app при обычном импорте extraction.
    # etalon_score лежит в web_app/ и импортируется абсолютным именем.
    import sys
    from pathlib import Path

    web_app_dir = str(Path(__file__).resolve().parents[1] / "web_app")
    if web_app_dir not in sys.path:
        sys.path.insert(0, web_app_dir)

    from web_app.transcript_parser_local import parse_transcript_local

    raw = parse_transcript_local(transcript)
    if not isinstance(raw, dict):
        logger.warning("parse_transcript_local вернул %s, ожидался dict", type(raw))
        raw = {}

    extraction = DealExtraction(
        client=Client(
            name=_pick(raw, "client_name", "name"),
            phone=_pick(raw, "client_phone", "phone"),
            email=_pick(raw, "client_email", "email"),
            telegram=_pick(raw, "client_telegram", "telegram"),
        ),
        object=Object(
            plot=_pick(raw, "plot", "plot_size"),
            area_m2=_to_float(_pick(raw, "area", "area_m2")),
            material=_normalize_material(_pick(raw, "material", "wall_material")),
            catalog_project=_pick(raw, "catalog_project"),
        ),
        deal=Deal(
            budget_rub=_to_float(_pick(raw, "budget", "budget_rub")),
            financing=_normalize_financing(
                _pick(raw, "funding_source", "financing", "payment")
            ),
            start_date=_pick(raw, "timeline", "deadline", "start_date"),
        ),
        confidence=Confidence(overall=REGEX_CONFIDENCE, per_field={}),
    )

    # Пересчитываем missing_fields в терминах DealExtraction
    filled = extraction.required_filled()
    extraction.missing_fields = [k for k, v in filled.items() if not v]
    return extraction


# ---------- хелперы ----------

def _pick(raw: dict, *keys: str):
    """Первое непустое значение среди ключей. Пустая строка игнорируется."""
    for k in keys:
        v = raw.get(k)
        if v is None:
            continue
        if isinstance(v, str) and not v.strip():
            continue
        return v
    return None


def _to_float(value) -> float | None:
    """'120-140 м2' → 130.0; '7-8 млн руб.' → 7_500_000.0; '' → None."""
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)

    s = str(value).lower().replace(",", ".")

    multiplier = 1.0
    if "млн" in s or "million" in s:
        multiplier = 1_000_000.0
    elif "тыс" in s:
        multiplier = 1_000.0

    nums = re.findall(r"\d+(?:\.\d+)?", s)
    if not nums:
        return None
    floats = [float(n) for n in nums]
    avg = sum(floats) / len(floats)
    return avg * multiplier


def _normalize_material(value) -> str:
    """Свободный текст → enum object.material."""
    if not value:
        return "не указано"
    s = str(value).lower()
    if "газобетон" in s or "газоблок" in s:
        return "газобетон"
    if "брус" in s and ("клеен" in s or "клеён" in s):
        return "клееный брус"
    if "кирпич" in s:
        return "кирпич"
    if "каркас" in s:
        return "каркас"
    return "не указано"


def _normalize_financing(value) -> str:
    """Свободный текст → enum deal.financing."""
    if not value:
        return "не указано"
    s = str(value).lower()
    if "ипотек" in s:
        return "ипотека"
    if "мат" in s and "капит" in s:
        return "маткапитал"
    if "рассроч" in s:
        return "рассрочка"
    if "нал" in s or "свои" in s or "собствен" in s or "cash" in s:
        return "наличные"
    return "не указано"
