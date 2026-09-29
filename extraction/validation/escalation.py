"""Правила эскалации сделки.

Эскалация — сигнал «нужно вмешательство человека», а не додумывание.
Возвращает EscalationDecision или None.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from ..schema import DealExtraction
from .rules import ValidationResult

# Порог для LLM. Regex всегда даёт 0.3 — по нему не эскалируем.
MIN_CONFIDENCE = 0.6
LEGAL_KEYWORDS = ("суд", "юрид", "проверк", "травм", "угроз")


@dataclass
class EscalationDecision:
    reasons: list[str] = field(default_factory=list)
    target: str = "руководитель ОП"
    priority: str = "high"

    def to_dict(self) -> dict:
        return {
            "reasons": self.reasons,
            "target": self.target,
            "priority": self.priority,
        }


def _has_legal_risk(extraction: DealExtraction, raw_text: str = "") -> bool:
    """Ищет юридические маркеры в objections И в сыром тексте.

    Fallback на raw_text нужен, потому что regex-парсер не заполняет
    sales_signals.objections — без него судебный риск на regex-сделке
    потерялся бы.
    """
    sources = [
        " ".join(extraction.sales_signals.objections).lower(),
        raw_text.lower(),
    ]
    return any(kw in text for text in sources for kw in LEGAL_KEYWORDS)


def _has_low_confidence_reason(reasons: list[str]) -> bool:
    return any(r.startswith("low_confidence") for r in reasons)


def escalate(
    extraction: DealExtraction,
    validation: ValidationResult | None = None,
    raw_text: str = "",
    source: str = "llm",
    llm_error: str | None = None,
) -> EscalationDecision | None:
    reasons: list[str] = []

    # 1) Низкая уверенность LLM → эскалация (по чеклисту).
    #    Для regex не эскалируем: regex всегда 0.3, шум.
    if source == "llm" and extraction.confidence.overall < MIN_CONFIDENCE:
        reasons.append(f"low_confidence:{extraction.confidence.overall:.2f}")

    # 2) Мало данных (etalon < 30) → эскалация «нужны уточнения».
    if extraction.etalon_score() < 30:
        reasons.append("insufficient_data")

    # 3) Юридический риск — из objections И сырого текста.
    if _has_legal_risk(extraction, raw_text):
        reasons.append("legal_risk")

    # 4) Негатив + низкий score.
    if (
        extraction.sales_signals.sentiment == "негативный"
        and extraction.etalon_score() < 30
    ):
        reasons.append("negative_and_low_score")

    # 5) Провал бизнес-валидации.
    if validation and not validation.ok:
        reasons.append("validation_failed")

    # 6) LLM упал (невалидный JSON / схема / сеть) — regex только страховка.
    if llm_error:
        reasons.append("llm_failed")

    if not reasons:
        return None

    if "legal_risk" in reasons:
        target = "юрист + руководитель ОП"
    elif (
        "insufficient_data" in reasons
        or "llm_failed" in reasons
        or _has_low_confidence_reason(reasons)
    ):
        target = "менеджер (нужны уточнения)"
    else:
        target = "руководитель ОП"

    return EscalationDecision(reasons=reasons, target=target, priority="high")
