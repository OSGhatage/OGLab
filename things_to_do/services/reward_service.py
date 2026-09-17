"""XP, levels, streaks and achievements.

Every reward is an immutable row in ``xp_transactions`` with a unique
``reward_key``.  That is the anti-gaming backbone:

* a completion is rewarded **exactly once** (``completion:<task_id>``);
* bonuses are likewise one-shot per task;
* changing a task's priority *after* completion never changes stored XP;
* deleting and recreating a task creates a new ID and therefore new (empty)
  reward history — old rewards cannot be replayed;
* rewards are stored separately from the task record, so task edits can
  never inflate the total.
"""
from __future__ import annotations

import sqlite3
from datetime import date, datetime, timedelta

import utils.date_utils as du
from database import db, schema
from services import notification_service, settings_service

STREAK_NOTIFY_AT = (2, 3, 5, 7, 14, 30)


# ------------------------------------------------------------------ queries
def total_xp() -> int:
    row = db.query_one("SELECT COALESCE(SUM(points), 0) AS x FROM xp_transactions")
    return int(row["x"]) if row else 0


def xp_for_day(day: date) -> int:
    row = db.query_one(
        "SELECT COALESCE(SUM(points), 0) AS x FROM xp_transactions WHERE date(created_at) = ?",
        (du.date_str(day),),
    )
    return int(row["x"]) if row else 0


def xp_since(dt: datetime) -> int:
    row = db.query_one(
        "SELECT COALESCE(SUM(points), 0) AS x FROM xp_transactions WHERE created_at >= ?",
        (du.iso_dt(dt),),
    )
    return int(row["x"]) if row else 0


def xp_today(now_dt: datetime | None = None) -> int:
    return xp_for_day((now_dt or du.now()).date())


def xp_this_week(now_dt: datetime | None = None) -> int:
    now_dt = now_dt or du.now()
    monday = now_dt.date() - timedelta(days=now_dt.weekday())
    return xp_since(datetime.combine(monday, datetime.min.time()))


def xp_this_month(now_dt: datetime | None = None) -> int:
    now_dt = now_dt or du.now()
    first = now_dt.date().replace(day=1)
    return xp_since(datetime.combine(first, datetime.min.time()))


def xp_by_day(days: int, ref: date | None = None) -> dict[str, int]:
    ref = ref or du.today()
    return {du.date_str(du.add_days(ref, -i)): xp_for_day(du.add_days(ref, -i)) for i in range(days - 1, -1, -1)}


def level_info() -> dict:
    """Current level, progress toward the next and remaining XP."""
    settings = settings_service.get_section("rewards")
    thresholds = sorted(
        int(t) for t in settings.get("level_thresholds", []) if isinstance(t, (int, float)) and t >= 0
    )
    if not thresholds or thresholds[0] != 0:
        thresholds = [0] + thresholds
    total = total_xp()
    # Extend the ladder indefinitely if the player outgrew the configured list.
    while total >= thresholds[-1]:
        step = max(500, thresholds[-1] - thresholds[-2]) if len(thresholds) >= 2 else 1000
        thresholds.append(thresholds[-1] + step)
    level = 1
    for i, t in enumerate(thresholds):
        if total >= t:
            level = i + 1
    floor = thresholds[level - 1]
    nxt = thresholds[level]
    remaining = max(0, nxt - total)
    span = nxt - floor
    pct = 0.0 if span <= 0 else min(100.0, 100.0 * (total - floor) / span)
    return {"level": level, "total": total, "floor": floor, "next": nxt,
            "remaining": remaining, "pct": round(pct, 1)}


def get_streak() -> dict:
    row = db.query_one("SELECT current, longest, last_date FROM streaks WHERE id = 1")
    return {
        "current": int(row["current"]) if row else 0,
        "longest": int(row["longest"]) if row else 0,
        "last_date": row["last_date"] if row else None,
    }


def update_streak(day: date) -> dict:
    """Record a completion on *day*; a day with >= 1 completed task counts."""
    with db.transaction() as conn:
        row = conn.execute("SELECT current, longest, last_date FROM streaks WHERE id = 1").fetchone()
        current = int(row["current"]) if row else 0
        longest = int(row["longest"]) if row else 0
        last = row["last_date"] if row else None
        day_key = du.date_str(day)
        if last == day_key:
            return {"current": current, "longest": longest, "changed": False}
        if last == du.date_str(du.add_days(day, -1)):
            current += 1
        else:
            current = 1
        longest = max(longest, current)
        conn.execute(
            "INSERT INTO streaks (id, current, longest, last_date) VALUES (1, ?, ?, ?) "
            "ON CONFLICT(id) DO UPDATE SET current = excluded.current, "
            "longest = excluded.longest, last_date = excluded.last_date",
            (current, longest, day_key),
        )
    if current in STREAK_NOTIFY_AT:
        notification_service.create_notification(
            "streak", "🔥 Streak", f"{current}-day streak — keep it going!", at=du.now()
        )
    return {"current": current, "longest": longest, "changed": True}


# ---------------------------------------------------------------- completion
def award_completion(task_id: str, completed_dt: datetime | None = None) -> dict:
    """Award the (one-time) completion reward for a freshly completed task.

    Must be called *after* the task row has been updated to completed.
    """
    completed_dt = completed_dt or du.now()
    row = db.query_one("SELECT * FROM tasks WHERE id = ?", (task_id,))
    if row is None:
        return {"total_xp": 0, "items": [], "streak": None, "achievements": []}
    task = dict(row)
    settings = settings_service.get_section("rewards")
    xp_values = settings.get("xp", {})
    bonuses = settings.get("bonuses", {})
    items: list[dict] = []

    def grant(kind: str, points: int, reason: str, key: str) -> None:
        if not points:
            return
        try:
            db.execute(
                "INSERT INTO xp_transactions (task_id, kind, points, reason, reward_key, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (task_id, kind, int(points), reason, key, du.iso_dt(completed_dt)),
            )
            items.append({"kind": kind, "points": int(points), "reason": reason})
        except sqlite3.IntegrityError:
            pass  # reward_key already exists → already rewarded (anti-gaming)

    label = task["priority"].capitalize()
    grant("completion", int(xp_values.get(task["priority"], 10)),
          f"{label} priority task completed", f"completion:{task_id}")

    est = task.get("estimated_duration")
    actual = task.get("actual_duration")
    if est and actual is not None and actual < est:
        grant("bonus", int(bonuses.get("before_estimate", 5)),
              "Completed before estimate", f"before_estimate:{task_id}")

    original = du.parse_date(task.get("original_date"))
    if original and completed_dt.date() == original:
        grant("bonus", int(bonuses.get("on_planned_date", 5)),
              "Completed on the planned date", f"on_planned_date:{task_id}")

    if task["priority"] in ("high", "immediate"):
        grant("bonus", int(bonuses.get("high_priority", 5)),
              "High-priority task completed", f"high_priority:{task_id}")

    milestone_n = int(settings.get("daily_milestone_count", 5))
    done_today = int(db.query_one(
        "SELECT COUNT(*) AS c FROM tasks WHERE status = 'completed' AND date(completed_at) = ?",
        (du.date_str(completed_dt.date()),),
    )["c"])
    if done_today == milestone_n:
        grant("milestone", int(settings.get("daily_milestone_xp", 10)),
              f"Daily milestone: {milestone_n} tasks completed today",
              f"milestone:daily:{du.date_str(completed_dt.date())}")

    streak = update_streak(completed_dt.date())
    achievements = check_achievements(completed_dt)
    return {"total_xp": sum(i["points"] for i in items), "items": items,
            "streak": streak, "achievements": achievements}


# ------------------------------------------------------------ achievements
def _achieved(key: str, ctx: dict) -> bool:
    if key == "first_task":
        return ctx["total_completed"] >= 1
    if key == "tasks_10":
        return ctx["total_completed"] >= 10
    if key == "tasks_100":
        return ctx["total_completed"] >= 100
    if key == "streak_3":
        return ctx["streak_longest"] >= 3
    if key == "streak_7":
        return ctx["streak_longest"] >= 7
    if key == "streak_30":
        return ctx["streak_longest"] >= 30
    if key == "first_high":
        return ctx["high_completed"] >= 1
    return False


def check_achievements(at: datetime | None = None) -> list[dict]:
    """Unlock any newly-earned achievements; returns the newly unlocked ones."""
    at = at or du.now()
    total_completed = int(db.query_one(
        "SELECT COUNT(*) AS c FROM tasks WHERE status = 'completed'")["c"])
    high_completed = int(db.query_one(
        "SELECT COUNT(*) AS c FROM tasks WHERE status = 'completed' AND priority IN ('high', 'immediate')"
    )["c"])
    streak = get_streak()
    ctx = {"total_completed": total_completed, "high_completed": high_completed,
           "streak_current": streak["current"], "streak_longest": streak["longest"]}

    unlocked = {r["key"] for r in db.query("SELECT key FROM achievements WHERE unlocked_at IS NOT NULL")}
    new: list[dict] = []
    for key, name, description, icon, xp in schema.ACHIEVEMENT_CATALOG:
        if key in unlocked or not _achieved(key, ctx):
            continue
        db.execute(
            "UPDATE achievements SET unlocked_at = ? WHERE key = ? AND unlocked_at IS NULL",
            (du.iso_dt(at), key),
        )
        try:
            db.execute(
                "INSERT INTO xp_transactions (task_id, kind, points, reason, reward_key, created_at) "
                "VALUES (?, 'achievement', ?, ?, ?, ?)",
                (None, int(xp), f"Achievement unlocked: {name}", f"achievement:{key}", du.iso_dt(at)),
            )
        except sqlite3.IntegrityError:
            pass
        notification_service.create_notification(
            "achievement", f"{icon} Achievement unlocked", f"{name} — {description}", at=at
        )
        new.append({"key": key, "name": name, "icon": icon, "xp": int(xp)})
    return new


def achievements() -> list[dict]:
    return [dict(r) for r in db.query("SELECT * FROM achievements ORDER BY id")]


def xp_history(limit: int = 50) -> list[dict]:
    rows = db.query(
        "SELECT x.id, x.kind, x.points, x.reason, x.created_at, t.title "
        "FROM xp_transactions x LEFT JOIN tasks t ON t.id = x.task_id "
        "ORDER BY x.id DESC LIMIT ?",
        (limit,),
    )
    return [dict(r) for r in rows]
