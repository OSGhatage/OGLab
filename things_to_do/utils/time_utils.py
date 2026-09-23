"""Duration and timestamp display helpers.

Durations are stored internally in seconds (and milliseconds for running
timers) and rendered as compact human strings such as ``1h 24m``.
"""
from __future__ import annotations

from datetime import datetime

import utils.date_utils as du


def seconds_to_human(total_seconds, default: str = "—") -> str:
    """Format seconds as ``1h 24m``, ``45m`` or ``30s``."""
    if total_seconds is None:
        return default
    try:
        s = int(round(float(total_seconds)))
    except (TypeError, ValueError):
        return default
    if s < 0:
        s = 0
    h, rem = divmod(s, 3600)
    m, sec = divmod(rem, 60)
    if h:
        return f"{h}h {m}m"
    if m:
        return f"{m}m"
    return f"{sec}s"


def fmt_time(dt: datetime | None) -> str:
    if dt is None:
        return "—"
    return dt.strftime("%H:%M")


def time_ago(iso_value: str | None, at: datetime | None = None) -> str:
    """Relative time label: 'just now', '4m ago', '3h ago', '2d ago' …"""
    dt = du.parse_dt(iso_value)
    if dt is None:
        return "—"
    at = at or du.now()
    delta = (at - dt).total_seconds()
    if delta < 45:
        return "just now"
    if delta < 90:
        return "1m ago"
    minutes = int(delta // 60)
    if minutes < 60:
        return f"{minutes}m ago"
    hours = minutes // 60
    if hours < 24:
        return f"{hours}h ago"
    days = hours // 24
    return f"{days}d ago"
