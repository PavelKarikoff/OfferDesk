"""Бизнес-правила валидации DealExtraction.

issues — критичные нарушения (validation.ok = False), warnings — некритичные.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

from ..schema import DealExtraction

MIN_BUDGET = 3_000_000
MIN_PRICE_PER_M2 = 60_000
AREA_RANGE = (50, 500)

PHONE_RE = re.compile(r"^\+7\d{10}$")
EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


@dataclass
class ValidationResult:
    ok: bool = True
    issues: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {"ok": self.ok, "issues": self.issues, "warnings": self.warnings}


def validate(extraction: DealExtraction) -> ValidationResult:
    result = ValidationResult()

    if extraction.deal.budget_rub is not None and extraction.deal.budget_rub < MIN_BUDGET:
        result.issues.append(f"budget_below_min:{int(extraction.deal.budget_rub)}")
        result.ok = False

    if extraction.object.area_m2 is not None:
        lo, hi = AREA_RANGE
        if not (lo <= extraction.object.area_m2 <= hi):
            result.issues.append(f"area_out_of_range:{extraction.object.area_m2}")
            result.ok = False

    if extraction.client.phone and not PHONE_RE.match(extraction.client.phone):
        result.issues.append(f"phone_format:{extraction.client.phone}")
        result.ok = False

    if extraction.client.email and not EMAIL_RE.match(extraction.client.email):
        result.issues.append(f"email_format:{extraction.client.email}")
        result.ok = False

    if extraction.deal.budget_rub and extraction.object.area_m2:
        ppm = extraction.deal.budget_rub / extraction.object.area_m2
        if ppm < MIN_PRICE_PER_M2:
            result.warnings.append(f"below_company_price:{int(ppm)}")

    return result
