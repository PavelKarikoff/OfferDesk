"""Тесты POST /ingest."""
from __future__ import annotations

import os
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if str(ROOT / "web_app") not in sys.path:
    sys.path.insert(0, str(ROOT / "web_app"))

# Импорт app — после правок в app.py
from web_app.app import app  # noqa: E402
from extraction.pipeline import extract as real_extract  # noqa: E402


class TestIngest(unittest.TestCase):
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

        app.config["TESTING"] = True
        self.client = app.test_client()
        # Изолируем от токена в .env: иначе 400/200 превратятся в 401
        self._token = patch.dict(os.environ, {"FLASK_API_TOKEN": ""}, clear=False)
        self._token.start()

    def tearDown(self):
        self._token.stop()
        os.chdir(self._old_cwd)
        self._tmpdir.cleanup()

    def test_empty_transcript_400(self):
        resp = self.client.post("/ingest", json={"transcript": ""})
        self.assertEqual(resp.status_code, 400)

    def test_valid_transcript_200(self):
        with patch(
            "routes_ingest.extract",
            side_effect=lambda text: real_extract(text, force_regex=True),
        ):
            resp = self.client.post("/ingest", json={
                "transcript": "Здравствуйте, меня зовут Сергей, "
                              "телефон +7 916 123-45-67, хочу дом 150 квадратов из газобетона"
            })
        self.assertEqual(resp.status_code, 200)
        data = resp.get_json()
        self.assertIn("source", data)
        self.assertIn("lead_grade", data)
        self.assertIn("extraction", data)

    def test_with_token_when_set(self):
        # Если FLASK_API_TOKEN задан, без заголовка — 401
        with patch.dict(os.environ, {"FLASK_API_TOKEN": "secret-token"}):
            resp = self.client.post("/ingest", json={"transcript": "тест"})
        self.assertEqual(resp.status_code, 401)


if __name__ == "__main__":
    unittest.main()
