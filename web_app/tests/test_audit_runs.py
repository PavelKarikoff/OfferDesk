"""audit_runs: миграция action/duration_ms и запись из POST /capture."""
from __future__ import annotations

import os
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from flask import Flask

WEB = Path(__file__).resolve().parents[1]
ROOT = WEB.parent
sys.path.insert(0, str(WEB))
sys.path.insert(0, str(ROOT))

from db_utils import ensure_audit_runs_table  # noqa: E402
from extraction.audit.logger import log_item  # noqa: E402
from extraction.schemas.item import ItemExtraction  # noqa: E402
from routes_tasks import tasks_bp  # noqa: E402

OLD_AUDIT_RUNS = """
CREATE TABLE audit_runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    item_id INTEGER,
    ts TEXT NOT NULL,
    source TEXT NOT NULL,
    status TEXT NOT NULL,
    input_text TEXT,
    result_json TEXT,
    error TEXT,
    confidence REAL,
    needs_review INTEGER NOT NULL DEFAULT 0
)
"""


def _item() -> ItemExtraction:
    return ItemExtraction(
        item_type="task",
        title="Позвонить",
        body="Позвонить Сергею",
        priority="high",
        confidence=0.9,
        confidence_band="high",
    )


class AuditRunsMigrationTests(unittest.TestCase):
    def test_old_table_gains_action_and_duration(self):
        conn = sqlite3.connect(":memory:")
        conn.execute(OLD_AUDIT_RUNS)
        conn.execute(
            """INSERT INTO audit_runs (item_id, ts, source, status, input_text)
               VALUES (1, '2026-10-01T00:00:00', 'llm', 'success', 'старая запись')"""
        )
        conn.commit()

        ensure_audit_runs_table(conn)

        cols = {row[1] for row in conn.execute("PRAGMA table_info(audit_runs)")}
        self.assertIn("action", cols)
        self.assertIn("duration_ms", cols)
        action, duration = conn.execute(
            "SELECT action, duration_ms FROM audit_runs WHERE item_id = 1"
        ).fetchone()
        self.assertEqual(action, "extract")
        self.assertIsNone(duration)

    def test_log_item_writes_action_and_duration(self):
        conn = sqlite3.connect(":memory:")
        ensure_audit_runs_table(conn)
        row_id = log_item(
            conn,
            item_id=7,
            item=_item(),
            source="llm",
            action="capture",
            input_text="Позвонить Сергею",
            duration_ms=42,
            status="success",
        )
        row = conn.execute(
            "SELECT action, duration_ms, item_id, status FROM audit_runs WHERE id = ?",
            (row_id,),
        ).fetchone()
        self.assertEqual(row, ("capture", 42, 7, "success"))


class CaptureAuditTests(unittest.TestCase):
    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        self._old_cwd = os.getcwd()
        os.chdir(self._tmpdir.name)
        self.app = Flask(__name__)
        self.app.register_blueprint(tasks_bp)
        self.app.config["TESTING"] = True
        self.client = self.app.test_client()
        self._env = mock.patch.dict(os.environ, {"FLASK_API_TOKEN": ""}, clear=False)
        self._env.start()

    def tearDown(self):
        self._env.stop()
        os.chdir(self._old_cwd)
        self._tmpdir.cleanup()

    def test_capture_records_action_and_duration(self):
        with mock.patch(
            "routes_tasks.extract_item",
            return_value=(_item(), "llm", {"error": None, "llm_attempted": True}),
        ):
            r = self.client.post("/capture", json={"text": "Позвонить Сергею"})
        self.assertEqual(r.status_code, 200)
        body = r.get_json()
        self.assertIn("id", body["item"])

        conn = sqlite3.connect("deals.db")
        row = conn.execute(
            "SELECT action, duration_ms, input_text, status FROM audit_runs"
        ).fetchone()
        conn.close()
        self.assertEqual(row[0], "capture")
        self.assertIsInstance(row[1], int)
        self.assertGreaterEqual(row[1], 0)
        self.assertEqual(row[2], "Позвонить Сергею")
        self.assertEqual(row[3], "success")
