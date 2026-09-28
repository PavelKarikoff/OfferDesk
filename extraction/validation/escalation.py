"""Правила эскалации сделки.

Эскалация — сигнал «нужно вмешательство человека».
Возвращает EscalationDecision или None.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from ..schema import DealExtraction
from .rules import ValidationResult

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


def _has_legal_risk(extraction: DealExtraction) -> bool:
    text = " ".join(extraction.sales_signals.objections).lower()
    return any(kw in text for kw in LEGAL_KEYWORDS)


def escalate(
    extraction: DealExtraction,
    validation: ValidationResult | None = None,
) -> EscalationDecision | None:
    reasons: list[str] = []

    if extraction.confidence.overall < MIN_CONFIDENCE:
        reasons.append(f"low_confidence:{extraction.confidence.overall:.2f}")

    if _has_legal_risk(extraction):
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
