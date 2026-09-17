"""In-app notification center + reminder scheduling.

Notifications are rows in SQLite (``notifications`` table), so the bell, the
notification center and any future provider all read the same state and
nothing is lost on restart.

Reminders are driven by a small scheduling pass — :func:`check_due_reminders`
— that the background worker runs every ~30 s (and the UI may run on load).
The "last reminder" timestamp is claimed with an atomic conditional UPDATE,
so a due reminder can never be sent twice even if two processes race.

Keeping the scheduling logic in a plain function (no Streamlit imports) is
what makes it possible to swap in another notification provider later:
create the row here, deliver it anywhere.
"""
from __future__ import annotations

from datetime import datetime, timedelta

import utils.date_utils as du
import utils.time_utils as tu
from database import db
from services import settings_service

REMINDER_LIKE = ("reminder", "escalation", "overdue")


# ------------------------------------------------------------ creation / UI
def create_notification(
    ntype: str,
    title: str,
    body: str = "",
    task_id: str | None = None,
    at: datetime | None = None,
) -> int:
    at = at or du.now()
    cur = db.execute(
        "INSERT INTO notifications (ntype, title, body, task_id, created_at) VALUES (?, ?, ?, ?, ?)",
        (ntype, title, body, task_id, du.iso_dt(at)),
    )
    return int(cur.lastrowid or 0)


def list_recent(limit: int = 30, include_dismissed: bool = False) -> list[dict]:
    sql = "SELECT * FROM notifications"
    if not include_dismissed:
        sql += " WHERE is_dismissed = 0"
    sql += " ORDER BY id DESC LIMIT ?"
    return [dict(r) for r in db.query(sql, (limit,))]


def unread_count() -> int:
    row = db.query_one(
        "SELECT COUNT(*) AS c FROM notifications WHERE is_read = 0 AND is_dismissed = 0"
    )
    return int(row["c"]) if row else 0


def mark_read(notification_id: int | None = None) -> None:
    """Mark one notification (or all, when *notification_id* is None) as read."""
    if notification_id is None:
        db.execute("UPDATE notifications SET is_read = 1 WHERE is_read = 0")
    else:
        db.execute("UPDATE notifications SET is_read = 1 WHERE id = ?", (notification_id,))


def dismiss(notification_id: int) -> None:
    db.execute("UPDATE notifications SET is_dismissed = 1, is_read = 1 WHERE id = ?", (notification_id,))


def snooze(notification_id: int | None, task_id: str | None, until: datetime) -> None:
    """Defer the notification and (for reminder-like types) the task's reminders."""
    until_iso = du.iso_dt(until)
    if notification_id is not None:
        db.execute("UPDATE notifications SET is_read = 1, snooze_until = ? WHERE id = ?", (until_iso, notification_id))
    if task_id:
        db.execute("UPDATE tasks SET snooze_until = ? WHERE id = ?", (until_iso, task_id))


def clear_snooze(task_id: str) -> None:
    db.execute("UPDATE tasks SET snooze_until = NULL WHERE id = ?", (task_id,))


# ------------------------------------------------------------ scheduler pass
def _active_reminder_tasks() -> list[dict]:
    rows = db.query(
        "SELECT * FROM tasks WHERE status = 'active' AND reminder_enabled = 1 ORDER BY id"
    )
    return [dict(r) for r in rows]


def check_due_reminders(now_dt: datetime | None = None) -> list[dict]:
    """Inspect active tasks and emit any notifications that are due.

    Returns the list of notifications created by this pass.
    """
    now_dt = now_dt or du.now()
    settings = settings_service.get_section("notifications")
    if not settings.get("enabled", True):
        return []

    created: list[dict] = []
    quiet = du.in_quiet_hours(
        now_dt,
        settings.get("quiet_start", "22:00"),
        settings.get("quiet_end", "07:00"),
    )
    high_only = bool(settings.get("high_only", False))
    default_interval = int(settings.get("default_interval", 7200))
    high_interval = int(settings.get("high_priority_interval", 7200))
    threshold_hours = float(settings.get("long_task_threshold_hours", 4))

    for task in _active_reminder_tasks():
        base = du.parse_dt(task.get("start_time")) or du.parse_dt(task.get("created_at"))
        if base is None:
            continue
        if high_only and task["priority"] not in ("high", "immediate"):
            continue
        snoozed_until = du.parse_dt(task.get("snooze_until"))
        if snoozed_until is not None and snoozed_until > now_dt:
            continue

        # --- periodic reminder (default 2h; every 3rd one is escalated) ---
        is_high = task["priority"] in ("high", "immediate")
        interval = int(
            task.get("reminder_interval")
            or (high_interval if is_high else default_interval)
        )
        last = du.parse_dt(task.get("last_reminder_at")) or base
        if now_dt >= last + timedelta(seconds=interval):
            if not quiet:
                with db.transaction() as conn:
                    cur = conn.execute(
                        "UPDATE tasks SET last_reminder_at = ?, reminders_sent = reminders_sent + 1, "
                        "escalation_level = escalation_level + 1 "
                        "WHERE id = ? AND (last_reminder_at IS NULL OR last_reminder_at <= ?)",
                        (du.iso_dt(now_dt), task["id"], du.iso_dt(last)),
                    )
                    claimed = cur.rowcount > 0
                if claimed:
                    n = int(task["reminders_sent"]) + 1
                    escalated = n % 3 == 0
                    active_for = (now_dt - base).total_seconds()
                    title = "⚠️ Escalated reminder" if escalated else "🔔 Reminder"
                    body = (
                        f"“{task['title']}” — this task has been active "
                        f"for {tu.seconds_to_human(active_for)}."
                    )
                    nid = create_notification(
                        "escalation" if escalated else "reminder",
                        title,
                        body,
                        task["id"],
                        at=now_dt,
                    )
                    created.append(
                        {"id": nid, "task_id": task["id"],
                         "ntype": "escalation" if escalated else "reminder",
                         "escalated": escalated}
                    )
            # Inside quiet hours we deliberately do not advance the clock:
            # the reminder fires as soon as quiet hours are over.

        # --- long-running / estimate-exceeded warning (once per crossing) ---
        if not quiet and not task.get("estimate_warning_sent"):
            est = task.get("estimated_duration")
            active_for = (now_dt - base).total_seconds()
            limit = (est * 1.5) if est else threshold_hours * 3600
            if active_for > limit:
                db.execute("UPDATE tasks SET estimate_warning_sent = 1 WHERE id = ?", (task["id"],))
                if est:
                    body = (
                        f"“{task['title']}” has exceeded its estimated time "
                        f"({tu.seconds_to_human(est)}). It has been active "
                        f"for {tu.seconds_to_human(active_for)}."
                    )
                else:
                    body = (
                        f"“{task['title']}” has been active for "
                        f"{tu.seconds_to_human(active_for)} — longer than your "
                        f"long-task threshold."
                    )
                nid = create_notification("overdue", "⚠️ Long-running task", body, task["id"], at=now_dt)
                created.append({"id": nid, "task_id": task["id"], "ntype": "overdue", "escalated": False})

    return created


def maybe_daily_summary(now_dt: datetime | None = None) -> dict | None:
    """Emit the end-of-day summary notification once per day at summary_hour."""
    now_dt = now_dt or du.now()
    settings = settings_service.get_section("notifications")
    if not settings.get("enabled", True):
        return None
    today_key = du.date_str(now_dt.date())
    if settings_service.get_scalar("last_daily_summary") == today_key:
        return None
    if now_dt.hour * 60 + now_dt.minute < du.hhmm_to_minutes(settings.get("summary_hour", "21:00"), 21 * 60):
        return None
    if du.in_quiet_hours(now_dt, settings.get("quiet_start", "22:00"), settings.get("quiet_end", "07:00")):
        return None  # fire on the next cycle, outside quiet hours

    completed = db.query_one(
        "SELECT COUNT(*) AS c, COALESCE(SUM(actual_duration), 0) AS tracked "
        "FROM tasks WHERE status = 'completed' AND date(completed_at) = ?",
        (today_key,),
    )
    xp_today = db.query_one(
        "SELECT COALESCE(SUM(points), 0) AS x FROM xp_transactions WHERE date(created_at) = ?",
        (today_key,),
    )
    streak = db.query_one("SELECT current FROM streaks WHERE id = 1")
    n = int(completed["c"]) if completed else 0
    tracked_s = int(completed["tracked"]) if completed else 0
    xp = int(xp_today["x"]) if xp_today else 0
    streak_now = int(streak["current"]) if streak else 0
    lines = [
        f"Completed: {n}",
        f"XP earned: {xp}",
        f"Time focused: {tu.seconds_to_human(tracked_s, default='0m')}",
        f"Current streak: {streak_now} days",
    ]
    nid = create_notification("summary", "🌙 Daily summary", "\n".join(lines), at=now_dt)
    settings_service.set_scalar("last_daily_summary", today_key)
    return {"id": nid, "completed": n, "xp": xp, "tracked_seconds": tracked_s}
