"""SQLite connection handling for Things To Do.

One connection per thread (thread-local) with the WAL journal mode, so the
Streamlit UI and the background reminder worker can safely share the same
database file.  All SQL lives in this module plus the services layer — the
UI never writes SQL directly.

The database location defaults to ``data/tasks.db`` next to the app and can
be overridden with the ``TTD_DB_PATH`` environment variable (the test suite
uses this to stay on a throwaway database).
"""
from __future__ import annotations

import os
import sqlite3
import threading
from contextlib import contextmanager
from pathlib import Path

APP_ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = APP_ROOT / "data"

_local = threading.local()
_init_lock = threading.Lock()
_initialized: set[str] = set()


def db_path() -> Path:
    override = os.environ.get("TTD_DB_PATH")
    if override:
        return Path(override)
    return DATA_DIR / "tasks.db"


def _configure(conn: sqlite3.Connection) -> None:
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("PRAGMA busy_timeout=8000")


def _ensure_migrated(conn: sqlite3.Connection, path: Path) -> None:
    key = str(path)
    with _init_lock:
        if key in _initialized:
            return
        from database import migrations

        migrations.migrate(conn)
        _initialized.add(key)


def get_conn() -> sqlite3.Connection:
    """This thread's connection (created + migrated on first use)."""
    path = db_path()
    conn = getattr(_local, "conn", None)
    if conn is None or getattr(_local, "path", None) != path:
        if conn is not None:
            try:
                conn.close()
            except sqlite3.Error:
                pass
        path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(str(path), timeout=10)
        _configure(conn)
        _local.conn = conn
        _local.path = path
        _ensure_migrated(conn, path)
    return conn


def init_db() -> sqlite3.Connection:
    """Make sure the database exists, is migrated and seeded. Returns the connection."""
    return get_conn()


@contextmanager
def transaction():
    """A single write transaction on this thread's connection."""
    conn = get_conn()
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise


def execute(sql: str, params: tuple = ()) -> sqlite3.Cursor:
    conn = get_conn()
    cur = conn.execute(sql, params)
    conn.commit()
    return cur


def executemany(sql: str, seq) -> sqlite3.Cursor:
    conn = get_conn()
    cur = conn.executemany(sql, seq)
    conn.commit()
    return cur


def query(sql: str, params: tuple = ()) -> list[sqlite3.Row]:
    return get_conn().execute(sql, params).fetchall()


def query_one(sql: str, params: tuple = ()) -> sqlite3.Row | None:
    return get_conn().execute(sql, params).fetchone()


def reset_thread_local() -> None:
    """Close this thread's connection (used by the test suite)."""
    conn = getattr(_local, "conn", None)
    if conn is not None:
        try:
            conn.close()
        except sqlite3.Error:
            pass
    _local.conn = None
    _local.path = None
