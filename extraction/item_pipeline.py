"""Пайплайн «текст → Item» (вариант 1: Персональный помощник).

Единственная точка входа: extract_item(text) -> (ItemExtraction, source, meta).

Стратегия:
  1) если вход противоречивый (is_ambiguous) → needs_review=True, AMBIGUOUS_INPUT;
  2) LLM (utils.chat_json) + промпт item_v1.md → Pydantic-валидация;
  3) при любой ошибке LLM/JSON/схемы → fallback: needs_review=True + review_reason;
  4) если confidence < 0.5 → needs_review=True, LOW_CONFIDENCE.
"""
from __future__ import annotations

import logging
from datetime import date
from pathlib import Path
from typing import Optional

from pydantic import ValidationError

from utils import chat_json

from .schemas.item import (
    ItemExtraction,
    ReviewReason,
    band_from_confidence,
    is_ambiguous,
)

logger = logging.getLogger(__name__)

PROMPT_PATH = Path(__file__).parent / "prompts" / "item_v1.md"
MIN_CONFIDENCE = 0.5
_WEEKDAYS = (
    "понедельник",
    "вторник",
    "среда",
    "четверг",
    "пятница",
    "суббота",
    "воскресенье",
)


def _load_system_prompt() -> str:
    return PROMPT_PATH.read_text(encoding="utf-8")


def _user_prompt(text: str) -> str:
    """Дата нужна модели, иначе «завтра» считается от года обучения."""
    today = date.today()
    weekday = _WEEKDAYS[today.weekday()]
    return (
        f"Сегодня: {today.isoformat()} ({weekday}).\n"
        "Считай относительные даты («завтра», «до пятницы», «на следующей неделе») от этой даты.\n\n"
        f"Входной текст:\n\n{text}"
    )


def _fallback(
    text: str,
    reason: ReviewReason,
    source: str = "fallback",
    error: str = "",
) -> tuple[ItemExtraction, str, dict]:
    """Безопасный fallback: needs_review=True, без выдумок."""
    item = ItemExtraction(
        item_type="note",
        title=(text or "").strip()[:80] or "Пустой ввод",
        body=(text or "").strip(),
        priority="medium",
        confidence=0.0,
        confidence_band="low",
        needs_review=True,
        review_reason=reason,
    )
    meta = {"error": error, "llm_attempted": source != "fallback"}
    return item, source, meta


def extract_item(text: str) -> tuple[ItemExtraction, str, dict]:
    """Текст → ItemExtraction + источник + мета."""
    text = (text or "").strip()
    if not text:
        item, source, meta = _fallback(text, "AMBIGUOUS_INPUT", source="empty")
        meta["llm_attempted"] = False
        return item, source, meta

    # 1) Ранняя эвристика на противоречивый вход
    if is_ambiguous(text):
        item, source, meta = _fallback(text, "AMBIGUOUS_INPUT", source="heuristic")
        meta["llm_attempted"] = False
        return item, source, meta

    # 2) LLM + Pydantic
    try:
        raw = chat_json(_load_system_prompt(), _user_prompt(text))
    except Exception as e:
        logger.warning("item: LLM failed (%s: %s)", type(e).__name__, e)
        return _fallback(text, "INVALID_JSON", source="error", error=str(e))

    try:
        item = ItemExtraction.model_validate(raw)
    except ValidationError as e:
        logger.warning("item: Pydantic failed: %s", e)
        return _fallback(text, "SCHEMA_MISMATCH", source="schema", error=str(e))

    # 3) Пост-нормализация
    item.confidence = max(0.0, min(1.0, float(item.confidence or 0.0)))
    item.confidence_band = band_from_confidence(item.confidence)

    # 4) Если модель сама не выставила needs_review, но confidence низкий
    if item.confidence < MIN_CONFIDENCE and not item.needs_review:
        item.needs_review = True
        item.review_reason = "LOW_CONFIDENCE"

    return item, "llm", {"error": None, "llm_attempted": True}
