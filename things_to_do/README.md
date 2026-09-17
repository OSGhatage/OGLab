# ✅ Things To Do

A personal **task-management dashboard** built with Streamlit and SQLite:
daily task planning with priorities, a start/pause/resume time tracker,
automatic carry-forward of unfinished tasks, a reminder/notification center
driven by a lightweight background worker, and a light productivity game
(XP, levels, streaks, achievements) — all persistently stored in one local
database file.

## Features

**Task management**
- Add tasks for today (or any planned date) with priority, notes, optional
  estimate and an optional "start timer on add".
- Priorities: 🚨 Immediate, 🔴 High, 🟠 Medium, 🟢 Low — always shown with
  icon **and** text, not color alone.
- Active-task cards with due date, created date, carry-forward indicator
  (`🔁 ×n`), live timer chip and an expandable details/history panel.
- Check the box to complete: exact completion timestamp is recorded, the
  task leaves the active list **immediately**, and its row is kept forever
  in the history (never deleted).
- Search + filters (keyword, priority, status, planned date) on the
  *All Tasks* page; period + priority filters on *Completed History*.
- Calendar month view with per-day planned/completed counts and a day
  detail (planned, completed, carried-forward).
- Compact metrics: Today's Tasks · Completed · Remaining · Completion Rate ·
  Time Tracked, plus a "completed per day" chart.

**Time tracking**
- Per-task **Start / Pause / Resume / Complete** workflow.
- Durations stored internally in seconds (running segments accumulated in
  milliseconds) and displayed as e.g. `1h 24m`.
- Tasks completed without a timer get an **elapsed** duration (created →
  completed) clearly labelled *elapsed (untracked)*, so **tracked** and
  **elapsed** time are never confused.
- ⚠️ In-card warning when a task exceeds its estimate.

**Carry-forward (automatic, duplicate-free)**
- On app start, on each page load and every ~30 s in the background worker,
  active tasks whose planned date is before today are rolled to today:
  the **same row** is updated — `planned_date` moves, `carry_forward_count`
  increments, a `carried_forward` event is logged — so a task keeps one
  stable UUID, its `original_date` is preserved, and duplicates are
  impossible. Re-running on the same day is a no-op, and tasks planned for
  the future are never touched.

  Example: created Sep 17 → not done → carried Sep 18 (`🔁 ×1`) →
  completed Sep 19. The history shows created *Sep 17*, completed *Sep 19*,
  carries *1*.

**Notifications & reminders**
- Every task can have reminders enabled with its own interval (default
  **2 h**, same for High/Immediate — configurable per priority class).
- The **background worker** (`worker/reminder_worker.py`) runs a scheduling
  pass every ~30 s: due reminders (every 3rd is an *escalated* reminder),
  long-running / estimate-exceeded warnings, the once-per-day summary.
  It never depends on Streamlit reruns.
- Due reminders are claimed with an atomic conditional UPDATE, so a
  notification is never duplicated even if worker and UI race.
- Quiet hours (default 22:00–07:00) suppress delivery — the reminder fires
  when quiet hours are over.
- In-app **notification center** (bell 🔔 in the header + Today page):
  read / dismiss / **snooze 30 m / 1 h / 2 h / tomorrow**. Reminders stop
  automatically when a task is completed.
- Optional in-app sound, and a browser-notification permission panel in
  Settings (gracefully handles browsers that deny iframe notifications —
  the in-app center always works).

**Productivity rewards**
- XP per completion: Low +5, Medium +10, High +20, Immediate +30 (all
  configurable), plus small bonuses: completed before the estimate,
  completed on the original planned date, high-priority task, and a daily
  milestone (default: 5 tasks in a day).
- Levels with configurable thresholds and a progress bar; XP today / week /
  month counters.
- Streaks: 🔥 current and 🏆 longest (a day counts when ≥ 1 task is
  completed), with streak notifications at milestones.
- Permanent achievements (First Task, 10/100 tasks, 3/7/30-day streaks,
  first high-priority task) stored in SQLite with one-time XP.
- **Anti-gaming:** every reward is an immutable `xp_transactions` row with
  a unique `reward_key` — a task is rewarded exactly once; changing a
  priority after completion never retroactively changes XP; recreating a
  task creates a new ID with no reward history.
- Today's Progress card and the 🌙 daily summary are generated strictly
  from stored statistics — nothing is fabricated.

**Persistence & data safety**
- SQLite in **WAL mode** (`data/tasks.db`), thread-local connections,
  busy-timeout, short transactions — safe for the UI *and* the worker.
- Versioned migrations (`database/migrations.py`) + automatic seeding of
  defaults and the achievement catalog.
- A `task_events` table preserves the full state-change history
  (created / started / paused / resumed / completed / carried_forward /
  priority_changed).
- The database is **not committed to Git** (`.gitignore`); it is recreated
  automatically on any machine the first time the app runs.

## Project structure

```
things_to_do/
├── app.py                     # Streamlit entrypoint (bootstrap + navigation)
├── database/
│   ├── db.py                  # connections (WAL, thread-local), helpers
│   ├── schema.py              # DDL, default settings, achievement catalog
│   └── migrations.py          # versioned migrations + seeding
├── services/
│   ├── task_service.py        # task lifecycle, carry-forward, stats
│   ├── timer_service.py       # start/pause/resume/finalize (ms precision)
│   ├── notification_service.py# notification center + reminder scheduler
│   ├── reward_service.py      # XP, levels, streaks, achievements
│   └── settings_service.py    # settings table (deep-merged defaults)
├── worker/
│   └── reminder_worker.py     # background scheduler thread (also standalone)
├── components/
│   ├── dashboard.py           # header, metrics, level strip, charts
│   ├── task_card.py           # task card + completed row
│   ├── task_form.py           # add-task form
│   ├── notification_center.py # bell, feed, snooze, browser permission
│   ├── reward_panel.py        # progress card, achievements, reward history
│   ├── pages.py               # Today / All / History / Calendar / Analytics / Settings
│   └── _compat.py             # streamlit version-compat helpers
├── utils/
│   ├── date_utils.py          # dates, quiet hours, period bounds
│   └── time_utils.py          # duration formatting (1h 24m), time-ago
├── data/
│   └── tasks.db               # created at runtime (git-ignored)
├── assets/
│   └── style.css
├── tests/
│   └── test_workflows.py      # service + headless-UI workflow tests
├── requirements.txt
└── README.md
```

## Installation

Requires Python 3.10+.

```bash
cd things_to_do
python3 -m venv .venv && source .venv/bin/activate   # recommended
pip install -r requirements.txt
```

(On systems with PEP 668 "externally managed" Python, either use a venv as
above or `pip install --break-system-packages -r requirements.txt`.)

No database setup is needed — `data/tasks.db` is created, migrated and
seeded automatically on first run. To initialize an empty database manually
on another machine:

```bash
python3 -c "from database import db; db.init_db(); print('DB ready at', db.db_path())"
```

You can override the location with the `TTD_DB_PATH` environment variable.

## Running the app

```bash
cd things_to_do
streamlit run app.py
```

The in-process background worker starts automatically. For a headless
deployment where Streamlit is not always up, you can instead (or also) run
the standalone worker:

```bash
python3 -m worker.reminder_worker
```

## Testing

```bash
python3 tests/test_workflows.py
```

Runs ~50 checks against a throwaway database: zero-task state, validation,
add/complete, timer pause/resume/complete, tracked vs elapsed duration,
carry-forward (incl. idempotency and no duplicates), reminders (due, no
duplicates, escalation, quiet hours, snooze, stop-on-completion), XP
anti-gaming, streaks, history filters, achievements, daily summary, and a
headless UI test that drives the real Streamlit app (form submit, checkbox
completion, timer buttons, all six pages, and a full "restart" to prove
persistence).

## How time tracking works

| Column              | Meaning                                                        |
|---------------------|----------------------------------------------------------------|
| `start_time`        | start of the current running segment                           |
| `paused_at`         | set while paused                                               |
| `accumulated_ms`    | finished running segments (milliseconds)                       |
| `actual_duration`   | final seconds on completion                                    |
| `duration_basis`    | `tracked` (timer used) or `elapsed` (created → completed)      |

Tracked time = `accumulated_ms + (now − start_time)` while running.

## How carry-forward works

`task_service.roll_day(ref=today)` finds `status='active' AND planned_date < today`
and, in one transaction, updates each row: `planned_date = today`,
`carry_forward_count += 1`, `last_active_date = today`, plus a
`carried_forward` event with the old and new date. No copies are created;
re-running the same day matches nothing (no-op).

## How reminders work

`notification_service.check_due_reminders(now)`:

1. For each active task with reminders enabled (and not snoozed, not
   filtered out by *high-only*, outside quiet hours): if
   `now ≥ last_reminder_at + interval` (interval defaults to 2 h, and High/
   Immediate use the high-priority interval) it atomically claims the
   reminder (conditional UPDATE) and writes a 🔔 notification — the 3rd
   consecutive reminder is an ⚠️ escalated one.
2. Independently, if a task has been active longer than
   `max(1.5 × estimate, long-task threshold)` it gets a one-time ⏰
   long-running warning.
3. Once per day at the configured summary hour (default 21:00, outside
   quiet hours) a 🌙 daily summary is written from real statistics.

Completion sets `status='completed'`, so the task immediately drops out of
all reminder queries.

## Ideas for the future

- Desktop notification provider (e.g. `plyer`) as an alternative delivery
  channel — the notification row design makes this a drop-in.
- Task editing (title/description/estimate) and archiving.
- Weekly goals and a "focus mode" that silences everything but one task.
- CSV export / backup of `tasks.db`.
- Multi-user support (currently a single-user personal app).
