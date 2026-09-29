"""POST /ingest — публичный приём заявок в контур квалификации.

Отдельный от CRM-формы endpoint: принимает JSON с текстом заявки,
прогоняет через extraction.pipeline.extract, валидирует, эскалирует,
считает lead scoring, пишет в audit_log.

Ответ — два блока: CRM-контракт (`extraction`, `crm`, scoring) и
формальный inbox-контракт (`action`: intent / summary / priority /
next_action / fields / confidence / escalate).

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
from extraction.audit.logger import ensure_table, log_extraction
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


@ingest_bp.route("/ingest", methods=["POST"])
def ingest():
    if not _check_token():
        return jsonify({"error": "unauthorized"}), 401

    data = request.get_json(silent=True) or {}
    transcript = (data.get("transcript") or "").strip()

    if not transcript:
        return jsonify({"error": "transcript is required"}), 400

    try:
        extracted, source = extract(transcript)
    except Exception as e:
        logger.exception("ingest: extract failed")
        return jsonify({
            "error": "extraction_failed",
            "detail": f"{type(e).__name__}: {e}",
        }), 500

    validation = validate(extracted)
    escalation = escalate(extracted, validation, raw_text=transcript)
    lead = lead_score(extracted)

    # CRM-словарь для ответа (совместим с форматом сделки)
    crm_data = to_crm_dict(extracted, overrides=None, source=source)
    envelope = build_envelope(
        extracted,
        validation,
        escalation,
        lead,
        source=source,
        fields=crm_data,
    )

    # Аудит (deal_id=None, потому что сделка ещё не создана — ingest публичный)
    try:
        conn = connect_db()
        ensure_table(conn)
        log_extraction(
            conn,
            deal_id=None,
            extraction=extracted,
            source=source,
            validation=validation,
            escalation=escalation,
            lead=lead,
        )
        conn.close()
    except Exception:
        logger.exception("ingest: audit_log write failed")

    return jsonify({
        # старые поля (backward compatible)
        "source": source,
        "etalon_score": extracted.etalon_score(),
        "lead_grade": lead.grade,
        "lead_score": lead.score,
        "validation": validation.to_dict(),
        "escalation": escalation.to_dict() if escalation else None,
        "extraction": extracted.model_dump(),
        "crm": crm_data,
        # новый блок — формальный контракт чеклиста
        "action": envelope.to_dict(),
    }), 200
