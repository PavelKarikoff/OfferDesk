"""Тесты прокси-конфига OpenAI."""

from __future__ import annotations

import os
import unittest
from unittest.mock import patch

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from utils.config import apply_outbound_proxy_env, openai_proxy_url  # noqa: E402


class OpenAIProxyConfigTests(unittest.TestCase):
    def test_priority_openai_proxy(self):
        env = {
            "OPENAI_PROXY": "http://p:1@proxy.example:8080",
            "HTTPS_PROXY": "http://other:9",
        }
        with patch.dict(os.environ, env, clear=False):
            # clear may leave other vars; set explicitly
            os.environ["OPENAI_PROXY"] = env["OPENAI_PROXY"]
            os.environ["HTTPS_PROXY"] = env["HTTPS_PROXY"]
            self.assertEqual(openai_proxy_url(), env["OPENAI_PROXY"])

    def test_apply_sets_https_proxy(self):
        with patch.dict(os.environ, {}, clear=True):
            os.environ["OPENAI_PROXY"] = "socks5://127.0.0.1:1080"
            got = apply_outbound_proxy_env()
            self.assertEqual(got, "socks5://127.0.0.1:1080")
            self.assertEqual(os.environ.get("HTTPS_PROXY"), "socks5://127.0.0.1:1080")
            self.assertIn("127.0.0.1", os.environ.get("NO_PROXY", ""))


class OpenAIClientTimeoutTests(unittest.TestCase):
    """Зависший прокси не должен съедать ретраи SDK — иначе /ingest обрывается раньше fallback."""

    def test_timeout_and_no_retries_without_proxy(self):
        env = {"OPENAI_API_KEY": "sk-test", "OPENAI_TIMEOUT": "35"}
        with patch.dict(os.environ, env, clear=True):
            with patch("utils.ai_processor.OpenAI") as mock_cls:
                from utils.ai_processor import get_openai_client

                get_openai_client()
        kwargs = mock_cls.call_args.kwargs
        self.assertEqual(kwargs["timeout"], 35.0)
        self.assertEqual(kwargs["max_retries"], 0)
        self.assertNotIn("http_client", kwargs)

    def test_timeout_and_no_retries_with_proxy(self):
        env = {
            "OPENAI_API_KEY": "sk-test",
            "OPENAI_TIMEOUT": "35",
            "OPENAI_PROXY": "http://proxy.example:8888",
        }
        with patch.dict(os.environ, env, clear=True):
            with patch("utils.ai_processor.OpenAI") as mock_cls:
                from utils.ai_processor import get_openai_client

                get_openai_client()
        kwargs = mock_cls.call_args.kwargs
        self.assertEqual(kwargs["timeout"], 35.0)
        self.assertEqual(kwargs["max_retries"], 0)
        self.assertIn("http_client", kwargs)


if __name__ == "__main__":
    unittest.main()
