"""Правила эскалации сделки.

Эскалация — сигнал «нужно вмешательство человека».
Возвращает EscalationDecision или None.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from ..schema import DealExtraction
from .rules import ValidationResult

# Метрика качества разбора, не триггер эскалации.
# Regex всегда даёт 0.3; порог 0.6 эскалировал бы каждую regex-сделку.
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


def escalate(
    extraction: DealExtraction,
    validation: ValidationResult | None = None,
    raw_text: str = "",
) -> EscalationDecision | None:
    reasons: list[str] = []

    # low_confidence — метрика, не триггер: regex всегда даёт 0.3,
    # эскалация срабатывала бы на каждой regex-сделке.
    # Оставляем в audit_log через confidence, но не эскалируем по нему.

    if _has_legal_risk(extraction, raw_text):
        reasons.append("legal_risk")

    if (
        extraction.sales_signals.sentiment == "негативный"
        and extraction.etalon_score() < 30
    ):
        reasons.append("negative_and_low_score")

    if validation and not validation.ok:
        reasons.append("validation_failed")

    if not reasons:
        return None

    target = "юрист + руководитель ОП" if "legal_risk" in reasons else "руководитель ОП"
    return EscalationDecision(reasons=reasons, target=target, priority="high")
