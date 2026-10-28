"""Выбор модели менеджером: сессия важнее LLM_PROVIDER из окружения."""

from __future__ import annotations

import os
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from flask import Flask, session  # noqa: E402

from utils.ai_processor import llm_provider  # noqa: E402


class LlmChoiceTests(unittest.TestCase):
    def test_session_openai_overrides_env_gigachat(self):
        app = Flask(__name__)
        app.secret_key = "test"
        with app.test_request_context():
            session["llm_provider"] = "openai"
            with patch.dict(os.environ, {"LLM_PROVIDER": "gigachat"}, clear=False):
                self.assertEqual(llm_provider(), "openai")

    def test_without_request_env_wins(self):
        with patch.dict(os.environ, {"LLM_PROVIDER": "gigachat"}, clear=False):
            self.assertEqual(llm_provider(), "gigachat")

    def test_unknown_session_value_falls_back(self):
        app = Flask(__name__)
        app.secret_key = "test"
        with app.test_request_context():
            session["llm_provider"] = "claude"
            with patch.dict(os.environ, {"LLM_PROVIDER": "gigachat"}, clear=False):
                self.assertEqual(llm_provider(), "gigachat")


if __name__ == "__main__":
    unittest.main()
