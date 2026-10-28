"""Выгрузка сделки в Bitrix24: лид, транскрибация, файл КП."""

from __future__ import annotations

import base64
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

WEB = Path(__file__).resolve().parents[1]
ROOT = WEB.parent
sys.path.insert(0, str(WEB))
sys.path.insert(0, str(ROOT))

from bitrix_client import (  # noqa: E402
    BitrixError,
    _error_message,
    bitrix_configured,
    publish_deal,
)


def _deal(**extra) -> dict:
    deal = {
        "id": 4,
        "client_name": "Иванов Иван Иванович",
        "client_phone": "+7 900 000-00-00",
        "client_email": "ivan@example.com",
        "client_telegram": "@ivan",
        "transcript": "Звонок: дом 150 м², газобетон, бюджет 9 млн.",
        "notes": "",
        "status": "new",
        "status_label": "Новая",
        "plot": "12 соток",
        "area": "150",
        "material": "газобетон",
        "budget": "9 млн",
        "timeline": "ноябрь 2026",
        "funding_source": "ипотека",
        "catalog_project": "",
        "etalon_score": 86,
        "etalon_missing": ["Сроки старта"],
        "extraction_source": "llm",
    }
    deal.update(extra)
    return deal


def _audit(**extra) -> dict:
    audit = {
        "lead_grade": "B",
        "lead_score": 0.55,
        "escalation_reasons": ["legal_risk"],
        "escalation_target": "юрист",
        "audit_source": "llm",
    }
    audit.update(extra)
    return audit


class _Portal:
    def __init__(self, *, stage: str = "NEW", fail_files: bool = False):
        self.stage = stage
        self.fail_files = fail_files
        self.calls: list[tuple[str, dict]] = []

    def __call__(self, method: str, payload: dict):
        self.calls.append((method, payload))
        if method == "crm.item.add":
            return {"item": {"id": 17, "stageId": self.stage}}
        if method == "crm.item.update":
            return {"item": {"id": payload["id"], "stageId": self.stage}}
        if method == "crm.item.get":
            return {"item": {"id": 17, "stageId": self.stage}}
        if method == "crm.item.list":
            return {"items": [{"id": 90}]}
        if method == "crm.timeline.comment.add":
            files = (payload.get("fields") or {}).get("FILES")
            if self.fail_files and files:
                raise BitrixError("портал не принял файл")
            return 1
        if method == "tasks.task.add":
            return {"task": {"id": 5}}
        if method == "im.notify.system.add":
            return True
        raise AssertionError(method)

    def methods(self) -> list[str]:
        return [method for method, _payload in self.calls]

    def comments(self) -> list[dict]:
        return [
            payload["fields"]
            for method, payload in self.calls
            if method == "crm.timeline.comment.add"
        ]


class PublishDealTests(unittest.TestCase):
    def test_creates_lead_with_transcript_task_and_notice(self):
        portal = _Portal()
        result = publish_deal(
            _deal(),
            audit=_audit(),
            notify=True,
            transport=portal,
            user_id=1,
        )

        self.assertEqual(result.entity_label, "лид")
        self.assertEqual(result.entity_id, 17)
        self.assertEqual(result.lead_id, 17)
        self.assertEqual(result.task_id, 5)
        self.assertIsNone(result.warning)
        self.assertEqual(
            portal.methods(),
            [
                "crm.item.add",
                "crm.timeline.comment.add",
                "tasks.task.add",
                "im.notify.system.add",
            ],
        )

        fields = portal.calls[0][1]["fields"]
        self.assertEqual(fields["title"], "Заявка: Иванов Иван Иванович — лид B")
        self.assertEqual(fields["name"], "Иван")
        self.assertEqual(fields["lastName"], "Иванов")
        self.assertEqual(fields["secondName"], "Иванович")
        self.assertEqual(fields["sourceDescription"], "OfferDesk")
        self.assertEqual(fields["fm"][0]["value"], "+7 900 000-00-00")

        comment = portal.comments()[0]
        self.assertIn("Транскрибация звонка:", comment["COMMENT"])
        self.assertIn("дом 150 м²", comment["COMMENT"])
        self.assertIn("лид: b", comment["COMMENT"].lower())
        self.assertEqual(comment["ENTITY_TYPE"], "lead")
        transcript = base64.b64decode(comment["FILES"][0][1]).decode("utf-8")
        self.assertIn("газобетон", transcript)
        self.assertIn("L_17", portal.calls[2][1]["fields"]["UF_CRM_TASK"])

    def test_attaches_kp_pdf(self):
        portal = _Portal()
        with tempfile.TemporaryDirectory() as tmp:
            pdf = Path(tmp) / "kp.pdf"
            pdf.write_bytes(b"%PDF-1.4 test")
            result = publish_deal(
                _deal(),
                audit=_audit(escalation_reasons=[]),
                kp_meta={"kp_number": "КП-4", "total_fmt": "11 250 000 ₽", "watermark": "draft"},
                kp_pdf=pdf,
                notify=False,
                transport=portal,
                user_id=1,
            )
        self.assertIsNone(result.warning)
        self.assertNotIn("tasks.task.add", portal.methods())
        kp = portal.comments()[1]
        self.assertIn("КП-4", kp["COMMENT"])
        self.assertEqual(kp["FILES"][0][0], "KP_DomMaster_deal4.pdf")
        self.assertEqual(base64.b64decode(kp["FILES"][0][1]), b"%PDF-1.4 test")

    def test_repeat_updates_same_lead(self):
        portal = _Portal()
        result = publish_deal(
            _deal(bitrix_lead_id=17, bitrix_entity_type="lead", bitrix_entity_id=17, bitrix_task_id=5),
            audit=_audit(),
            notify=False,
            transport=portal,
            user_id=1,
        )
        self.assertEqual(result.lead_id, 17)
        self.assertEqual(result.task_id, 5)
        self.assertNotIn("crm.item.add", portal.methods())
        self.assertIn("crm.item.update", portal.methods())
        self.assertNotIn("tasks.task.add", portal.methods())
        self.assertNotIn("im.notify.system.add", portal.methods())

    def test_converted_lead_binds_comment_to_deal(self):
        portal = _Portal(stage="CONVERTED")
        result = publish_deal(
            _deal(),
            audit=_audit(escalation_reasons=[]),
            notify=False,
            transport=portal,
            user_id=1,
        )
        self.assertEqual(result.entity_type, "deal")
        self.assertEqual(result.entity_id, 90)
        self.assertEqual(result.lead_id, 17)
        self.assertEqual(portal.comments()[0]["ENTITY_TYPE"], "deal")
        self.assertEqual(portal.comments()[0]["ENTITY_ID"], 90)

    def test_file_rejection_keeps_text_comment(self):
        portal = _Portal(fail_files=True)
        result = publish_deal(
            _deal(),
            audit=_audit(escalation_reasons=[]),
            notify=False,
            transport=portal,
            user_id=1,
        )
        self.assertIn("Файл к комментарию не приложен", result.warning or "")
        self.assertEqual(len(portal.comments()), 2)
        self.assertNotIn("FILES", portal.comments()[1])
        self.assertIn("Транскрибация", portal.comments()[1]["COMMENT"])

    def test_omits_empty_contacts(self):
        portal = _Portal()
        publish_deal(
            _deal(client_phone="", client_email=""),
            audit=_audit(escalation_reasons=[]),
            notify=False,
            transport=portal,
            user_id=1,
        )
        self.assertNotIn("fm", portal.calls[0][1]["fields"])


class WebhookTests(unittest.TestCase):
    def test_missing_url(self):
        with mock.patch.dict(os.environ, {"B24_URL": "", "BITRIX_URL": ""}, clear=False):
            self.assertFalse(bitrix_configured())
            with self.assertRaises(BitrixError) as caught:
                publish_deal(_deal(), audit=_audit(), notify=False)
        self.assertIn("BITRIX_URL", caught.exception.message)

    def test_bitrix_url_alias_is_enough(self):
        with mock.patch.dict(
            os.environ,
            {"B24_URL": "", "BITRIX_URL": "https://portal.bitrix24.ru/rest/3/token/"},
            clear=False,
        ):
            self.assertTrue(bitrix_configured())

    def test_malformed_url(self):
        with mock.patch.dict(
            os.environ,
            {"B24_URL": "http://example.com/not-a-hook", "BITRIX_URL": ""},
            clear=False,
        ):
            with self.assertRaises(BitrixError) as caught:
                publish_deal(_deal(), audit=_audit(), notify=False)
        self.assertIn("не похож", caught.exception.message)

    def test_error_hides_token(self):
        base = "https://portal.bitrix24.ru/rest/1/secret-token/"
        text = _error_message("OTHER", f"fail {base} secret-token", base)
        self.assertNotIn("secret-token", text)
        self.assertNotIn("portal.bitrix24.ru", text)


if __name__ == "__main__":
    unittest.main()
