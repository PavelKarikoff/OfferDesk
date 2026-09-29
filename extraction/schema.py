"""Pydantic-схемы для LLM-извлечения данных из транскрибации звонка."""
from __future__ import annotations

from typing import Literal, Optional
from pydantic import BaseModel, Field, EmailStr, field_validator


Material = Literal["газобетон", "клееный брус", "кирпич", "каркас", "не указано"]
Financing = Literal["ипотека", "наличные", "маткапитал", "рассрочка", "не указано"]
Sentiment = Literal["позитивный", "нейтральный", "негативный"]
Tone = Literal["спокойный", "требовательный", "сомневающийся", "агрессивный", "не определён"]


class Client(BaseModel):
    name: Optional[str] = None
    phone: Optional[str] = None
    email: Optional[str] = None
    telegram: Optional[str] = None


class Object(BaseModel):
    plot: Optional[str] = Field(None, description="есть/нет + соток")
    area_m2: Optional[float] = None
    material: Material = "не указано"
    floors: Optional[int] = None
    style: Optional[str] = None
    catalog_project: Optional[str] = None

    @field_validator("material", mode="before")
    @classmethod
    def _material_none(cls, v):
        if v in (None, ""):
            return "не указано"
        return v


class Deal(BaseModel):
    budget_rub: Optional[float] = None
    financing: Financing = "не указано"
    start_date: Optional[str] = None
    urgency: Optional[Literal["высокая", "средняя", "низкая"]] = None

    @field_validator("financing", mode="before")
    @classmethod
    def _financing_none(cls, v):
        if v in (None, ""):
            return "не указано"
        return v


class SalesSignals(BaseModel):
    objections: list[str] = Field(default_factory=list)
    tone: Tone = "не определён"
    sentiment: Sentiment = "нейтральный"
    competitors_mentioned: list[str] = Field(default_factory=list)
    decision_maker: Optional[bool] = None


class Confidence(BaseModel):
    overall: float = Field(0.0, ge=0.0, le=1.0)
    per_field: dict[str, float] = Field(default_factory=dict)


class DealExtraction(BaseModel):
    client: Client = Field(default_factory=Client)
    object: Object = Field(default_factory=Object)
    deal: Deal = Field(default_factory=Deal)
    sales_signals: SalesSignals = Field(default_factory=SalesSignals)
    confidence: Confidence = Field(default_factory=Confidence)
    missing_fields: list[str] = Field(default_factory=list)

    @field_validator("missing_fields")
    @classmethod
    def _unique(cls, v: list[str]) -> list[str]:
        return list(dict.fromkeys(v))

    def required_filled(self) -> dict[str, bool]:
        """Обязательные поля эталона для КП."""
        return {
            "phone": bool(self.client.phone),
            "email": bool(self.client.email),
            "plot": bool(self.object.plot),
            "area_m2": bool(self.object.area_m2),
            "material": self.object.material != "не указано",
            "start_date": bool(self.deal.start_date),
            "financing": self.deal.financing != "не указано",
        }

    def etalon_score(self) -> int:
        """% заполнения эталона (как в текущей CRM)."""
        filled = self.required_filled()
        return int(round(100 * sum(filled.values()) / len(filled)))
