"""Read/write of the application settings table (key -> JSON value).

Settings are always served deep-merged on top of :data:`schema.DEFAULT_SETTINGS`
so new options introduced by a later version appear automatically on old
databases.  Everything works out of the box without touching this page.
"""
from __future__ import annotations

import copy
import json

from database import db, schema


def _merge(base: dict, override: dict) -> dict:
    out = copy.deepcopy(base)
    for key, value in (override or {}).items():
        if isinstance(value, dict) and isinstance(out.get(key), dict):
            out[key] = _merge(out[key], value)
        else:
            out[key] = value
    return out


def get_all() -> dict:
    rows = db.query("SELECT key, value FROM settings")
    stored: dict = {}
    for row in rows:
        try:
            stored[row["key"]] = json.loads(row["value"])
        except (json.JSONDecodeError, TypeError):
            continue
    return _merge(schema.DEFAULT_SETTINGS, stored)


def get_section(section: str) -> dict:
    value = get_all().get(section, {})
    return value if isinstance(value, dict) else {}


def set_section(section: str, value: dict) -> None:
    with db.transaction() as conn:
        conn.execute(
            "INSERT INTO settings (key, value) VALUES (?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (section, json.dumps(value)),
        )


def get_scalar(key: str, default=None):
    row = db.query_one("SELECT value FROM settings WHERE key = ?", (key,))
    if row is None:
        return default
    try:
        return json.loads(row["value"])
    except (json.JSONDecodeError, TypeError):
        return default


def set_scalar(key: str, value) -> None:
    with db.transaction() as conn:
        conn.execute(
            "INSERT INTO settings (key, value) VALUES (?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (key, json.dumps(value)),
        )
