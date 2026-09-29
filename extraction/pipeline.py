"""Пайплайн извлечения: LLM → fallback на regex → merge.

Точка входа: extract(transcript) -> (DealExtraction, source, meta).

Стратегия:
  1) force_regex=True → сразу regex (для тестов, для отключения LLM);
  2) иначе — LLM (utils.chat_json). При любой ошибке — regex;
  3) если LLM вернул confidence.overall < MIN_CONFIDENCE —
     merge: пустые поля LLM добираем из regex;
  4) source ∈ {'llm', 'regex', 'merged'} — для аудита и UI-бейджа.
  5) meta.llm_error — причина падения LLM (для envelope / эскалации).
"""
from __future__ import annotations

import logging

from .llm_extractor import extract_with_llm
from .regex_fallback import extract_with_regex
from .schema import DealExtraction

logger = logging.getLogger(__name__)

MIN_CONFIDENCE = 0.5


def _meta(*, llm_error: str | None = None, llm_attempted: bool) -> dict:
    return {"llm_error": llm_error, "llm_attempted": llm_attempted}


def extract(
    transcript: str, force_regex: bool = False,
) -> tuple[DealExtraction, str, dict]:
    """Извлекает DealExtraction из транскрибации.

    Returns:
        (DealExtraction, source, meta), source ∈ {'llm', 'regex', 'merged'}.
        meta = {"llm_error": str | None, "llm_attempted": bool}

    Raises:
        ValueError: пустая транскрибация (пробрасывается из слоёв).
    """
    if not transcript or not transcript.strip():
        raise ValueError("Пустая транскрибация")

    if force_regex:
        logger.info("force_regex=True — LLM пропущен")
        return (
            extract_with_regex(transcript),
            "regex",
            _meta(llm_error=None, llm_attempted=False),
        )

    # --- Шаг 1: LLM ---
    try:
        llm_result = extract_with_llm(transcript)
    except Exception as e:
        logger.warning(
            "LLM extraction failed (%s: %s), falling back to regex",
            type(e).__name__, e,
        )
        return (
            extract_with_regex(transcript),
            "regex",
            _meta(llm_error=f"{type(e).__name__}: {e}", llm_attempted=True),
        )

    # --- Шаг 2: confidence gate ---
    if llm_result.confidence.overall >= MIN_CONFIDENCE:
        logger.info(
            "LLM confidence %.2f ≥ %.2f — используем LLM",
            llm_result.confidence.overall, MIN_CONFIDENCE,
        )
        return llm_result, "llm", _meta(llm_error=None, llm_attempted=True)

    # --- Шаг 3: merge LLM + regex ---
    logger.info(
        "LLM confidence %.2f < %.2f — merge с regex",
        llm_result.confidence.overall, MIN_CONFIDENCE,
    )
    try:
        regex_result = extract_with_regex(transcript)
    except Exception as e:
        logger.warning(
            "Regex fallback тоже упал (%s: %s) — возвращаем LLM как есть",
            type(e).__name__, e,
        )
        return llm_result, "llm", _meta(llm_error=None, llm_attempted=True)

    merged = _merge(llm_result, regex_result)
    return merged, "merged", _meta(llm_error=None, llm_attempted=True)


def _merge(llm: DealExtraction, regex: DealExtraction) -> DealExtraction:
    """Заполняет пустые поля LLM значениями из regex.

    LLM имеет приоритет: если поле заполнено — оставляем как есть.
    Если пусто (None / "" / "не указано" / []) — берём из regex.
    После merge пересчитываем missing_fields.
    """
    data = llm.model_dump()

    def is_empty(v) -> bool:
        return v in (None, "", "не указано", [], {})

    def fill(section: str, field: str) -> None:
        cur = data[section].get(field)
        fb = getattr(getattr(regex, section), field, None)
        if is_empty(cur) and not is_empty(fb):
            data[section][field] = fb
            logger.debug("merge: %s.%s ← '%s'", section, field, fb)

    for section, fields in {
        "client": ["name", "phone", "email", "telegram"],
        "object": ["plot", "area_m2", "material", "floors", "style", "catalog_project"],
        "deal": ["budget_rub", "financing", "start_date", "urgency"],
    }.items():
        for f in fields:
            fill(section, f)

    # sales_signals: у regex их нет — остаются из LLM.
    # confidence: оставляем из LLM (regex 0.3 занизил бы метрику).

    merged = DealExtraction.model_validate(data)

    # Пересчитываем missing_fields после merge
    filled = merged.required_filled()
    merged.missing_fields = [k for k, v in filled.items() if not v]
    return merged
