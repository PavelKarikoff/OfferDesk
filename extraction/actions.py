"""Формальный inbox-контракт: intent / summary / priority / next_action.

Вычисляется кодом поверх DealExtraction, validate(), escalate(), score().
Модель не решает escalate и priority — это код, чтобы не «замолчать»
юридический риск или провал валидации.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from .schema import DealExtraction
from .scoring.lead_score import LeadScore
from .validation.escalation import EscalationDecision
from .validation.rules import MIN_BUDGET, ValidationResult

# --- intent -------------------------------------------------------------

INTENT_QUOTE = "quote_request"  # запрос КП, всё есть
INTENT_QUALIFY = "qualify"  # не хватает данных — квалифицировать
INTENT_ESCALATE = "escalate"  # суд / провал валидации / низкая уверенность / падение LLM
INTENT_REJECT = "reject"  # бюджет ниже минимума и не тянет

CHECKLIST_KEYS = (
    "intent",
    "summary",
    "priority",
    "next_action",
    "fields",
    "confidence",
    "escalate",
)


def _budget_only_below_min(
    extraction: DealExtraction,
    validation: ValidationResult,
    escalation: EscalationDecision | None,
) -> bool:
    """True, если единственная жёсткая проблема — бюджет < минимума.

    Без этой развилки INTENT_REJECT мёртв: validate() ставит ok=False
    по budget_below_min, и ветка «not validation.ok → escalate» съедает отказ.
    Юр. риск и прочие issues (телефон, площадь) остаются escalate.
    """
    budget = extraction.deal.budget_rub
    if budget is None or budget >= MIN_BUDGET:
        return False
    if escalation and "legal_risk" in escalation.reasons:
        return False
    if escalation and "llm_failed" in escalation.reasons:
        return False
    other = [i for i in validation.issues if not i.startswith("budget_below_min")]
    return not other


def _intent(
    extraction: DealExtraction,
    validation: ValidationResult,
    escalation: EscalationDecision | None,
) -> str:
    if escalation:
        if "legal_risk" in escalation.reasons:
            return INTENT_ESCALATE
        if _budget_only_below_min(extraction, validation, escalation):
            return INTENT_REJECT
        if "validation_failed" in escalation.reasons:
            return INTENT_ESCALATE
        if any(r.startswith("low_confidence") for r in escalation.reasons):
            return INTENT_ESCALATE
        if "llm_failed" in escalation.reasons:
            return INTENT_ESCALATE
        if "insufficient_data" in escalation.reasons:
            return INTENT_QUALIFY  # мягкая эскалация — уточнить, не «жёстко эскалировать»
        return INTENT_ESCALATE
    if extraction.etalon_score() >= 80:
        return INTENT_QUOTE
    if extraction.deal.budget_rub and extraction.deal.budget_rub < MIN_BUDGET:
        return INTENT_REJECT
    return INTENT_QUALIFY


# --- priority -----------------------------------------------------------

def _priority(escalation: EscalationDecision | None, lead: LeadScore) -> str:
    if escalation:
        return "high"
    if lead.grade == "A":
        return "high"
    if lead.grade == "B":
        return "medium"
    return "low"


# --- summary ------------------------------------------------------------

def _summary(extraction: DealExtraction, lead: LeadScore) -> str:
    """Короткая сводка без выдумок — только то, что в DealExtraction."""
    parts = []
    if extraction.client.name:
        parts.append(extraction.client.name)
    if extraction.object.area_m2:
        parts.append(f"{int(extraction.object.area_m2)} м²")
    if extraction.object.material and extraction.object.material != "не указано":
        parts.append(extraction.object.material)
    if extraction.deal.budget_rub:
        parts.append(f"{int(extraction.deal.budget_rub / 1_000_000)} млн")
    if extraction.deal.start_date:
        parts.append(extraction.deal.start_date)
    prefix = f"Лид {lead.grade}"
    return f"{prefix}. " + ", ".join(parts) if parts else prefix


# --- next_action --------------------------------------------------------

def _next_action(
    extraction: DealExtraction,
    validation: ValidationResult,
    escalation: EscalationDecision | None,
) -> str:
    if _budget_only_below_min(extraction, validation, escalation):
        return "Отказать: бюджет ниже минимума компании"
    if escalation:
        if "legal_risk" in escalation.reasons:
            return "Передать юристу и руководителю ОП — юридический риск"
        if any(r.startswith("low_confidence") for r in escalation.reasons):
            return "Передать менеджеру — низкая уверенность модели, нужны уточнения"
        if "llm_failed" in escalation.reasons:
            return "Передать менеджеру — модель не разобрала заявку, нужны уточнения"
        if "insufficient_data" in escalation.reasons:
            return "Передать менеджеру — мало данных, уточнить поля"
        if "validation_failed" in escalation.reasons:
            return "Передать руководителю ОП — провал валидации"
        return f"Передать {escalation.target}"
    if extraction.etalon_score() >= 80:
        return "Сгенерировать КП и отправить клиенту"
    if extraction.missing_fields:
        missing = ", ".join(extraction.missing_fields)
        return f"Дозаполнить поля ({missing}), затем КП"
    return "Связаться с клиентом"


# --- public -------------------------------------------------------------

@dataclass
class ActionEnvelope:
    intent: str
    summary: str
    priority: str  # low / medium / high
    next_action: str
    fields: dict  # извлечённые данные (CRM-проекция)
    confidence: str  # high / medium / low
    escalate: bool
    meta: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "intent": self.intent,
            "summary": self.summary,
            "priority": self.priority,
            "next_action": self.next_action,
            "fields": self.fields,
            "confidence": self.confidence,
            "escalate": self.escalate,
            "meta": self.meta,
        }


def _confidence_enum(overall: float, source: str) -> str:
    """low / medium / high. Regex всегда low (0.3). LLM — по overall."""
    if source == "regex":
        return "low"
    if overall >= 0.75:
        return "high"
    if overall >= 0.5:
        return "medium"
    return "low"


def build_envelope(
    extraction: DealExtraction,
    validation: ValidationResult,
    escalation: EscalationDecision | None,
    lead: LeadScore,
    *,
    source: str,
    fields: dict | None = None,
    pipeline_meta: dict | None = None,
) -> ActionEnvelope:
    """Строит формальный inbox-контракт из результата pipeline."""
    pipe = pipeline_meta or {}
    return ActionEnvelope(
        intent=_intent(extraction, validation, escalation),
        summary=_summary(extraction, lead),
        priority=_priority(escalation, lead),
        next_action=_next_action(extraction, validation, escalation),
        fields=fields or {},
        confidence=_confidence_enum(extraction.confidence.overall, source),
        escalate=bool(escalation),
        meta={
            "source": source,
            "etalon_score": extraction.etalon_score(),
            "lead_grade": lead.grade,
            "lead_score": lead.score,
            "reasons": escalation.reasons if escalation else [],
            "llm_error": pipe.get("llm_error"),
            "llm_attempted": pipe.get("llm_attempted"),
        },
    )
