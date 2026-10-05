"""Задачи и заметки (вариант 1: Персональный помощник).

POST /capture                 — текст → Item, запись в items и audit_runs
GET  /capture                 — форма захвата
GET  /tasks                   — витрина
POST /tasks/<id>/done         — отметка выполненной
GET  /tasks/<id>/review       — карточка на проверку
POST /tasks/<id>/review       — снять needs_review
GET  /journal                 — журнал audit_runs

Токен — как у /ingest: X-Api-Token, если FLASK_API_TOKEN задан.
Сессия CRM тоже пускает (форма в браузере).
"""
from __future__ import annotations

import json
import logging
import os
import sys
from datetime import datetime
from pathlib import Path

from flask import Blueprint, jsonify, redirect, render_template, request, session, url_for

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from db_utils import connect_db
from extraction.item_pipeline import extract_item

logger = logging.getLogger(__name__)

tasks_bp = Blueprint("tasks", __name__)


def _check_token() -> bool:
    """Проверяет X-Api-Token, если FLASK_API_TOKEN задан в .env."""
    expected = os.getenv("FLASK_API_TOKEN", "").strip()
    if not expected:
        return True
    provided = (
        request.headers.get("X-Api-Token")
        or request.headers.get("X-API-Token")
        or ""
    ).strip()
    auth = request.headers.get("Authorization", "").strip()
    if not provided and auth.lower().startswith("bearer "):
        provided = auth[7:].strip()
    return provided == expected


def _allowed() -> bool:
    if session.get("user_id"):
        return True
    return _check_token()


def _wants_json() -> bool:
    """JSON — явный Accept/format или POST без HTML-формы.

    Голый curl и браузер на GET получают страницу: у них Accept */* или text/html.
    """
    if request.mimetype in {"application/x-www-form-urlencoded", "multipart/form-data"} or request.form:
        return False
    if request.is_json:
        return True
    if request.args.get("format") == "json":
        return True
    if request.method == "GET":
        return (
            request.accept_mimetypes.quality("application/json")
            > request.accept_mimetypes.quality("text/html")
        )
    return True


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _tags_dump(tags) -> str:
    if not tags:
        return ""
    if isinstance(tags, str):
        return tags
    return json.dumps(list(tags), ensure_ascii=False)


def _tags_load(raw: str | None) -> list:
    text = (raw or "").strip()
    if not text:
        return []
    if text.startswith("["):
        try:
            data = json.loads(text)
            if isinstance(data, list):
                return [str(t) for t in data]
        except (TypeError, ValueError, json.JSONDecodeError):
            pass
    return [part.strip() for part in text.split(",") if part.strip()]


def _item_dict(row) -> dict:
    due = row["due_date"]
    return {
        "id": row["id"],
        "item_type": row["item_type"],
        "title": row["title"],
        "body": row["body"] or "",
        "priority": row["priority"],
        "due_date": due,
        "tags": _tags_load(row["tags"]),
        "status": row["status"],
        "needs_review": bool(row["needs_review"]),
        "review_reason": row["review_reason"],
        "source": row["source"],
        "confidence": row["confidence"],
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
    }


def _audit_status(item, source: str) -> str:
    reason = item.review_reason or ""
    if source in {"error", "schema"} or reason in {"INVALID_JSON", "SCHEMA_MISMATCH"}:
        return "error"
    if item.needs_review:
        return "needs_review"
    return "success"


def _input_text() -> str:
    data = request.get_json(silent=True) or {}
    return (data.get("text") or data.get("transcript") or request.form.get("text") or "").strip()


def _save_item(conn, text: str, item, source: str, meta: dict) -> dict:
    now = _now()
    due = item.due_date.isoformat() if item.due_date else None
    cur = conn.execute(
        """
        INSERT INTO items (
            item_type, title, body, priority, due_date, tags, status,
            needs_review, review_reason, source, confidence, created_at, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, 'open', ?, ?, ?, ?, ?, ?)
        """,
        (
            item.item_type,
            item.title,
            item.body,
            item.priority,
            due,
            _tags_dump(item.tags),
            1 if item.needs_review else 0,
            item.review_reason,
            source,
            float(item.confidence or 0.0),
            now,
            now,
        ),
    )
    item_id = cur.lastrowid
    payload = item.to_dict()
    payload["id"] = item_id
    payload["status"] = "open"
    conn.execute(
        """
        INSERT INTO audit_runs (
            item_id, ts, source, status, input_text, result_json,
            error, confidence, needs_review
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            item_id,
            now,
            source,
            _audit_status(item, source),
            text,
            json.dumps(payload, ensure_ascii=False),
            item.review_reason or (meta.get("error") or None),
            float(item.confidence or 0.0),
            1 if item.needs_review else 0,
        ),
    )
    conn.commit()
    return payload


def _load_item(conn, item_id: int):
    return conn.execute("SELECT * FROM items WHERE id = ?", (item_id,)).fetchone()


@tasks_bp.route("/capture", methods=["GET"])
def capture_form():
    if not _allowed():
        return jsonify({"error": "unauthorized"}), 401
    conn = connect_db()
    try:
        recent = [
            _item_dict(row)
            for row in conn.execute(
                "SELECT * FROM items ORDER BY created_at DESC LIMIT 5"
            )
        ]
    finally:
        conn.close()
    return render_template("tasks/capture.html", recent=recent)


@tasks_bp.route("/capture", methods=["POST"])
def capture():
    if not _allowed():
        return jsonify({"error": "unauthorized"}), 401

    text = _input_text()
    if not text:
        return jsonify({"error": "text is required"}), 400

    conn = connect_db()
    try:
        item, source, meta = extract_item(text)
        saved = _save_item(conn, text, item, source, meta)
    except Exception as e:
        logger.exception("capture: failed")
        conn.close()
        return jsonify({"source": "error", "error": f"{type(e).__name__}: {e}"}), 200
    conn.close()

    if not _wants_json() and request.form.get("text"):
        if saved["needs_review"]:
            return redirect(url_for("tasks.review", item_id=saved["id"]))
        return redirect(url_for("tasks.tasks_list"))

    return jsonify({"source": source, "item": saved, "meta": meta}), 200


@tasks_bp.route("/tasks", methods=["GET"])
def tasks_list():
    if not _allowed():
        return jsonify({"error": "unauthorized"}), 401

    status = (request.args.get("status") or "all").strip()
    review_only = request.args.get("needs_review") == "1" or status == "needs_review"
    conn = connect_db()
    try:
        sql = "SELECT * FROM items"
        clauses = []
        params: list = []
        if status in {"open", "done"}:
            clauses.append("status = ?")
            params.append(status)
        if review_only:
            clauses.append("needs_review = 1")
        if clauses:
            sql += " WHERE " + " AND ".join(clauses)
        sql += " ORDER BY id DESC"
        rows = conn.execute(sql, params).fetchall()
        items = [_item_dict(row) for row in rows]
    finally:
        conn.close()

    if _wants_json():
        return jsonify({"items": items}), 200
    return render_template("tasks/list.html", items=items, status=status)


@tasks_bp.route("/tasks/<int:item_id>/done", methods=["POST"])
def mark_done(item_id: int):
    if not _allowed():
        return jsonify({"error": "unauthorized"}), 401

    conn = connect_db()
    try:
        row = _load_item(conn, item_id)
        if row is None:
            return jsonify({"error": "not found"}), 404
        now = _now()
        conn.execute(
            "UPDATE items SET status = 'done', updated_at = ? WHERE id = ?",
            (now, item_id),
        )
        conn.commit()
        saved = _item_dict(_load_item(conn, item_id))
    finally:
        conn.close()

    if not _wants_json():
        return redirect(url_for("tasks.tasks_list"))
    return jsonify({"item": saved}), 200


@tasks_bp.route("/tasks/<int:item_id>/review", methods=["GET", "POST"])
def review(item_id: int):
    if not _allowed():
        return jsonify({"error": "unauthorized"}), 401

    conn = connect_db()
    try:
        row = _load_item(conn, item_id)
        if row is None:
            return jsonify({"error": "not found"}), 404

        if request.method == "POST":
            now = _now()
            incoming = request.get_json(silent=True) or {}
            title = (request.form.get("title") or incoming.get("title") or row["title"] or "").strip()
            priority = (request.form.get("priority") or incoming.get("priority") or row["priority"])
            if priority not in {"low", "medium", "high"}:
                priority = row["priority"]
            conn.execute(
                """
                UPDATE items
                   SET title = ?,
                       priority = ?,
                       needs_review = 0,
                       review_reason = NULL,
                       updated_at = ?
                 WHERE id = ?
                """,
                (title, priority, now, item_id),
            )
            conn.commit()
            saved = _item_dict(_load_item(conn, item_id))
        else:
            saved = _item_dict(row)
    finally:
        conn.close()

    if request.method == "POST" and not _wants_json() and request.form:
        return redirect(url_for("tasks.tasks_list"))
    if request.method == "GET" and not _wants_json():
        return render_template("tasks/review.html", item=saved)
    return jsonify({"item": saved}), 200


@tasks_bp.route("/journal", methods=["GET"])
def journal():
    if not _allowed():
        return jsonify({"error": "unauthorized"}), 401

    conn = connect_db()
    try:
        rows = conn.execute(
            "SELECT * FROM audit_runs ORDER BY id DESC LIMIT 200"
        ).fetchall()
        runs = [dict(row) for row in rows]
    finally:
        conn.close()

    if _wants_json():
        return jsonify({"runs": runs}), 200
    return render_template("tasks/journal.html", runs=runs)
