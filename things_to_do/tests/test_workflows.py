"""Workflow tests for Things To Do.

Run from anywhere:

    python3 tests/test_workflows.py

Part A exercises the service layer directly against a throwaway SQLite
database (``TTD_DB_PATH``) — validation, timer pause/resume, completion,
carry-forward, reminders, quiet hours, snooze, XP anti-gaming, streaks,
history filters, persistence across "restarts".

Part B drives the real Streamlit UI headlessly with
``streamlit.testing.v1.AppTest``: adding a task through the form,
completing via the checkbox, timer buttons, navigation over all six pages,
and a full app restart to prove persistence.

Exits non-zero if any check fails.
"""
from __future__ import annotations

import os
import sys
import tempfile
from datetime import date, datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

_tmp = Path(tempfile.mkdtemp(prefix="ttd_test_"))
os.environ["TTD_DB_PATH"] = str(_tmp / "test.db")

RESULTS: list[tuple[str, bool, str]] = []


def check(name: str, cond: bool, extra: str = "") -> None:
    RESULTS.append((name, bool(cond), extra))
    line = f"[{'PASS' if cond else 'FAIL'}] {name}"
    if not cond and extra:
        line += f"  ({extra})"
    print(line, flush=True)


def section(title: str) -> None:
    print(f"\n=== {title} ===", flush=True)


def exc_text(at) -> str:
    return "; ".join(f"{e}: {e.value}" for e in at.exception)


def w(at, etype: str, key: str):
    """Find a widget on an AppTest by its stable key."""
    for el in getattr(at, etype):
        if el.key == key:
            return el
    raise KeyError(f"{etype} with key {key!r} not found")


def main() -> int:
    from database import db
    import utils.date_utils as du
    import services.task_service as ts
    import services.timer_service as tms
    import services.notification_service as ns
    import services.reward_service as rs
    import services.settings_service as ss

    db.init_db()
    now = du.now()

    # ------------------------------------------------------------ zero state
    section("Zero-task state")
    check("empty: no tasks", db.query_one("SELECT COUNT(*) c FROM tasks")["c"] == 0)
    check("empty: no active tasks", ts.list_tasks(status="active") == [])
    check("empty: history empty", ts.get_completed_between(None, None) == [])
    stats = ts.today_stats()
    check("empty: stats safe", stats["planned_total"] == 0 and stats["rate"] is None)
    level = rs.level_info()
    check("empty: level 1 with 0 XP", level["level"] == 1 and level["total"] == 0)

    # ------------------------------------------------------------- validation
    section("Validation")
    tid, err = ts.create_task("   ")
    check("create: empty title rejected", tid is None and bool(err))
    tid, err = ts.create_task("", priority="bogus")
    check("create: empty title rejected (2)", tid is None and bool(err))

    # --------------------------------------------------------------- add task
    section("Add task")
    tid, err = ts.create_task("Write quarterly report", "Draft + review", "high",
                              du.today(), 30, start_timer=True, at=now)
    check("create: ok", tid is not None and err is None, str(err))
    task = ts.get_task(tid)
    check("create: appears in active list", any(t["id"] == tid for t in ts.list_tasks(status="active")))
    check("create: original_date preserved", task["original_date"] == du.date_str(now.date()))
    check("create: high priority uses 2h reminder interval", task["reminder_interval"] == 7200)
    check("create: timer running on add", task["start_time"] is not None and task["is_paused"] == 0)

    # ------------------------------------------------------------- timer flow
    section("Timer start / pause / resume / complete")
    t0 = now
    tms.pause(tid, at=t0 + timedelta(minutes=5))
    t = ts.get_task(tid)
    # ±2 s tolerance: ISO storage is second-precision while `now` has microseconds
    check("timer: pause accumulates 5m",
          t["is_paused"] == 1 and 5 * 60 * 1000 <= t["accumulated_ms"] < 5 * 60 * 1000 + 2000,
          str(t["accumulated_ms"]))
    tms.resume(tid, at=t0 + timedelta(minutes=20))
    t = ts.get_task(tid)
    check("timer: resume clears pause flag", t["is_paused"] == 0 and t["start_time"] is not None)
    # complete at t0+40m → running segment 20m + accumulated 5m = 25m (±2 s)
    res = ts.complete_task(tid, at=t0 + timedelta(minutes=40))
    t = ts.get_task(tid)
    check("complete: status completed", t["status"] == "completed" and t["completed_at"] is not None)
    check("complete: tracked duration 25m",
          t["duration_basis"] == "tracked" and 24 * 60 <= t["actual_duration"] <= 25 * 60 + 5,
          str(t["actual_duration"]))
    check("complete: removed from active list", all(x["id"] != tid for x in ts.list_tasks(status="active")))
    check("complete: reward returned", res["ok"] and res["reward"]["total_xp"] > 0)

    # elapsed (untracked) duration when no timer was used
    tid_e, _ = ts.create_task("Quick email", priority="low", at=now)
    ts.complete_task(tid_e, at=now + timedelta(hours=1, minutes=30))
    te = ts.get_task(tid_e)
    check("complete: elapsed basis without timer",
          te["duration_basis"] == "elapsed" and te["actual_duration"] == 90 * 60)

    # -------------------------------------------------------------- rewards
    section("XP / anti-gaming")
    reward = res["reward"]
    # high: base 20 + before-estimate 5 (25m < 30m) + on-planned-date 5 + high bonus 5 = 35
    check("reward: high task +35 XP", reward["total_xp"] == 35, str(reward["items"]))
    check("reward: before-estimate bonus", any(i["reason"] == "Completed before estimate" for i in reward["items"]))
    total_before = rs.total_xp()
    rs.award_completion(tid, completed_dt=now)  # force re-award
    check("reward: no duplicate XP on re-award", rs.total_xp() == total_before)
    ts.set_priority(tid, "immediate")
    check("reward: priority change after completion keeps XP", rs.total_xp() == total_before)
    events = ts.get_events(tid)
    check("reward: priority change logged as event",
          any(e["event_type"] == "priority_changed" for e in events))

    # ----------------------------------------------------------- carry-forward
    section("Carry-forward")
    tid2, _ = ts.create_task("Old uncompleted task", priority="medium", at=now)
    db.execute("UPDATE tasks SET planned_date = ? WHERE id = ?",
               (du.date_str(du.add_days(now.date(), -1)), tid2))
    carried = ts.roll_day(now.date())
    t2 = ts.get_task(tid2)
    check("carry: rolled to today", carried == 1 and t2["planned_date"] == du.date_str(now.date()))
    check("carry: counter incremented once", t2["carry_forward_count"] == 1)
    check("carry: original date preserved", t2["original_date"] == du.date_str(now.date()))
    again = ts.roll_day(now.date())
    t2 = ts.get_task(tid2)
    check("carry: idempotent same-day (no duplicates)", again == 0 and t2["carry_forward_count"] == 1)
    evs = [e for e in ts.get_events(tid2) if e["event_type"] == "carried_forward"]
    check("carry: event logged with from/to", len(evs) == 1)
    # missed again → carried a second time (same row, no duplicates)
    db.execute("UPDATE tasks SET planned_date = ? WHERE id = ?",
               (du.date_str(du.add_days(now.date(), -1)), tid2))
    ts.roll_day(now.date())
    t2 = ts.get_task(tid2)
    check("carry: repeated carry increments same task", t2["carry_forward_count"] == 2)
    check("carry: no duplicate rows", db.query_one("SELECT COUNT(*) c FROM tasks WHERE id = ?", (tid2,))["c"] == 1)

    # ---------------------------------------------------------------- reminders
    section("Reminders")
    # Run this whole section outside quiet hours so wall-clock time is irrelevant.
    prev_n = ss.get_section("notifications")
    n_off = dict(prev_n)
    n_off["quiet_start"] = n_off["quiet_end"] = "00:00"  # s == e → no quiet window
    ss.set_section("notifications", n_off)

    tid3, _ = ts.create_task("Long running task", priority="high", start_timer=True, at=now - timedelta(hours=3))
    db.execute("UPDATE tasks SET start_time = ?, created_at = ? WHERE id = ?",
               (du.iso_dt(now - timedelta(hours=3)), du.iso_dt(now - timedelta(hours=3)), tid3))
    sent = ns.check_due_reminders(now_dt=now)
    t3 = ts.get_task(tid3)
    check("reminder: one due reminder sent", t3["reminders_sent"] == 1 and any(r["task_id"] == tid3 for r in sent),
          f"sent={sent} sent_count={t3['reminders_sent']}")
    sent2 = ns.check_due_reminders(now_dt=now + timedelta(seconds=30))
    t3 = ts.get_task(tid3)
    check("reminder: no duplicate within interval", t3["reminders_sent"] == 1 and not sent2)
    # force two more due cycles → the 3rd must be escalated
    for _ in range(2):
        db.execute("UPDATE tasks SET last_reminder_at = ? WHERE id = ?",
                   (du.iso_dt(now - timedelta(hours=2)), tid3))
        ns.check_due_reminders(now_dt=now)
    t3 = ts.get_task(tid3)
    check("reminder: escalated on 3rd (level 3)", t3["reminders_sent"] == 3 and t3["escalation_level"] == 3,
          f"sent={t3['reminders_sent']} level={t3['escalation_level']}")
    esc = [x for x in ns.list_recent(10) if x["ntype"] == "escalation" and x["task_id"] == tid3]
    check("reminder: escalation notification exists", len(esc) == 1)

    # quiet hours suppress (explicit window covering the test instant)
    n_q = dict(prev_n)
    n_q["quiet_start"], n_q["quiet_end"] = "00:00", "23:59"
    ss.set_section("notifications", n_q)
    db.execute("UPDATE tasks SET last_reminder_at = ? WHERE id = ?",
               (du.iso_dt(now - timedelta(hours=2)), tid3))
    sent_q = ns.check_due_reminders(now_dt=now)
    t3 = ts.get_task(tid3)
    check("reminder: quiet hours suppress", not sent_q and t3["reminders_sent"] == 3)

    # snooze suppresses until snooze_until passes (back outside quiet hours)
    ss.set_section("notifications", n_off)
    tid4, _ = ts.create_task("Snoozed task", priority="medium", start_timer=True, at=now - timedelta(hours=3))
    db.execute("UPDATE tasks SET start_time = ?, created_at = ? WHERE id = ?",
               (du.iso_dt(now - timedelta(hours=3)), du.iso_dt(now - timedelta(hours=3)), tid4))
    ns.check_due_reminders(now_dt=now)  # send the first one
    first_notif = [x for x in ns.list_recent(5) if x["task_id"] == tid4]
    check("snooze: first reminder existed to snooze", len(first_notif) == 1)
    ns.snooze(first_notif[0]["id"], tid4, du.next_midnight(now))
    db.execute("UPDATE tasks SET last_reminder_at = ? WHERE id = ?",
               (du.iso_dt(now - timedelta(hours=2)), tid4))
    sent_s = ns.check_due_reminders(now_dt=now)
    t4 = ts.get_task(tid4)
    check("snooze: suppresses until snooze_until",
          t4["snooze_until"] is not None and t4["reminders_sent"] == 1 and not sent_s)
    sent_s2 = ns.check_due_reminders(now_dt=du.next_midnight(now) + timedelta(seconds=5))
    t4 = ts.get_task(tid4)
    check("snooze: fires again after snooze window", t4["reminders_sent"] == 2)

    # completion stops reminders
    ts.complete_task(tid3, at=now)
    db.execute("UPDATE tasks SET last_reminder_at = NULL WHERE id = ?", (tid3,))
    sent_c = ns.check_due_reminders(now_dt=now + timedelta(hours=3))
    check("reminder: stopped after completion", not any(r["task_id"] == tid3 for r in sent_c))
    ss.set_section("notifications", prev_n)  # restore real settings

    # --------------------------------------------------------------- streaks
    section("Streaks")
    db.execute("UPDATE streaks SET current = 0, longest = 0, last_date = NULL")
    d0 = now.date()
    r1 = rs.update_streak(d0)
    r2 = rs.update_streak(du.add_days(d0, 1))
    check("streak: consecutive days count", r1["current"] == 1 and r2["current"] == 2)
    r3 = rs.update_streak(du.add_days(d0, 3))  # gap
    check("streak: gap resets", r3["current"] == 1 and r3["longest"] == 2)

    # -------------------------------------------------------------- history
    section("History filters")
    todays = ts.get_completed_between(now.date(), now.date())
    check("history: today filter", todays and all(x["completed_at"][:10] == du.date_str(now.date()) for x in todays))
    yest = ts.get_completed_between(du.add_days(now.date(), -1), du.add_days(now.date(), -1))
    check("history: yesterday filter (empty)", yest == [])
    week = ts.get_completed_between(du.add_days(now.date(), -6), now.date())
    check("history: last-7-days includes today's", len(week) >= len(todays))
    all_done = ts.get_completed_between(None, None)
    check("history: all-time", len(all_done) >= 3)

    # --------------------------------------------------------- achievements
    section("Achievements")
    unlocked = {r["key"] for r in db.query("SELECT key FROM achievements WHERE unlocked_at IS NOT NULL")}
    check("achievement: first_task unlocked", "first_task" in unlocked)
    check("achievement: first_high unlocked", "first_high" in unlocked)
    xp_rows = db.query("SELECT * FROM xp_transactions WHERE kind = 'achievement'")
    check("achievement: XP transaction stored", len(xp_rows) >= 2)
    ach_notifs = [x for x in ns.list_recent(50) if x["ntype"] == "achievement"]
    check("achievement: notification created", len(ach_notifs) >= 2)

    # ------------------------------------------------------------- daily summary
    section("Daily summary")
    ss.set_scalar("last_daily_summary", None)
    db.execute("UPDATE settings SET value = 'null' WHERE key = 'last_daily_summary'")
    summary = ns.maybe_daily_summary(now_dt=now.replace(hour=21, minute=5))
    check("summary: emitted once at summary hour", summary is not None and summary["completed"] >= 3)
    summary2 = ns.maybe_daily_summary(now_dt=now.replace(hour=21, minute=6))
    check("summary: not repeated same day", summary2 is None)

    # ---------------------------------------------------------------- E2E UI
    section("E2E: Streamlit UI (headless AppTest)")
    from streamlit.testing.v1 import AppTest

    at = AppTest.from_file(str(ROOT / "app.py"), default_timeout=60)
    at.run()
    check("UI: app starts without exceptions", not at.exception, exc_text(at))

    w(at, "text_input", "tf_title").set_value("E2E: file the expense report")
    w(at, "selectbox", "tf_prio").set_value("medium")
    w(at, "button", "tf_submit").set_value(True).run()
    check("UI: healthy after form submit", not at.exception, exc_text(at))
    rows = db.query("SELECT * FROM tasks WHERE title = ?", ("E2E: file the expense report",))
    check("UI: task created through form", len(rows) == 1)
    tid5 = rows[0]["id"]

    w(at, "checkbox", f"chk_{tid5}").set_value(True).run()
    t5 = ts.get_task(tid5)
    check("UI: checkbox completes task", t5 and t5["status"] == "completed" and t5["completed_at"] is not None)
    check("UI: elapsed duration recorded (no timer)",
          t5 and t5["duration_basis"] == "elapsed" and (t5["actual_duration"] or 0) >= 0)

    # empty-title validation through the UI
    w(at, "text_input", "tf_title").set_value("   ")
    w(at, "button", "tf_submit").set_value(True).run()
    check("UI: empty title shows error, no row",
          any("cannot be empty" in str(e.value) for e in at.error)
          and db.query_one("SELECT COUNT(*) c FROM tasks WHERE title = '   '")["c"] == 0)

    # timer workflow through UI buttons
    w(at, "text_input", "tf_title").set_value("E2E: timed task")
    w(at, "checkbox", "tf_timer").set_value(True)
    w(at, "button", "tf_submit").set_value(True).run()
    rows = db.query("SELECT * FROM tasks WHERE title = ?", ("E2E: timed task",))
    check("UI: timed task created running",
          len(rows) == 1 and rows[0]["start_time"] is not None and rows[0]["is_paused"] == 0)
    tid6 = rows[0]["id"]
    w(at, "button", f"timer_{tid6}_pause").set_value(True).run()
    t6 = ts.get_task(tid6)
    check("UI: pause button works", t6["is_paused"] == 1 and t6["paused_at"] is not None)
    w(at, "button", f"timer_{tid6}_resume").set_value(True).run()
    t6 = ts.get_task(tid6)
    check("UI: resume button works", t6["is_paused"] == 0 and t6["accumulated_ms"] >= 0)

    # ------------------------------------------------------- restart persistence
    section("Persistence across restart")
    at2 = AppTest.from_file(str(ROOT / "app.py"), default_timeout=60)
    at2.run()
    check("UI: restart without exceptions", not at2.exception, exc_text(at2))
    check("persist: completed task survives restart", ts.get_task(tid5)["status"] == "completed")
    check("persist: active timer task survives restart", ts.get_task(tid6)["status"] == "active")
    check("persist: XP total survives restart", rs.total_xp() > 0)
    check("persist: notifications survive restart", len(ns.list_recent(5)) > 0)
    check("persist: history page data present", len(ts.get_completed_between(du.today(), du.today())) >= 1)

    # ------------------------------------------------------- navigation smoke
    section("Navigation (all pages render)")
    for label in ["📋 All Tasks", "📜 Completed History", "🗓 Calendar", "📊 Analytics",
                  "⚙️ Settings", "📌 Today"]:
        w(at2, "radio", "nav").set_value(label).run()
        check(f"UI: page renders — {label}", not at2.exception, exc_text(at2))

    # ---------------------------------------------------------------- summary
    passed = sum(1 for _, ok, _ in RESULTS if ok)
    failed = [(n, x) for n, ok, x in RESULTS if not ok]
    print(f"\n{'=' * 50}\n{passed}/{len(RESULTS)} checks passed. Temp DB: {_tmp/'test.db'}")
    if failed:
        print("FAILED:")
        for name, extra in failed:
            print(f"  - {name}: {extra}")
        return 1
    print("ALL CHECKS PASSED ✅")
    return 0


if __name__ == "__main__":
    sys.exit(main())
