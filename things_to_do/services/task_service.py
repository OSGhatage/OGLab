"""Task lifecycle: create, list, filter, complete, carry-forward.

All task mutations happen here so the UI and the background worker share
exactly the same behaviour.  Every function accepts an optional explicit
timestamp / reference date (``at=`` / ``ref=``) which is what lets the
tests — and the midnight roll-over — simulate other days deterministically.
"""
from __future__ import annotations

import json
import uuid
from datetime import date, datetime

import utils.date_utils as du
from database import db, migrations
from services import reward_service, settings_service, timer_service

PRIORITIES = ("immediate", "high", "medium", "low")
PRIORITY_META = {
    "immediate": {"label": "Immediate", "icon": "🚨", "rank": 0},
    "high": {"label": "High", "icon": "🔴", "rank": 1},
    "medium": {"label": "Medium", "icon": "🟠", "rank": 2},
    "low": {"label": "Low", "icon": "🟢", "rank": 3},
}
ACTIVE = "active"
COMPLETED = "completed"

_PRIORITY_ORDER = ("CASE priority WHEN 'immediate' THEN 0 WHEN 'high' THEN 1 "
                   "WHEN 'medium' THEN 2 ELSE 3 END")


def _norm_priority(value: str) -> str:
    return value if value in PRIORITIES else "medium"


def priority_label(value: str) -> str:
    meta = PRIORITY_META.get(value, PRIORITY_META["medium"])
    return f"{meta['icon']} {meta['label']}"


def log_event(conn, task_id: str, event_type: str, at: datetime, details: dict | None = None) -> None:
    conn.execute(
        "INSERT INTO task_events (task_id, event_type, occurred_at, details) VALUES (?, ?, ?, ?)",
        (task_id, event_type, du.iso_dt(at), json.dumps(details or {})),
    )


# ------------------------------------------------------------------- create
def create_task(
    title: str,
    description: str = "",
    priority: str = "medium",
    planned_date: date | str | None = None,
    estimated_minutes: float | int | None = 0,
    start_timer: bool = False,
    reminder_enabled: bool = True,
    at: datetime | None = None,
) -> tuple[str | None, str | None]:
    """Create a task. Returns ``(task_id, None)`` or ``(None, error_message)``."""
    at = at or du.now()
    title = (title or "").strip()
    if not title:
        return None, "Task title cannot be empty."
    priority = _norm_priority(priority)
    planned = du.parse_date(planned_date) or at.date()
    try:
        estimated_minutes = float(estimated_minutes or 0)
    except (TypeError, ValueError):
        estimated_minutes = 0
    estimated = int(estimated_minutes * 60) if estimated_minutes > 0 else None

    settings = settings_service.get_section("notifications")
    interval = int(
        settings.get("high_priority_interval", 7200)
        if priority in ("high", "immediate")
        else settings.get("default_interval", 7200)
    )
    task_id = uuid.uuid4().hex
    with db.transaction() as conn:
        conn.execute(
            "INSERT INTO tasks (id, title, description, priority, status, created_at, "
            "original_date, planned_date, last_active_date, estimated_duration, "
            "reminder_enabled, reminder_interval) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                task_id, title, (description or "").strip(), priority, ACTIVE,
                du.iso_dt(at), du.date_str(at.date()), du.date_str(planned),
                du.date_str(planned if planned >= at.date() else at.date()),
                estimated, 1 if reminder_enabled else 0, interval,
            ),
        )
        log_event(conn, task_id, "created", at,
                  {"planned_date": du.date_str(planned), "priority": priority})
    if start_timer:
        timer_service.start(task_id, at=at)
    return task_id, None


# -------------------------------------------------------------------- reads
def get_task(task_id: str) -> dict | None:
    row = db.query_one("SELECT * FROM tasks WHERE id = ?", (task_id,))
    return dict(row) if row else None


def get_events(task_id: str, limit: int = 20) -> list[dict]:
    rows = db.query(
        "SELECT * FROM task_events WHERE task_id = ? ORDER BY id DESC LIMIT ?",
        (task_id, limit),
    )
    return [dict(r) for r in rows]


def list_tasks(
    status: str | None = None,
    planned_date: date | str | None = None,
    keyword: str | None = None,
    priorities: list[str] | None = None,
    completed_from: date | str | None = None,
    completed_to: date | str | None = None,
    limit: int = 500,
) -> list[dict]:
    """Search/filter tasks across any status (All Tasks page)."""
    clauses: list[str] = []
    params: list = []
    if status in (ACTIVE, COMPLETED):
        clauses.append("status = ?")
        params.append(status)
    if planned_date:
        parsed = du.parse_date(planned_date)
        if parsed:
            clauses.append("planned_date = ?")
            params.append(du.date_str(parsed))
    if keyword and keyword.strip():
        clauses.append("(title LIKE ? OR description LIKE ?)")
        like = f"%{keyword.strip()}%"
        params.extend([like, like])
    if priorities:
        clauses.append(f"priority IN ({','.join('?' for _ in priorities)})")
        params.extend(priorities)
    if completed_from:
        clauses.append("date(completed_at) >= ?")
        params.append(du.date_str(du.parse_date(completed_from) or completed_from))
    if completed_to:
        clauses.append("date(completed_at) <= ?")
        params.append(du.date_str(du.parse_date(completed_to) or completed_to))
    where = (" WHERE " + " AND ".join(clauses)) if clauses else ""
    sql = (
        "SELECT * FROM tasks" + where +
        " ORDER BY CASE status WHEN 'active' THEN 0 ELSE 1 END, "
        f"{_PRIORITY_ORDER}, planned_date ASC, completed_at DESC LIMIT ?"
    )
    params.append(limit)
    return [dict(r) for r in db.query(sql, params)]


def get_due_tasks(ref: date | None = None) -> list[dict]:
    """Active tasks due (planned on or before *ref*) — the Today list."""
    ref = ref or du.today()
    rows = db.query(
        "SELECT * FROM tasks WHERE status = 'active' AND planned_date <= ? "
        f"ORDER BY {_PRIORITY_ORDER}, planned_date",
        (du.date_str(ref),),
    )
    return [dict(r) for r in rows]


def get_upcoming_tasks(ref: date | None = None) -> list[dict]:
    ref = ref or du.today()
    rows = db.query(
        "SELECT * FROM tasks WHERE status = 'active' AND planned_date > ? ORDER BY planned_date",
        (du.date_str(ref),),
    )
    return [dict(r) for r in rows]


def get_completed_between(start: date | None, end: date | None) -> list[dict]:
    clauses = ["status = 'completed'"]
    params: list = []
    if start:
        clauses.append("date(completed_at) >= ?")
        params.append(du.date_str(du.parse_date(start) or start))
    if end:
        clauses.append("date(completed_at) <= ?")
        params.append(du.date_str(du.parse_date(end) or end))
    rows = db.query(
        "SELECT * FROM tasks WHERE " + " AND ".join(clauses) + " ORDER BY completed_at DESC",
        params,
    )
    return [dict(r) for r in rows]


def count_completed_per_day(days: list[date]) -> dict[str, int]:
    out = {du.date_str(d): 0 for d in days}
    if not days:
        return out
    placeholders = ",".join("?" for _ in days)
    rows = db.query(
        f"SELECT date(completed_at) AS d, COUNT(*) AS c FROM tasks "
        f"WHERE status = 'completed' AND date(completed_at) IN ({placeholders}) GROUP BY d",
        [du.date_str(d) for d in days],
    )
    for row in rows:
        out[row["d"]] = int(row["c"])
    return out


# ----------------------------------------------------------------- updates
def set_priority(task_id: str, priority: str, at: datetime | None = None) -> bool:
    """Change priority. Rewards already stored are never touched."""
    priority = _norm_priority(priority)
    at = at or du.now()
    task = get_task(task_id)
    if task is None or task["priority"] == priority:
        return False
    with db.transaction() as conn:
        conn.execute("UPDATE tasks SET priority = ? WHERE id = ?", (priority, task_id))
        log_event(conn, task_id, "priority_changed", at,
                  {"from": task["priority"], "to": priority})
    return True


def set_description(task_id: str, description: str) -> None:
    db.execute("UPDATE tasks SET description = ? WHERE id = ?",
               ((description or "").strip(), task_id))


def set_reminder(task_id: str, enabled: bool, interval: int | None = None) -> None:
    if interval:
        db.execute(
            "UPDATE tasks SET reminder_enabled = ?, reminder_interval = ? WHERE id = ?",
            (1 if enabled else 0, int(interval), task_id),
        )
    else:
        db.execute("UPDATE tasks SET reminder_enabled = ? WHERE id = ?",
                   (1 if enabled else 0, task_id))


# ---------------------------------------------------------------- complete
def complete_task(task_id: str, at: datetime | None = None) -> dict:
    """Mark a task completed.

    Finalises the timer (or falls back to elapsed time), stores the
    completion timestamp/duration and awards the one-time XP reward.  The
    row is kept — completed records are never deleted.
    """
    at = at or du.now()
    task = get_task(task_id)
    if task is None:
        return {"ok": False, "error": "Task not found."}
    if task["status"] == COMPLETED:
        return {"ok": False, "error": "Task is already completed."}
    final = timer_service.finalize(task, at=at)
    with db.transaction() as conn:
        conn.execute(
            "UPDATE tasks SET status = 'completed', completed_at = ?, actual_duration = ?, "
            "duration_basis = ?, accumulated_ms = ?, is_paused = 0, snooze_until = NULL, "
            "last_active_date = ? WHERE id = ?",
            (du.iso_dt(at), final["actual"], final["basis"], final["acc_ms"],
             du.date_str(at.date()), task_id),
        )
        log_event(conn, task_id, "completed", at,
                  {"actual_duration": final["actual"], "basis": final["basis"]})
    reward = reward_service.award_completion(task_id, completed_dt=at)
    return {"ok": True, "task_id": task_id, "title": task["title"],
            "actual": final["actual"], "basis": final["basis"], "reward": reward}


# ----------------------------------------------------------- carry-forward
def roll_day(ref: date | None = None) -> int:
    """Carry uncompleted tasks forward to *ref* (today when called from the UI).

    The same row is updated — planned date moved, counter incremented, event
    logged — so a task keeps one stable ID, its original creation date is
    preserved and no duplicate copies are ever created.  Re-running on the
    same day is a no-op.  Only *overdue* tasks (planned before *ref*) roll;
    tasks planned for the future stay put.
    """
    ref = ref or du.today()
    ref_key = du.date_str(ref)
    now_dt = du.now()
    with db.transaction() as conn:
        rows = conn.execute(
            "SELECT id, planned_date FROM tasks WHERE status = 'active' AND planned_date < ?",
            (ref_key,),
        ).fetchall()
        for row in rows:
            conn.execute(
                "UPDATE tasks SET planned_date = ?, carry_forward_count = carry_forward_count + 1, "
                "last_active_date = ? WHERE id = ?",
                (ref_key, ref_key, row["id"]),
            )
            log_event(conn, row["id"], "carried_forward", now_dt,
                      {"from": row["planned_date"], "to": ref_key})
    return len(rows)


# ------------------------------------------------------------------- stats
def today_stats(ref: date | None = None) -> dict:
    ref = ref or du.today()
    ref_key = du.date_str(ref)
    completed_row = db.query_one(
        "SELECT COUNT(*) AS c, COALESCE(SUM(actual_duration), 0) AS tracked, "
        "COALESCE(SUM(CASE WHEN estimated_duration IS NOT NULL THEN estimated_duration ELSE 0 END), 0) AS est "
        "FROM tasks WHERE status = 'completed' AND date(completed_at) = ?",
        (ref_key,),
    )
    active_row = db.query_one(
        "SELECT COUNT(*) AS c FROM tasks WHERE status = 'active' AND planned_date <= ?",
        (ref_key,),
    )
    high_row = db.query_one(
        "SELECT COUNT(*) AS c FROM tasks WHERE status = 'completed' AND date(completed_at) = ? "
        "AND priority IN ('high', 'immediate')",
        (ref_key,),
    )
    completed = int(completed_row["c"]) if completed_row else 0
    active = int(active_row["c"]) if active_row else 0
    total = completed + active
    return {
        "ref": ref,
        "planned_total": total,
        "completed_today": completed,
        "active_now": active,
        "remaining": active,
        "rate": (100.0 * completed / total) if total else None,
        "tracked_seconds": int(completed_row["tracked"]) if completed_row else 0,
        "estimated_seconds": int(completed_row["est"]) if completed_row else 0,
        "high_completed": int(high_row["c"]) if high_row else 0,
    }


# ------------------------------------------------------------------ danger
def reset_all() -> None:
    """Wipe all data and re-seed defaults (Settings → Danger zone)."""
    with db.transaction() as conn:
        for table in ("task_events", "notifications", "xp_transactions", "tasks",
                      "achievements", "streaks", "settings"):
            conn.execute(f"DELETE FROM {table}")
        migrations.seed(conn)
