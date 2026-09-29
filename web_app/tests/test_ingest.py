"""Тесты POST /ingest: валидация входа, токен, пайплайн, audit_log."""
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

from extraction.schema import DealExtraction  # noqa: E402
from routes_ingest import ingest_bp  # noqa: E402


def _sample_extraction() -> DealExtraction:
    return DealExtraction.model_validate({
        "client": {
            "name": "Сергей",
            "phone": "+79001112233",
            "email": "sergey@example.com",
        },
        "object": {
            "plot": "12 соток, ИЖС",
            "area_m2": 150,
            "material": "газобетон",
        },
        "deal": {
            "budget_rub": 8_000_000,
            "financing": "ипотека",
            "start_date": "весна 2026",
        },
        "sales_signals": {"sentiment": "позитивный", "decision_maker": True},
        "confidence": {"overall": 0.8},
    })


class IngestEndpointTests(unittest.TestCase):
    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        self._old_cwd = os.getcwd()
        os.chdir(self._tmpdir.name)

        bootstrap = sqlite3.connect("deals.db")
        bootstrap.execute(
            "CREATE TABLE deals (id INTEGER PRIMARY KEY AUTOINCREMENT)"
        )
        bootstrap.commit()
        bootstrap.close()

        self.app = Flask(__name__)
        self.app.register_blueprint(ingest_bp)
        self.app.config["TESTING"] = True
        self.client = self.app.test_client()

        self._env = mock.patch.dict(os.environ, {"FLASK_API_TOKEN": ""}, clear=False)
        self._env.start()

    def tearDown(self):
        self._env.stop()
        os.chdir(self._old_cwd)
        self._tmpdir.cleanup()

    def test_missing_transcript_returns_400(self):
        r = self.client.post("/ingest", json={})
        self.assertEqual(r.status_code, 400)
        self.assertEqual(r.get_json().get("error"), "transcript is required")

    def test_blank_transcript_returns_400(self):
        r = self.client.post("/ingest", json={"transcript": "   "})
        self.assertEqual(r.status_code, 400)

    def test_unauthorized_without_token(self):
        with mock.patch.dict(os.environ, {"FLASK_API_TOKEN": "secret-token"}):
            r = self.client.post(
                "/ingest",
                json={"transcript": "заявка на дом"},
            )
        self.assertEqual(r.status_code, 401)
        self.assertEqual(r.get_json().get("error"), "unauthorized")

    def test_authorized_with_x_api_token(self):
        fake = _sample_extraction()
        with mock.patch.dict(os.environ, {"FLASK_API_TOKEN": "secret-token"}):
            with mock.patch(
                "routes_ingest.extract",
                return_value=(fake, "regex", {"llm_error": None, "llm_attempted": False}),
            ):
                r = self.client.post(
                    "/ingest",
                    json={"transcript": "Здравствуйте, меня зовут Сергей"},
                    headers={"X-Api-Token": "secret-token"},
                )
        self.assertEqual(r.status_code, 200)
        body = r.get_json()
        self.assertEqual(body["source"], "regex")
        self.assertEqual(body["etalon_score"], 100)
        self.assertIn(body["lead_grade"], ("A", "B", "C"))
        self.assertIn("extraction", body)
        self.assertIn("crm", body)
        self.assertEqual(body["crm"]["client_name"], "Сергей")
        action = body["action"]
        for key in (
            "intent", "summary", "priority", "next_action",
            "fields", "confidence", "escalate",
        ):
            self.assertIn(key, action)
        self.assertEqual(action["intent"], "quote_request")
        self.assertEqual(action["confidence"], "low")  # source=regex
        self.assertFalse(action["escalate"])
        self.assertEqual(action["fields"]["client_name"], "Сергей")

    def test_pipeline_json_and_audit_log(self):
        fake = _sample_extraction()
        with mock.patch(
            "routes_ingest.extract",
            return_value=(fake, "llm", {"llm_error": None, "llm_attempted": True}),
        ):
            r = self.client.post(
                "/ingest",
                json={"transcript": "Клиент Сергей, бюджет 8 млн, участок есть"},
            )
        self.assertEqual(r.status_code, 200)
        body = r.get_json()
        self.assertEqual(body["source"], "llm")
        self.assertIsInstance(body["lead_score"], (int, float))
        self.assertIn("ok", body["validation"])
        self.assertIn("client", body["extraction"])
        self.assertEqual(body["crm"]["extraction_source"], "llm")
        self.assertEqual(body["action"]["confidence"], "high")
        self.assertEqual(body["action"]["meta"]["source"], "llm")

        conn = sqlite3.connect("deals.db")
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            "SELECT deal_id, source, lead_grade, etalon_score, "
            "status, input_text, result_json FROM audit_log"
        ).fetchone()
        conn.close()
        self.assertIsNotNone(row)
        self.assertIsNone(row["deal_id"])
        self.assertEqual(row["source"], "llm")
        self.assertEqual(row["etalon_score"], 100)
        self.assertEqual(row["lead_grade"], body["lead_grade"])
        self.assertEqual(row["status"], "success")
        self.assertIn("Сергей", row["input_text"])
        self.assertIn("Сергей", row["result_json"])

    def test_extract_failure_returns_safe_envelope(self):
        with mock.patch(
            "routes_ingest.extract",
            side_effect=ValueError("Пустая транскрибация"),
        ):
            r = self.client.post(
                "/ingest",
                json={"transcript": "сломанный разбор заявки"},
            )
        self.assertEqual(r.status_code, 200)
        body = r.get_json()
        self.assertEqual(body["source"], "error")
        self.assertIn("ValueError", body["error"])
        action = body["action"]
        self.assertEqual(action["intent"], "escalate")
        self.assertTrue(action["escalate"])
        self.assertEqual(action["confidence"], "low")
        self.assertEqual(action["priority"], "high")
        self.assertIn("оператору", action["next_action"])
        self.assertEqual(action["fields"], {})

        conn = sqlite3.connect("deals.db")
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            "SELECT status, source, input_text, error_detail, result_json FROM audit_log"
        ).fetchone()
        conn.close()
        self.assertIsNotNone(row)
        self.assertEqual(row["status"], "error")
        self.assertEqual(row["source"], "error")
        self.assertEqual(row["input_text"], "сломанный разбор заявки")
        self.assertIn("ValueError", row["error_detail"])
        self.assertIsNone(row["result_json"])

    def test_llm_fallback_escalates_and_keeps_result(self):
        fake = _sample_extraction()
        with mock.patch(
            "routes_ingest.extract",
            return_value=(
                fake,
                "regex",
                {"llm_error": "ValidationError: schema", "llm_attempted": True},
            ),
        ):
            r = self.client.post(
                "/ingest",
                json={"transcript": "Клиент Сергей, бюджет 8 млн"},
            )
        self.assertEqual(r.status_code, 200)
        action = r.get_json()["action"]
        self.assertTrue(action["escalate"])
        self.assertIn("llm_failed", action["meta"]["reasons"])
        self.assertEqual(action["meta"]["llm_error"], "ValidationError: schema")
        self.assertIn("модель не разобрала", action["next_action"])

        conn = sqlite3.connect("deals.db")
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            "SELECT status, error_detail, result_json FROM audit_log"
        ).fetchone()
        conn.close()
        self.assertEqual(row["status"], "escalated")
        self.assertIn("ValidationError", row["error_detail"])
        self.assertIn("Сергей", row["result_json"])


if __name__ == "__main__":
    unittest.main()
