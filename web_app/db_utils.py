"""Общие helpers для deals.db."""

from __future__ import annotations

import sqlite3

from extraction.audit.logger import ensure_table as ensure_audit_table

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
    ("catalog_project", "TEXT"),  # типовой проект каталога клееного бруса
    ("extraction_source", "TEXT"),  # llm | regex | merged — канал разбора транскрибации
)


def ensure_deal_columns(conn: sqlite3.Connection) -> None:
    deals_exists = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='deals'"
    ).fetchone()
    if deals_exists:
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

    ensure_audit_table(conn)


def connect_db(path: str = "deals.db") -> sqlite3.Connection:
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    ensure_deal_columns(conn)
    ensure_items_table(conn)
    ensure_audit_runs_table(conn)
    return conn


def ensure_items_table(conn: sqlite3.Connection) -> None:
    """Таблица items — задачи и заметки (вариант 1: Персональный помощник)."""
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS items (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            item_type TEXT NOT NULL DEFAULT 'note',      -- task / note
            title TEXT NOT NULL DEFAULT '',
            body TEXT DEFAULT '',
            priority TEXT NOT NULL DEFAULT 'medium',     -- low / medium / high
            due_date TEXT,                                -- ISO YYYY-MM-DD
            tags TEXT DEFAULT '',                         -- JSON-массив или CSV
            status TEXT NOT NULL DEFAULT 'open',         -- open / done
            needs_review INTEGER NOT NULL DEFAULT 0,
            review_reason TEXT,                           -- LOW_CONFIDENCE и др.
            source TEXT NOT NULL DEFAULT '',              -- llm / heuristic / fallback
            confidence REAL NOT NULL DEFAULT 0.0,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_items_status ON items(status);
        CREATE INDEX IF NOT EXISTS idx_items_review ON items(needs_review);
        CREATE INDEX IF NOT EXISTS idx_items_created ON items(created_at);
    """)
    conn.commit()


def ensure_audit_runs_table(conn: sqlite3.Connection) -> None:
    """Таблица audit_runs — журнал обработок по ТЗ варианта 1."""
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS audit_runs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            item_id INTEGER,
            ts TEXT NOT NULL,
            source TEXT NOT NULL,
            status TEXT NOT NULL,                         -- success / needs_review / error
            input_text TEXT,
            result_json TEXT,
            error TEXT,                                   -- LOW_CONFIDENCE | INVALID_JSON | SCHEMA_MISMATCH | AMBIGUOUS_INPUT
            confidence REAL,
            needs_review INTEGER NOT NULL DEFAULT 0
        );
        CREATE INDEX IF NOT EXISTS idx_audit_runs_item ON audit_runs(item_id);
        CREATE INDEX IF NOT EXISTS idx_audit_runs_ts ON audit_runs(ts);
        CREATE INDEX IF NOT EXISTS idx_audit_runs_status ON audit_runs(status);
    """)
    conn.commit()
