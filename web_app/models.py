"""CRM-модели: статусы сделок и журнал действий (ActionLog)."""

from __future__ import annotations

import sqlite3
from typing import Any

# Пайплайн: new → incomplete → kp_ready → kp_sent → completed → lost
DEAL_STATUSES: tuple[str, ...] = (
    "new",
    "incomplete",
    "kp_ready",
    "kp_sent",
    "completed",
    "lost",
)

STATUS_LABELS: dict[str, str] = {
    "new": "Новая",
    "incomplete": "Неполные данные",
    "kp_ready": "КП готово",
    "kp_sent": "КП отправлено",
    "completed": "Завершена",
    "lost": "Проиграна",
}

# Старые значения → новый пайплайн
LEGACY_STATUS_MAP: dict[str, str] = {
    "sent": "kp_sent",
    "answered": "kp_sent",
    "won": "completed",
    "approved": "kp_ready",
    "stalled": "incomplete",
}

# Терминальные: автологика их не трогает
TERMINAL_STATUSES = frozenset({"completed", "lost"})

ACTION_LABELS: dict[str, str] = {
    "created": "Сделка создана",
    "updated": "Данные обновлены",
    "status_changed": "Статус изменён",
    "kp_generated": "КП сгенерировано",
    "kp_approved": "КП утверждено",
    "kp_sent": "КП отправлено клиенту",
    "kp_send_failed": "Ошибка отправки КП",
    "telegram_bound": "Telegram привязан",
    "reminder_sent": "Напоминание менеджеру",
    "bitrix_exported": "Выгружено в Bitrix24",
    "bitrix_failed": "Ошибка выгрузки в Bitrix24",
}


def normalize_status(raw: Any) -> str:
    text = (str(raw).strip() if raw is not None else "") or "new"
    text = LEGACY_STATUS_MAP.get(text, text)
    if text not in DEAL_STATUSES:
        return "new"
    return text


def status_label(raw: Any) -> str:
    key = normalize_status(raw)
    return STATUS_LABELS.get(key, key)


def ensure_action_log_table(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS action_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            deal_id INTEGER NOT NULL,
            action TEXT NOT NULL,
            detail TEXT,
            user_id INTEGER,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (deal_id) REFERENCES deals (id)
        )
        """
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_action_log_deal ON action_log (deal_id, id DESC)"
    )
    conn.commit()


def migrate_deal_statuses(conn: sqlite3.Connection) -> int:
    """Переписывает устаревшие status в deals. Возвращает число обновлённых строк."""
    updated = 0
    for old, new in LEGACY_STATUS_MAP.items():
        cur = conn.execute(
            "UPDATE deals SET status = ? WHERE status = ?",
            (new, old),
        )
        updated += cur.rowcount or 0
    if updated:
        conn.commit()
    return updated


def log_action(
    conn: sqlite3.Connection,
    *,
    deal_id: int,
    action: str,
    detail: str | None = None,
    user_id: int | None = None,
) -> None:
    ensure_action_log_table(conn)
    conn.execute(
        """
        INSERT INTO action_log (deal_id, action, detail, user_id)
        VALUES (?, ?, ?, ?)
        """,
        (deal_id, action, (detail or "").strip() or None, user_id),
    )


def list_actions(
    conn: sqlite3.Connection,
    deal_id: int,
    *,
    limit: int = 50,
) -> list[dict[str, Any]]:
    ensure_action_log_table(conn)
    rows = conn.execute(
        """
        SELECT id, deal_id, action, detail, user_id, created_at
        FROM action_log
        WHERE deal_id = ?
        ORDER BY id DESC
        LIMIT ?
        """,
        (deal_id, limit),
    ).fetchall()
    out: list[dict[str, Any]] = []
    for row in rows:
        item = dict(row)
        item["action_label"] = ACTION_LABELS.get(item["action"], item["action"])
        out.append(item)
    return out


def status_after_etalon(*, can_generate_kp: bool, current: str | None = None) -> str:
    """Автостатус по заполнению эталона (не трогает completed/lost/kp_sent/kp_ready)."""
    cur = normalize_status(current)
    if cur in TERMINAL_STATUSES or cur in {"kp_sent", "kp_ready"}:
        if not can_generate_kp and cur == "kp_ready":
            return "incomplete"
        return cur
    return "new" if can_generate_kp else "incomplete"


def status_after_kp_ready(current: str | None = None) -> str:
    cur = normalize_status(current)
    if cur in TERMINAL_STATUSES:
        return cur
    return "kp_ready"


def status_after_kp_sent(current: str | None = None) -> str:
    cur = normalize_status(current)
    if cur in TERMINAL_STATUSES:
        return cur
    return "kp_sent"
