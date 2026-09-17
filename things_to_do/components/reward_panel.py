"""Reward UI: today's progress card, achievements grid, reward history table."""
from __future__ import annotations

import streamlit as st

import utils.date_utils as du
import utils.time_utils as tu
from components import _compat as cx
from database import schema
from services import reward_service, task_service


def _summary_message(stats: dict, streak: dict) -> str:
    if stats["planned_total"] == 0:
        return "Nothing planned for today. Add a task and get something done."
    if stats["completed_today"] == 0:
        return (f"{stats['remaining']} task(s) waiting — pick the highest-priority "
                f"one and start the timer.")
    if stats["remaining"] == 0:
        extra = f" {stats['high_completed']} high-priority one(s) included." if stats["high_completed"] else ""
        return f"All of today's tasks are done{extra} Great work!"
    focused = (f" with {tu.seconds_to_human(stats['tracked_seconds'])} focused time"
               if stats["tracked_seconds"] else "")
    msg = f"{stats['completed_today']} done{focused} — {stats['remaining']} still to go."
    if streak["current"]:
        msg += f" Keep the streak alive (day {streak['current']})."
    return msg


def render_today_progress() -> None:
    """'Today's Progress' card with a short, fact-based message."""
    stats = task_service.today_stats()
    streak = reward_service.get_streak()
    m1, m2, m3, m4 = st.columns(4)
    m1.metric("Completed today", stats["completed_today"])
    m2.metric("XP earned", f"+{reward_service.xp_today()}")
    m3.metric("Time focused", tu.seconds_to_human(stats["tracked_seconds"], default="0m"))
    m4.metric("Current streak", f"{streak['current']} 🔥")
    st.markdown(
        f"<div class='progress-msg'>💬 {_summary_message(stats, streak)}</div>",
        unsafe_allow_html=True,
    )


def render_achievements() -> None:
    unlocked = {a["key"]: a for a in reward_service.achievements() if a.get("unlocked_at")}
    cols = st.columns(4)
    for i, (key, name, description, icon, xp) in enumerate(schema.ACHIEVEMENT_CATALOG):
        with cols[i % 4]:
            ach = unlocked.get(key)
            if ach:
                when = du.parse_dt(ach["unlocked_at"])
                st.markdown(
                    f"<div class='ach-card unlocked'><div class='ach-icon'>{icon}</div>"
                    f"<div class='ach-name'>{name}</div>"
                    f"<div class='ach-desc'>{description} · +{xp} XP</div>"
                    f"<div class='ach-when'>Unlocked {du.friendly_date(when.date()) if when else ''}</div></div>",
                    unsafe_allow_html=True,
                )
            else:
                st.markdown(
                    f"<div class='ach-card locked'><div class='ach-icon'>🔒</div>"
                    f"<div class='ach-name'>{name}</div>"
                    f"<div class='ach-desc'>{description} · +{xp} XP</div></div>",
                    unsafe_allow_html=True,
                )


def render_reward_history(limit: int = 30) -> None:
    rows = reward_service.xp_history(limit=limit)
    if not rows:
        st.info("No rewards earned yet — complete your first task to earn XP.")
        return
    data = []
    for row in rows:
        created = du.parse_dt(row["created_at"])
        data.append({
            "Date": du.friendly_date(created.date()) if created else "—",
            "Time": tu.fmt_time(created) if created else "—",
            "Task": row.get("title") or "—",
            "Reason": row["reason"],
            "Type": row["kind"],
            "XP": f"+{row['points']}",
        })
    cx.dataframe(data, height=min(430, 60 + 35 * len(data)))
