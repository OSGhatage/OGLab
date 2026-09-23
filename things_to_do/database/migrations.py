"""Versioned schema migrations.

Each migration is an ordered list of SQL statements applied exactly once,
tracked in the ``migrations`` table.  v1 is the initial schema; future
versions add ``ALTER TABLE`` / new-table statements as the app evolves —
existing databases upgrade in place instead of being recreated.
"""
from __future__ import annotations

import json
import sqlite3

import utils.date_utils as du
from database import schema

MIGRATIONS: dict[int, list[str]] = {
    1: list(schema.DDL),
}


def seed(conn: sqlite3.Connection) -> None:
    """Idempotently insert default settings, the achievement catalog and the streak row."""
    existing = {row[0] for row in conn.execute("SELECT key FROM settings").fetchall()}
    for key, value in schema.DEFAULT_SETTINGS.items():
        if key not in existing:
            conn.execute("INSERT INTO settings (key, value) VALUES (?, ?)", (key, json.dumps(value)))
    for key, name, description, icon, xp in schema.ACHIEVEMENT_CATALOG:
        conn.execute(
            "INSERT OR IGNORE INTO achievements (key, name, description, icon, xp) VALUES (?, ?, ?, ?, ?)",
            (key, name, description, icon, xp),
        )
    conn.execute("INSERT OR IGNORE INTO streaks (id, current, longest, last_date) VALUES (1, 0, 0, NULL)")


def migrate(conn: sqlite3.Connection) -> int:
    """Apply any pending migrations. Returns the resulting schema version."""
    conn.execute(
        "CREATE TABLE IF NOT EXISTS migrations (version INTEGER PRIMARY KEY, applied_at TEXT NOT NULL)"
    )
    applied = {row[0] for row in conn.execute("SELECT version FROM migrations").fetchall()}
    for version in sorted(MIGRATIONS):
        if version in applied:
            continue
        for statement in MIGRATIONS[version]:
            conn.execute(statement)
        conn.execute(
            "INSERT INTO migrations (version, applied_at) VALUES (?, ?)",
            (version, du.iso_dt(du.now())),
        )
        applied.add(version)
    seed(conn)
    conn.commit()
    return max(applied) if applied else 0
