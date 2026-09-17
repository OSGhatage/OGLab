"""Date helpers for Things To Do.

Every "current time" read in the app goes through :func:`now` /
:func:`today` (or an explicitly injected timestamp) so that tests and the
background worker behave deterministically across midnight.
"""
from __future__ import annotations

import calendar
from datetime import date, datetime, timedelta

DATE_FMT = "%Y-%m-%d"


def now() -> datetime:
    """Local 'now' (naive, in the timezone of this machine)."""
    return datetime.now()


def today() -> date:
    return now().date()


def iso_dt(dt: datetime | None) -> str | None:
    """Serialize a datetime to the ISO-8601 string stored in SQLite."""
    if dt is None:
        return None
    return dt.isoformat(sep="T", timespec="seconds")


def parse_dt(value: str | datetime | None) -> datetime | None:
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        return value
    try:
        return datetime.fromisoformat(str(value))
    except ValueError:
        return None


def parse_date(value: str | date | datetime | None) -> date | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    try:
        return datetime.strptime(str(value), DATE_FMT).date()
    except (ValueError, TypeError):
        return None


def date_str(d: date | datetime | None) -> str | None:
    if d is None:
        return None
    if isinstance(d, datetime):
        d = d.date()
    return d.strftime(DATE_FMT)


def add_days(d: date, days: int) -> date:
    return d + timedelta(days=days)


def hhmm_to_minutes(hhmm: str | None, default: int) -> int:
    if not hhmm:
        return default
    try:
        hours, minutes = str(hhmm).split(":")
        return int(hours) * 60 + int(minutes)
    except (ValueError, AttributeError):
        return default


def in_quiet_hours(dt: datetime, start: str = "22:00", end: str = "07:00") -> bool:
    """True when *dt* falls inside the (possibly overnight) quiet window."""
    cur = dt.hour * 60 + dt.minute
    s = hhmm_to_minutes(start, 22 * 60)
    e = hhmm_to_minutes(end, 7 * 60)
    if s == e:
        return False
    if s < e:
        return s <= cur < e
    return cur >= s or cur < e


def next_midnight(dt: datetime) -> datetime:
    return (dt + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)


def period_bounds(
    label: str,
    ref: date,
    custom_from: date | None = None,
    custom_to: date | None = None,
) -> tuple[date | None, date | None]:
    """Resolve a history-filter label to an inclusive (start, end) date pair.

    ``(None, None)`` means "all time".
    """
    if label == "today":
        return ref, ref
    if label == "yesterday":
        y = add_days(ref, -1)
        return y, y
    if label == "7d":
        return add_days(ref, -6), ref
    if label == "month":
        start = ref.replace(day=1)
        last = date(ref.year, ref.month, calendar.monthrange(ref.year, ref.month)[1])
        return start, min(ref, last)
    if label == "custom":
        if custom_from and custom_to and custom_from <= custom_to:
            return custom_from, custom_to
        return ref, ref
    return None, None  # "all"


def friendly_date(d: date | datetime | str | None) -> str:
    d = parse_date(d)
    if d is None:
        return "—"
    return d.strftime("%b %d, %Y").replace(" 0", " ")


def day_label(d: date | datetime | str | None, ref: date) -> str:
    d = parse_date(d)
    ref = parse_date(ref)
    if d is None or ref is None:
        return "—"
    if d == ref:
        return "Today"
    if d == add_days(ref, -1):
        return "Yesterday"
    if d == add_days(ref, 1):
        return "Tomorrow"
    return friendly_date(d)


def month_name(d: date) -> str:
    return d.strftime("%B %Y")
