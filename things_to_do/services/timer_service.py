"""Start / Pause / Resume / Complete workflow for task timers.

A task's tracked time is stored as:

* ``start_time``     – when the current running segment started (ISO)
* ``paused_at``      – when the task was paused (ISO), NULL while running
* ``is_paused``      – 0 / 1
* ``accumulated_ms`` – milliseconds accumulated from finished segments

Tracked time = ``accumulated_ms + (now - start_time)`` while running.
Milliseconds are used internally so pause/resume accumulates exactly.
"""
from __future__ import annotations

import json
from datetime import datetime

import utils.date_utils as du
from database import db


def _ms(delta: datetime) -> int:
    return max(0, int(delta.total_seconds() * 1000))


def _log(task_id: str, event_type: str, at: datetime) -> None:
    with db.transaction() as conn:
        conn.execute(
            "INSERT INTO task_events (task_id, event_type, occurred_at, details) VALUES (?, ?, ?, ?)",
            (task_id, event_type, du.iso_dt(at), json.dumps({})),
        )


def state(task: dict) -> str:
    """``'idle'`` | ``'running'`` | ``'paused'``."""
    if not task.get("start_time"):
        return "idle"
    return "paused" if task.get("is_paused") else "running"


def elapsed_ms(task: dict, at: datetime | None = None) -> int:
    """Tracked milliseconds so far (frozen at the pause point when paused)."""
    at = at or du.now()
    acc = int(task.get("accumulated_ms") or 0)
    if task.get("start_time") and not task.get("is_paused"):
        start = du.parse_dt(task["start_time"])
        if start is not None:
            acc += _ms(at - start)
    return acc


def start(task_id: str, at: datetime | None = None) -> bool:
    at = at or du.now()
    with db.transaction() as conn:
        row = conn.execute("SELECT id FROM tasks WHERE id = ?", (task_id,)).fetchone()
        if row is None:
            return False
        cur = conn.execute(
            "UPDATE tasks SET start_time = ?, is_paused = 0, last_active_date = ? "
            "WHERE id = ? AND start_time IS NULL",
            (du.iso_dt(at), du.date_str(at.date()), task_id),
        )
        started = cur.rowcount > 0
    if started:
        _log(task_id, "started", at)
    return started


def pause(task_id: str, at: datetime | None = None) -> bool:
    at = at or du.now()
    with db.transaction() as conn:
        row = conn.execute(
            "SELECT accumulated_ms, start_time, is_paused FROM tasks WHERE id = ?", (task_id,)
        ).fetchone()
        if row is None or row["is_paused"] or not row["start_time"]:
            return False
        start = du.parse_dt(row["start_time"])
        extra = _ms(at - start) if start else 0
        conn.execute(
            "UPDATE tasks SET accumulated_ms = accumulated_ms + ?, paused_at = ?, is_paused = 1 WHERE id = ?",
            (extra, du.iso_dt(at), task_id),
        )
    _log(task_id, "paused", at)
    return True


def resume(task_id: str, at: datetime | None = None) -> bool:
    at = at or du.now()
    with db.transaction() as conn:
        row = conn.execute("SELECT is_paused FROM tasks WHERE id = ?", (task_id,)).fetchone()
        if row is None or not row["is_paused"]:
            return False
        conn.execute(
            "UPDATE tasks SET start_time = ?, paused_at = NULL, is_paused = 0 WHERE id = ?",
            (du.iso_dt(at), task_id),
        )
    _log(task_id, "resumed", at)
    return True


def finalize(task: dict, at: datetime) -> dict:
    """Compute the final duration when a task is completed.

    Returns ``{"actual": seconds, "basis": "tracked" | "elapsed", "acc_ms": int}``.

    * **tracked**  – the task used the timer; duration = sum of running segments.
    * **elapsed**  – no timer was ever started; duration falls back to
      creation → completion so a number is still shown, clearly labelled as
      *elapsed (untracked)* in the UI.
    """
    if task.get("start_time"):
        acc = elapsed_ms(task, at=at)
        return {"actual": acc // 1000, "basis": "tracked", "acc_ms": acc}
    created = du.parse_dt(task.get("created_at"))
    if created is None:
        return {"actual": 0, "basis": "elapsed", "acc_ms": 0}
    seconds = max(0, int((at - created).total_seconds()))
    return {"actual": seconds, "basis": "elapsed", "acc_ms": 0}
