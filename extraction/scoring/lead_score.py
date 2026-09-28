"""Lead scoring: A/B/C по факторам DealExtraction.

Возвращает LeadScore (grade, score, factors) — буквенная оценка
+ разложение по факторам для аудита и UI.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from ..schema import DealExtraction
from ..validation.rules import MIN_BUDGET

W_BUDGET = 0.25
W_START_DATE = 0.15
W_PLOT = 0.15
W_NO_OBJECTIONS = 0.15
W_SENTIMENT = 0.15
W_DECISION_MAKER = 0.15

GRADE_A = 0.7
GRADE_B = 0.4


@dataclass
class LeadScore:
    grade: str
    score: float
    factors: dict[str, float] = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {"grade": self.grade, "score": self.score, "factors": self.factors}


def score(extraction: DealExtraction) -> LeadScore:
    """Считает A/B/C. A ≥ 0.7, B ≥ 0.4, C < 0.4."""
    factors: dict[str, float] = {}

    if extraction.deal.budget_rub and extraction.deal.budget_rub >= MIN_BUDGET:
        factors["budget"] = W_BUDGET

    if extraction.deal.start_date:
        factors["start_date"] = W_START_DATE

    if extraction.object.plot:
        factors["plot"] = W_PLOT

    # Пустой список возражений на пустой сделке — не сигнал качества.
    if not extraction.sales_signals.objections and extraction.etalon_score() > 0:
        factors["no_objections"] = W_NO_OBJECTIONS

    if extraction.sales_signals.sentiment == "позитивный":
        factors["positive_sentiment"] = W_SENTIMENT

    if extraction.sales_signals.decision_maker:
        factors["decision_maker"] = W_DECISION_MAKER

    total = round(sum(factors.values()), 2)
    if total >= GRADE_A:
        grade = "A"
    elif total >= GRADE_B:
        grade = "B"
    else:
        grade = "C"

    return LeadScore(grade=grade, score=total, factors=factors)
