#!/usr/bin/env python3
"""Пример: POST /ingest с текстом заявки. Идемпотентно, без побочек."""
from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
try:
    from dotenv import load_dotenv

    load_dotenv(ROOT / ".env")
except ImportError:
    pass

URL = os.getenv("INGEST_URL", "http://127.0.0.1:5001/ingest")
TOKEN = os.getenv("FLASK_API_TOKEN", "")

transcript = """Здравствуйте, меня зовут Сергей. Телефон +7 916 123-45-67,
почта sergey@example.com. Участок есть, 12 соток в Московской области.
Хочу дом из газобетона, 150 квадратов. Бюджет около 9 миллионов, ипотека.
Начать хотим в ноябре 2026."""


def main() -> int:
    req = urllib.request.Request(
        URL,
        data=json.dumps({"transcript": transcript}).encode("utf-8"),
        headers={
            "Content-Type": "application/json",
            **({"X-Api-Token": TOKEN} if TOKEN else {}),
        },
        method="POST",
    )

    try:
        with urllib.request.urlopen(req, timeout=120) as resp:
            result = json.loads(resp.read())
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        print(f"HTTP {exc.code}: {body}")
        return 1
    except urllib.error.URLError as exc:
        print(f"Не удалось подключиться к {URL}: {exc.reason}")
        print("Сначала запустите CRM: cd web_app && PYTHONPATH=.. python3 app.py")
        return 1

    print("source:", result["source"])
    print("etalon_score:", result["etalon_score"])
    print("lead_grade:", result["lead_grade"])
    print("escalation:", result["escalation"])
    print()
    print("action:")
    action = result["action"]
    for k in (
        "intent",
        "summary",
        "priority",
        "next_action",
        "confidence",
        "escalate",
        "meta",
    ):
        if k in action:
            print(f"  {k}: {action[k]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
