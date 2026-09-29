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
            with mock.patch("routes_ingest.extract", return_value=(fake, "regex")):
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

    def test_pipeline_json_and_audit_log(self):
        fake = _sample_extraction()
        with mock.patch("routes_ingest.extract", return_value=(fake, "llm")):
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

        conn = sqlite3.connect("deals.db")
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            "SELECT deal_id, source, lead_grade, etalon_score FROM audit_log"
        ).fetchone()
        conn.close()
        self.assertIsNotNone(row)
        self.assertIsNone(row["deal_id"])
        self.assertEqual(row["source"], "llm")
        self.assertEqual(row["etalon_score"], 100)
        self.assertEqual(row["lead_grade"], body["lead_grade"])


if __name__ == "__main__":
    unittest.main()
