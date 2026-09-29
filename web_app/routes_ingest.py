"""POST /ingest — публичный приём заявок в контур квалификации.

Отдельный от CRM-формы endpoint: принимает JSON с текстом заявки,
прогоняет через extraction.pipeline.extract, валидирует, эскалирует,
считает lead scoring, пишет в audit_log.

Ответ — два блока: CRM-контракт (`extraction`, `crm`, scoring) и
формальный inbox-контракт (`action`: intent / summary / priority /
next_action / fields / confidence / escalate).

Если LLM и regex оба упали — HTTP 200, `source=error`, безопасный
`action` с `escalate=true` и запись в audit_log (`status=error`).

Пример:
    curl -X POST http://localhost:5001/ingest \\
         -H "Content-Type: application/json" \\
         -H "X-Api-Token: <FLASK_API_TOKEN>" \\
         -d '{"transcript": "Здравствуйте, меня зовут Сергей..."}'
"""
from __future__ import annotations

import logging
import os
import sys
from pathlib import Path

from flask import Blueprint, jsonify, request

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from extraction.actions import build_envelope
from extraction.audit.logger import log_error, log_extraction
from extraction.crm_adapter import to_crm_dict
from extraction.pipeline import extract
from extraction.scoring.lead_score import score as lead_score
from extraction.validation.escalation import escalate
from extraction.validation.rules import validate
from db_utils import connect_db

logger = logging.getLogger(__name__)

ingest_bp = Blueprint("ingest", __name__)


def _check_token() -> bool:
    """Проверяет X-Api-Token, если FLASK_API_TOKEN задан в .env."""
    expected = os.getenv("FLASK_API_TOKEN", "").strip()
    if not expected:
        return True  # токен не задан — открытый endpoint (только для dev)
    provided = (
        request.headers.get("X-Api-Token")
        or request.headers.get("X-API-Token")
        or ""
    ).strip()
    auth = request.headers.get("Authorization", "").strip()
    if not provided and auth.lower().startswith("bearer "):
        provided = auth[7:].strip()
    return provided == expected


def _safe_error_action(detail: str) -> dict:
    """Inbox-контракт, когда LLM и regex оба упали — не 500, а эскалация."""
    return {
        "intent": "escalate",
        "summary": "Ошибка извлечения — передать оператору",
        "priority": "high",
        "next_action": "Передать оператору — автоматический разбор не удался",
        "fields": {},
        "confidence": "low",
        "escalate": True,
        "meta": {
            "source": "error",
            "reasons": ["extract_failed"],
            "llm_error": detail,
            "llm_attempted": True,
        },
    }


@ingest_bp.route("/ingest", methods=["POST"])
def ingest():
    if not _check_token():
        return jsonify({"error": "unauthorized"}), 401

    data = request.get_json(silent=True) or {}
    transcript = (data.get("transcript") or "").strip()

    if not transcript:
        return jsonify({"error": "transcript is required"}), 400

    conn = connect_db()

    try:
        extracted, source, meta = extract(transcript)
    except Exception as e:
        logger.exception("ingest: extract failed completely")
        detail = f"{type(e).__name__}: {e}"
        try:
            log_error(
                conn,
                deal_id=None,
                source="error",
                input_text=transcript,
                error_detail=detail,
            )
        except Exception:
            logger.exception("ingest: audit_log error write failed")
        conn.close()
        return jsonify({
            "source": "error",
            "action": _safe_error_action(detail),
            "error": detail,
        }), 200

    validation = validate(extracted)
    escalation = escalate(
        extracted,
        validation,
        raw_text=transcript,
        source=source,
        llm_error=meta.get("llm_error"),
    )
    lead = lead_score(extracted)

    crm_data = to_crm_dict(extracted, overrides=None, source=source)
    envelope = build_envelope(
        extracted,
        validation,
        escalation,
        lead,
        source=source,
        fields=crm_data,
        pipeline_meta=meta,
    )

    try:
        log_extraction(
            conn,
            deal_id=None,
            extraction=extracted,
            source=source,
            input_text=transcript,
            validation=validation,
            escalation=escalation,
            lead=lead,
            status="escalated" if escalation else "success",
            error_detail=meta.get("llm_error") or "",
        )
    except Exception:
        logger.exception("ingest: audit_log write failed")
    conn.close()

    return jsonify({
        "source": source,
        "etalon_score": extracted.etalon_score(),
        "lead_grade": lead.grade,
        "lead_score": lead.score,
        "validation": validation.to_dict(),
        "escalation": escalation.to_dict() if escalation else None,
        "extraction": extracted.model_dump(),
        "crm": crm_data,
        "action": envelope.to_dict(),
    }), 200
