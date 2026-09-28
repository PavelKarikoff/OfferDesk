"""Аудит извлечения: запись в SQLite-таблицу audit_log."""
from __future__ import annotations

import json
import logging
import sqlite3
from datetime import datetime, timezone
from typing import Optional

from ..schema import DealExtraction
from ..validation.rules import ValidationResult
from ..validation.escalation import EscalationDecision
from ..scoring.lead_score import LeadScore

logger = logging.getLogger(__name__)


CREATE_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS audit_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    deal_id INTEGER,
    ts TEXT NOT NULL,
    source TEXT NOT NULL,
    etalon_score INTEGER,
    confidence REAL,
    validation_json TEXT,
    escalation_json TEXT,
    lead_grade TEXT,
    lead_score REAL
);
CREATE INDEX IF NOT EXISTS idx_audit_deal_id ON audit_log(deal_id);
CREATE INDEX IF NOT EXISTS idx_audit_ts ON audit_log(ts);
"""


def ensure_table(conn: sqlite3.Connection) -> None:
    """Идемпотентно создаёт таблицу audit_log."""
    conn.executescript(CREATE_TABLE_SQL)
    conn.commit()


def log_extraction(
    conn: sqlite3.Connection,
    *,
    deal_id: Optional[int],
    extraction: DealExtraction,
    source: str,
    validation: Optional[ValidationResult] = None,
    escalation: Optional[EscalationDecision] = None,
    lead: Optional[LeadScore] = None,
) -> int:
    """Пишет строку в audit_log. Возвращает id."""
    row = (
        deal_id,
        datetime.now(timezone.utc).isoformat(timespec="seconds"),
        source,
        extraction.etalon_score(),
        extraction.confidence.overall,
        json.dumps(validation.to_dict(), ensure_ascii=False) if validation else None,
        json.dumps(escalation.to_dict(), ensure_ascii=False) if escalation else None,
        lead.grade if lead else None,
        lead.score if lead else None,
    )
    cur = conn.execute(
        """INSERT INTO audit_log
           (deal_id, ts, source, etalon_score, confidence,
            validation_json, escalation_json, lead_grade, lead_score)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        row,
    )
    conn.commit()
    logger.info("audit_log: deal=%s source=%s grade=%s",
                deal_id, source, lead.grade if lead else None)
    return cur.lastrowid


def get_last_audit(conn: sqlite3.Connection, deal_id: int) -> Optional[dict]:
    """Возвращает последнюю audit-запись по сделке или None.

    Поля пригодны для проброса в шаблон карточки:
      lead_grade, lead_score, escalation_reasons, escalation_target,
      validation_issues, validation_warnings.
    """
    row = conn.execute(
        """SELECT ts, source, etalon_score, confidence,
                  validation_json, escalation_json, lead_grade, lead_score
           FROM audit_log WHERE deal_id = ? ORDER BY id DESC LIMIT 1""",
        (deal_id,),
    ).fetchone()
    if not row:
        return None

    def _at(key: str, idx: int):
        """Поддержка sqlite3.Row и обычного tuple."""
        try:
            return row[key]
        except (TypeError, IndexError, KeyError):
            return row[idx]

    escalation = None
    esc_json = _at("escalation_json", 5)
    if esc_json:
        escalation = json.loads(esc_json)

    validation = None
    val_json = _at("validation_json", 4)
    if val_json:
        validation = json.loads(val_json)

    return {
        "audit_ts": _at("ts", 0),
        "audit_source": _at("source", 1),
        "audit_confidence": _at("confidence", 3),
        "lead_grade": _at("lead_grade", 6) or "",
        "lead_score": _at("lead_score", 7) or 0,
        "escalation_reasons": escalation["reasons"] if escalation else [],
        "escalation_target": escalation["target"] if escalation else "",
        "validation_issues": validation["issues"] if validation else [],
        "validation_warnings": validation["warnings"] if validation else [],
    }
