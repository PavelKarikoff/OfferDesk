"""Общие helpers для deals.db."""

from __future__ import annotations

import sqlite3

DEAL_EXTRA_COLUMNS: tuple[tuple[str, str], ...] = (
    ("budget", "TEXT"),
    ("area", "TEXT"),
    ("material", "TEXT"),
    ("timeline", "TEXT"),
    ("funding_source", "TEXT"),
    ("plot", "TEXT"),
    ("tk_cost", "INTEGER"),  # ориентировочная стоимость тёплого контура, ₽
    ("delivery_status", "TEXT"),
    ("delivery_error", "TEXT"),
    ("telegram_chat_id", "TEXT"),  # числовой chat_id для отправки КП ботом
    ("telegram_outbox", "TEXT"),  # JSON очередь отправки КП (когда VPS не достучится до Telegram)
    ("catalog_project", "TEXT"),  # типовой проект каталога «Дом Форест»
    ("extraction_source", "TEXT"),  # llm | regex | merged — канал разбора транскрибации
)


def ensure_deal_columns(conn: sqlite3.Connection) -> None:
    existing = {row[1] for row in conn.execute("PRAGMA table_info(deals)").fetchall()}
    for name, col_type in DEAL_EXTRA_COLUMNS:
        if name not in existing:
            conn.execute(f"ALTER TABLE deals ADD COLUMN {name} {col_type}")
    conn.commit()

    # Пересчёт Стоимости ТК по площади (стандарт 75 000 ₽/м²)
    try:
        from pricing import calc_tk_cost, is_timber_material

        rows = conn.execute("SELECT id, area, tk_cost, material FROM deals").fetchall()
        for row in rows:
            if isinstance(row, sqlite3.Row):
                deal_id, area, current, material = row["id"], row["area"], row["tk_cost"], row["material"]
            else:
                deal_id, area, current, material = row[0], row[1], row[2], row[3]
            if is_timber_material(material):
                continue
            cost = calc_tk_cost(area)
            if cost is not None and current != cost:
                conn.execute("UPDATE deals SET tk_cost = ? WHERE id = ?", (cost, deal_id))
        conn.commit()
    except Exception:
        pass

    try:
        from models import ensure_action_log_table, migrate_deal_statuses

        ensure_action_log_table(conn)
        migrate_deal_statuses(conn)
    except Exception:
        pass


def connect_db(path: str = "deals.db") -> sqlite3.Connection:
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    ensure_deal_columns(conn)
    return conn
