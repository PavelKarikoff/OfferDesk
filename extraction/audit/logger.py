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
    status TEXT NOT NULL DEFAULT 'success',
    input_text TEXT,
    result_json TEXT,
    etalon_score INTEGER,
    confidence REAL,
    validation_json TEXT,
    escalation_json TEXT,
    lead_grade TEXT,
    lead_score REAL,
    error_detail TEXT
);
CREATE INDEX IF NOT EXISTS idx_audit_deal_id ON audit_log(deal_id);
CREATE INDEX IF NOT EXISTS idx_audit_ts ON audit_log(ts);
"""

# Идемпотентная миграция для существующих БД
MIGRATIONS = [
    "ALTER TABLE audit_log ADD COLUMN status TEXT NOT NULL DEFAULT 'success'",
    "ALTER TABLE audit_log ADD COLUMN input_text TEXT",
    "ALTER TABLE audit_log ADD COLUMN result_json TEXT",
    "ALTER TABLE audit_log ADD COLUMN error_detail TEXT",
]


def ensure_table(conn: sqlite3.Connection) -> None:
    """Идемпотентно создаёт таблицу audit_log и дописывает новые колонки."""
    conn.executescript(CREATE_TABLE_SQL)
    for sql in MIGRATIONS:
        try:
            conn.execute(sql)
        except sqlite3.OperationalError:
            pass  # колонка уже есть
    # Индекс по status — после миграций: на старой таблице колонки ещё нет.
    conn.execute("CREATE INDEX IF NOT EXISTS idx_audit_status ON audit_log(status)")
    conn.commit()


def log_extraction(
    conn: sqlite3.Connection,
    *,
    deal_id: Optional[int],
    extraction: Optional[DealExtraction] = None,
    source: str,
    input_text: str = "",
    validation: Optional[ValidationResult] = None,
    escalation: Optional[EscalationDecision] = None,
    lead: Optional[LeadScore] = None,
    status: str = "success",
    error_detail: str = "",
) -> int:
    """Пишет строку в audit_log. Работает и при extraction=None (ошибка)."""
    if escalation and status == "success":
        status = "escalated"

    row = (
        deal_id,
        datetime.now(timezone.utc).isoformat(timespec="seconds"),
        source,
        status,
        input_text[:5000] if input_text else None,
        extraction.model_dump_json(ensure_ascii=False) if extraction else None,
        extraction.etalon_score() if extraction else None,
        extraction.confidence.overall if extraction else None,
        json.dumps(validation.to_dict(), ensure_ascii=False) if validation else None,
        json.dumps(escalation.to_dict(), ensure_ascii=False) if escalation else None,
        lead.grade if lead else None,
        lead.score if lead else None,
        error_detail[:2000] if error_detail else None,
    )
    cur = conn.execute(
        """INSERT INTO audit_log
           (deal_id, ts, source, status, input_text, result_json,
            etalon_score, confidence, validation_json, escalation_json,
            lead_grade, lead_score, error_detail)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        row,
    )
    conn.commit()
    logger.info(
        "audit_log: deal=%s source=%s status=%s grade=%s",
        deal_id, source, status, lead.grade if lead else None,
    )
    return cur.lastrowid


def log_error(
    conn: sqlite3.Connection,
    *,
    deal_id: Optional[int] = None,
    source: str,
    input_text: str = "",
    error_detail: str = "",
) -> int:
    """Запись в audit_log при полном провале (LLM + regex)."""
    return log_extraction(
        conn,
        deal_id=deal_id,
        extraction=None,
        source=source,
        input_text=input_text,
        status="error",
        error_detail=error_detail,
    )


def get_last_audit(conn: sqlite3.Connection, deal_id: int) -> Optional[dict]:
    """Возвращает последнюю audit-запись по сделке или None.

    Поля пригодны для проброса в шаблон карточки:
      lead_grade, lead_score, escalation_reasons, escalation_target,
      validation_issues, validation_warnings.
    """
    row = conn.execute(
        """SELECT ts, source, etalon_score, confidence,
                  validation_json, escalation_json, lead_grade, lead_score,
                  status, error_detail
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
        "audit_status": _at("status", 8) or "success",
        "audit_error": _at("error_detail", 9) or "",
    }
