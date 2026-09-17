"""Lightweight background reminder worker.

A daemon thread that periodically:

1. rolls the day over (carries unfinished tasks into today),
2. runs the reminder / long-task notification pass,
3. emits the once-per-day summary.

It never modifies user-facing task state beyond the reminder bookkeeping
columns (``last_reminder_at``, ``reminders_sent``, ``escalation_level``) and
the carry-forward update — all writes go through short, atomic transactions
so they cannot conflict with actions taken in the Streamlit UI (WAL mode +
busy timeout make concurrent access safe).

Run it in-process (automatic when Streamlit starts) or standalone for a
headless deployment:

    python -m worker.reminder_worker
"""
from __future__ import annotations

import logging
import os
import threading
import time
from datetime import datetime

import utils.date_utils as du

log = logging.getLogger("ttd.worker")


class ReminderWorker(threading.Thread):
    def __init__(self, check_interval: float = 30.0):
        super().__init__(daemon=True, name="ttd-reminder-worker")
        self._stop = threading.Event()
        self._interval = max(5.0, float(check_interval))
        self.last_cycle_at: datetime | None = None
        self.last_error: str | None = None

    # -- one scheduling pass -------------------------------------------
    def run_cycle(self) -> dict:
        # Local imports keep the module importable even before the DB exists.
        from services import notification_service, task_service

        now_dt = du.now()
        carried = task_service.roll_day()
        reminders = notification_service.check_due_reminders(now_dt)
        summary = notification_service.maybe_daily_summary(now_dt)
        self.last_cycle_at = now_dt
        self.last_error = None
        if carried:
            log.info("carried %d task(s) forward to %s", carried, now_dt.date())
        return {"carried": carried, "reminders": reminders, "summary": summary}

    # -- thread loop ------------------------------------------------------
    def run(self) -> None:
        log.info("reminder worker started (interval=%.0fs)", self._interval)
        while not self._stop.is_set():
            try:
                self.run_cycle()
            except Exception as exc:  # noqa: BLE001 — keep the loop alive
                self.last_error = str(exc)
                log.exception("reminder cycle failed: %s", exc)
            self._stop.wait(self._interval)

    def stop(self, timeout: float = 2.0) -> None:
        self._stop.set()
        self.join(timeout)


_started: dict[str, ReminderWorker] = {}
_lock = threading.Lock()


def ensure_worker_started(check_interval: float | None = None) -> ReminderWorker:
    """Start (once per database) and return the worker thread."""
    from database import db

    key = str(db.db_path())
    with _lock:
        worker = _started.get(key)
        if worker is None or not worker.is_alive():
            worker = ReminderWorker(check_interval or float(os.environ.get("TTD_CHECK_INTERVAL", 30)))
            _started[key] = worker
            worker.start()
        return worker


def get_worker() -> ReminderWorker | None:
    from database import db

    with _lock:
        return _started.get(str(db.db_path()))


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
    from database import db

    db.init_db()
    log.info("standalone reminder worker — database: %s", db.db_path())
    worker = ReminderWorker()
    try:
        worker.run()
    except KeyboardInterrupt:
        worker.stop()
        log.info("stopped")


if __name__ == "__main__":
    main()
