"""Схема Item — задача или заметка из свободного текста.

Соответствует ТЗ варианта 1 («Персональный помощник: от текста до действия»):
  POST /capture        — текст → Item
  GET  /tasks          — витрина задач
  POST /tasks/{id}/done — отметка выполненной

needs_review=True выставляется, если:
  - confidence="low" (численно < 0.5);
  - JSON не разобрался / не прошёл Pydantic-валидацию → INVALID_JSON;
  - структура JSON не совпадает со схемой → SCHEMA_MISMATCH;
  - вход противоречивый / слишком общий → AMBIGUOUS_INPUT.
"""
from __future__ import annotations

from datetime import date
from typing import Literal, Optional

from pydantic import BaseModel, Field, field_validator


ItemType = Literal["task", "note"]
Priority = Literal["low", "medium", "high"]
ReviewReason = Literal[
    "LOW_CONFIDENCE",
    "INVALID_JSON",
    "SCHEMA_MISMATCH",
    "AMBIGUOUS_INPUT",
]
ConfidenceBand = Literal["low", "medium", "high"]


class ItemExtraction(BaseModel):
    """Единица работы: задача или заметка."""

    item_type: ItemType = "note"
    title: str = ""
    body: str = ""
    priority: Priority = "medium"
    due_date: Optional[date] = None
    tags: list[str] = Field(default_factory=list)

    # Служебные поля (не из LLM, вычисляются в pipeline)
    confidence: float = Field(0.0, ge=0.0, le=1.0)
    confidence_band: ConfidenceBand = "low"
    needs_review: bool = False
    review_reason: Optional[ReviewReason] = None

    @field_validator("title", "body", mode="before")
    @classmethod
    def _strip_strings(cls, v):
        return (v or "").strip()

    @field_validator("tags", mode="before")
    @classmethod
    def _normalize_tags(cls, v):
        if not v:
            return []
        if isinstance(v, str):
            return [t.strip() for t in v.split(",") if t.strip()]
        return [str(t).strip() for t in v if str(t).strip()]

    def to_dict(self) -> dict:
        data = self.model_dump()
        if self.due_date:
            data["due_date"] = self.due_date.isoformat()
        return data


# --- Хелперы для pipeline ------------------------------------------------

VAGUE_MARKERS = (
    "потом разберусь",
    "сделай важное",
    "что-то важное",
    "как-нибудь",
    "позже",
    "потом",
)

MIN_TITLE_LEN = 5
MIN_CONFIDENCE = 0.5


def is_ambiguous(text: str) -> bool:
    """Вход противоречивый / слишком общий / без сути."""
    t = (text or "").strip().lower()
    if len(t) < MIN_TITLE_LEN:
        return True
    if any(m in t for m in VAGUE_MARKERS):
        return True
    return False


def band_from_confidence(conf: float) -> ConfidenceBand:
    if conf >= 0.75:
        return "high"
    if conf >= MIN_CONFIDENCE:
        return "medium"
    return "low"
