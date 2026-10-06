"""POST /tasks/extract: строгий JSON без записи в БД."""
from __future__ import annotations

import os
import sys
import unittest
from pathlib import Path
from unittest import mock

from flask import Flask

WEB = Path(__file__).resolve().parents[1]
ROOT = WEB.parent
sys.path.insert(0, str(WEB))
sys.path.insert(0, str(ROOT))

from extraction.schemas.item import ItemExtraction  # noqa: E402
from routes_tasks import tasks_bp  # noqa: E402


def _sample_item() -> ItemExtraction:
    return ItemExtraction(
        item_type="task",
        title="Позвонить клиенту",
        body="Позвонить клиенту завтра",
        priority="high",
        confidence=0.9,
        confidence_band="high",
    )


class TasksExtractTests(unittest.TestCase):
    def setUp(self):
        self.app = Flask(__name__)
        self.app.register_blueprint(tasks_bp)
        self.app.config["TESTING"] = True
        self.client = self.app.test_client()
        self._env = mock.patch.dict(os.environ, {"FLASK_API_TOKEN": ""}, clear=False)
        self._env.start()

    def tearDown(self):
        self._env.stop()

    def test_missing_text_returns_400(self):
        r = self.client.post("/tasks/extract", json={})
        self.assertEqual(r.status_code, 400)
        self.assertEqual(r.get_json().get("error"), "text is required")

    def test_unauthorized_without_token(self):
        with mock.patch.dict(os.environ, {"FLASK_API_TOKEN": "secret-token"}):
            r = self.client.post("/tasks/extract", json={"text": "купить молоко"})
        self.assertEqual(r.status_code, 401)

    def test_returns_json_and_does_not_touch_db(self):
        fake = _sample_item()
        with mock.patch(
            "routes_tasks.extract_item",
            return_value=(fake, "llm", {"error": None, "llm_attempted": True}),
        ) as extract, mock.patch("routes_tasks.connect_db") as connect_db:
            r = self.client.post(
                "/tasks/extract",
                json={"text": "Позвонить клиенту завтра"},
            )
        self.assertEqual(r.status_code, 200)
        extract.assert_called_once_with("Позвонить клиенту завтра")
        connect_db.assert_not_called()
        body = r.get_json()
        self.assertEqual(body["source"], "llm")
        self.assertEqual(body["item"]["title"], "Позвонить клиенту")
        self.assertEqual(body["item"]["item_type"], "task")
        self.assertNotIn("id", body["item"])
        self.assertIsNone(body["meta"]["error"])
