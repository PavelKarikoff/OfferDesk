"""Тесты журнала audit_log: схема, миграция, статусы, ошибки."""
from __future__ import annotations

import json
import sqlite3
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from extraction.audit.logger import (  # noqa: E402
    ensure_table,
    get_last_audit,
    log_error,
    log_extraction,
)
from extraction.schema import DealExtraction  # noqa: E402
from extraction.scoring.lead_score import score as lead_score  # noqa: E402
from extraction.validation.escalation import EscalationDecision  # noqa: E402
from extraction.validation.rules import validate  # noqa: E402


def _mk(**kwargs) -> DealExtraction:
    return DealExtraction.model_validate(kwargs)


OLD_SCHEMA_SQL = """
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


class TestAuditLog(unittest.TestCase):
    def setUp(self):
        self.conn = sqlite3.connect(":memory:")
        self.conn.row_factory = sqlite3.Row

    def tearDown(self):
        self.conn.close()

    def _columns(self) -> set[str]:
        return {row[1] for row in self.conn.execute("PRAGMA table_info(audit_log)")}

    def test_new_table_has_qc_columns(self):
        ensure_table(self.conn)
        cols = self._columns()
        for name in (
            "status", "input_text", "result_json", "error_detail",
            "etalon_score", "confidence", "validation_json", "escalation_json",
        ):
            self.assertIn(name, cols)

    def test_migrates_existing_table(self):
        self.conn.executescript(OLD_SCHEMA_SQL)
        self.conn.commit()
        ensure_table(self.conn)
        cols = self._columns()
        self.assertIn("status", cols)
        self.assertIn("input_text", cols)
        self.assertIn("result_json", cols)
        self.assertIn("error_detail", cols)
        ensure_table(self.conn)  # повторно — без ошибки

    def test_log_extraction_stores_input_and_result(self):
        ensure_table(self.conn)
        extraction = _mk(
            client={"phone": "+79161234567", "email": "a@b.ru", "name": "Сергей"},
            object={"plot": "12 соток", "area_m2": 150, "material": "газобетон"},
            deal={"budget_rub": 9_000_000, "financing": "ипотека", "start_date": "весна 2026"},
            confidence={"overall": 0.9},
        )
        validation = validate(extraction)
        lead = lead_score(extraction)
        log_extraction(
            self.conn,
            deal_id=1,
            extraction=extraction,
            source="llm",
            input_text="Клиент Сергей, дом 150 м²",
            validation=validation,
            lead=lead,
        )
        row = self.conn.execute("SELECT * FROM audit_log").fetchone()
        self.assertEqual(row["status"], "success")
        self.assertEqual(row["input_text"], "Клиент Сергей, дом 150 м²")
        self.assertIsNone(row["error_detail"])
        payload = json.loads(row["result_json"])
        self.assertEqual(payload["client"]["name"], "Сергей")
        self.assertEqual(payload["object"]["area_m2"], 150)

        audit = get_last_audit(self.conn, 1)
        self.assertEqual(audit["audit_status"], "success")
        self.assertEqual(audit["audit_source"], "llm")

    def test_escalation_sets_status_escalated(self):
        ensure_table(self.conn)
        extraction = _mk(confidence={"overall": 0.4})
        escalation = EscalationDecision(
            reasons=["legal_risk"],
            target="юрист + руководитель ОП",
            priority="high",
        )
        log_extraction(
            self.conn,
            deal_id=2,
            extraction=extraction,
            source="llm",
            input_text="угроза судом",
            escalation=escalation,
        )
        row = self.conn.execute("SELECT status, escalation_json FROM audit_log").fetchone()
        self.assertEqual(row["status"], "escalated")
        self.assertIn("legal_risk", row["escalation_json"])

        audit = get_last_audit(self.conn, 2)
        self.assertEqual(audit["audit_status"], "escalated")
        self.assertEqual(audit["escalation_reasons"], ["legal_risk"])

    def test_log_error_without_extraction(self):
        ensure_table(self.conn)
        log_error(
            self.conn,
            source="pipeline",
            input_text="короткая заявка",
            error_detail="ValueError: Пустая транскрибация",
        )
        row = self.conn.execute("SELECT * FROM audit_log").fetchone()
        self.assertEqual(row["status"], "error")
        self.assertEqual(row["source"], "pipeline")
        self.assertEqual(row["input_text"], "короткая заявка")
        self.assertIsNone(row["result_json"])
        self.assertIsNone(row["etalon_score"])
        self.assertIn("ValueError", row["error_detail"])

    def test_input_text_truncated(self):
        ensure_table(self.conn)
        log_error(
            self.conn,
            source="pipeline",
            input_text="я" * 6000,
            error_detail="x" * 3000,
        )
        row = self.conn.execute(
            "SELECT length(input_text) AS n, length(error_detail) AS e FROM audit_log"
        ).fetchone()
        self.assertEqual(row["n"], 5000)
        self.assertEqual(row["e"], 2000)


if __name__ == "__main__":
    unittest.main()
