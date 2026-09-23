"""Task card UI: priority badge, meta line, timer workflow, completion checkbox.

The completion checkbox is the single completion entry point in the UI —
checking it finalises the timer, records the exact completion timestamp and
moves the task into the permanent history (the row is kept, never deleted).
"""
from __future__ import annotations

import html

import streamlit as st

import utils.date_utils as du
import utils.time_utils as tu
from services import task_service, timer_service

REMINDER_INTERVALS = {
    "30 minutes": 1800,
    "1 hour": 3600,
    "2 hours": 7200,
    "4 hours": 14400,
    "8 hours": 28800,
    "12 hours": 43200,
}


def _timer_chip(task: dict) -> str:
    state = timer_service.state(task)
    if state == "idle":
        return ""
    elapsed = tu.seconds_to_human(timer_service.elapsed_ms(task) / 1000)
    if state == "running":
        return f"<span class='timer-chip running'>⏱ {elapsed}</span>"
    return f"<span class='timer-chip paused'>⏸ {elapsed}</span>"


def _exceeded_estimate(task: dict) -> bool:
    est = task.get("estimated_duration")
    if not est or task["status"] != task_service.ACTIVE:
        return False
    if task.get("start_time"):
        elapsed_s = timer_service.elapsed_ms(task) / 1000
    else:
        created = du.parse_dt(task.get("created_at")) or du.now()
        elapsed_s = (du.now() - created).total_seconds()
    return elapsed_s > est


def render_task_card(task: dict, interactive: bool = True, highlight: bool = False) -> None:
    meta = task_service.PRIORITY_META.get(task["priority"], task_service.PRIORITY_META["medium"])
    title_html = html.escape(task["title"])
    created = du.parse_dt(task.get("created_at"))
    planned = du.parse_date(task.get("planned_date"))
    carries = int(task.get("carry_forward_count") or 0)
    classes = f"task-card prio-{task['priority']}" + (" task-highlight" if highlight else "")
    is_active = task["status"] == task_service.ACTIVE

    st.markdown(f"<div class='{classes}'>", unsafe_allow_html=True)
    c0, c1, c2 = st.columns([0.06, 1.0, 0.72])
    with c0:
        if interactive and is_active:
            done = st.checkbox(
                "Complete task", value=False, key=f"chk_{task['id']}", label_visibility="collapsed"
            )
            if done:
                result = task_service.complete_task(task["id"])
                st.session_state.pop(f"chk_{task['id']}", None)
                if result["ok"]:
                    reward = result["reward"]
                    bits = [f"Completed in {tu.seconds_to_human(result['actual'])} ({result['basis']})"]
                    if reward["total_xp"]:
                        bits.append(f"+{reward['total_xp']} XP")
                    for ach in reward.get("achievements") or []:
                        bits.append(f"{ach['icon']} {ach['name']}")
                    st.toast(" · ".join(bits), icon="✅")
                else:
                    st.error(result["error"])
                st.rerun()
    with c1:
        badges = f"<span class='prio-badge prio-{task['priority']}'>{meta['icon']} {meta['label']}</span>"
        st.markdown(
            f"<div class='task-title'>{title_html} {badges} {_timer_chip(task)}</div>",
            unsafe_allow_html=True,
        )
        if task.get("description"):
            st.caption(html.escape(task["description"]).replace("\n", " ")[:180])
        meta_bits = [f"📅 due {du.day_label(planned, du.today())}"]
        if created:
            meta_bits.append(f"🗓 created {du.friendly_date(created.date())}")
        if carries:
            meta_bits.append(f"🔁 carried ×{carries}")
        if task.get("estimated_duration"):
            meta_bits.append(f"🎯 est. {tu.seconds_to_human(task['estimated_duration'])}")
        st.markdown(
            f"<div class='task-meta'>{'&nbsp;·&nbsp;'.join(html.escape(b) for b in meta_bits)}</div>",
            unsafe_allow_html=True,
        )
        if _exceeded_estimate(task):
            st.markdown(
                "<div class='task-warning'>⚠️ This task has exceeded its estimated time.</div>",
                unsafe_allow_html=True,
            )
    with c2:
        if interactive and is_active:
            state = timer_service.state(task)
            key_base = f"timer_{task['id']}"
            if state == "idle":
                if st.button("▶ Start", key=key_base + "_start", use_container_width=True):
                    timer_service.start(task["id"])
                    st.rerun()
            elif state == "running":
                if st.button("⏸ Pause", key=key_base + "_pause", use_container_width=True):
                    timer_service.pause(task["id"])
                    st.rerun()
            else:
                if st.button("⏵ Resume", key=key_base + "_resume", use_container_width=True):
                    timer_service.resume(task["id"])
                    st.rerun()

    with st.expander("⚙️ Details & reminders"):
        created_at = f"{du.friendly_date(created.date())} {tu.fmt_time(created)}" if created else "—"
        st.caption(f"ID `{task['id'][:8]}` · created {created_at}")
        if not is_active and task.get("completed_at"):
            comp = du.parse_dt(task["completed_at"])
            basis = "tracked" if task.get("duration_basis") == "tracked" else "elapsed (untracked)"
            extra = f" · estimate {tu.seconds_to_human(task['estimated_duration'])}" if task.get("estimated_duration") else ""
            st.caption(
                f"✅ Completed {du.friendly_date(comp.date())} at {tu.fmt_time(comp)} · "
                f"duration {tu.seconds_to_human(task.get('actual_duration'))} ({basis}){extra}"
                + (f" · carried ×{carries}" if carries else "")
            )
        if interactive and is_active:
            left, right = st.columns(2)
            with left:
                enabled = st.checkbox(
                    "🔔 Enable reminders", value=bool(task.get("reminder_enabled")), key=f"rem_{task['id']}"
                )
                if enabled != bool(task.get("reminder_enabled")):
                    task_service.set_reminder(task["id"], enabled, None)
                    st.rerun()
                current_sec = int(task.get("reminder_interval") or 7200)
                options = list(REMINDER_INTERVALS)
                idx = min(range(len(options)), key=lambda i: abs(REMINDER_INTERVALS[options[i]] - current_sec))
                chosen = st.selectbox("Reminder interval", options, index=idx, key=f"rint_{task['id']}")
                if REMINDER_INTERVALS[chosen] != current_sec:
                    task_service.set_reminder(task["id"], enabled, REMINDER_INTERVALS[chosen])
                    st.rerun()
            with right:
                new_prio = st.selectbox(
                    "Change priority", task_service.PRIORITIES,
                    index=task_service.PRIORITIES.index(task["priority"]),
                    format_func=task_service.priority_label, key=f"prio_{task['id']}",
                )
                if new_prio != task["priority"]:
                    task_service.set_priority(task["id"], new_prio)
                    st.toast(f"Priority set to {task_service.priority_label(new_prio)}", icon="ℹ️")
                    st.rerun()
        events = task_service.get_events(task["id"], limit=6)
        if events:
            st.markdown("**Recent activity**")
            labels = {
                "created": "🌱 Created",
                "started": "▶ Timer started",
                "paused": "⏸ Timer paused",
                "resumed": "⏵ Timer resumed",
                "completed": "✅ Completed",
                "carried_forward": "🔁 Carried forward",
                "priority_changed": "🔀 Priority changed",
            }
            for ev in events:
                ev_dt = du.parse_dt(ev["occurred_at"])
                st.caption(
                    f"{labels.get(ev['event_type'], ev['event_type'])} — "
                    f"{du.friendly_date(ev_dt.date()) if ev_dt else '—'} {tu.fmt_time(ev_dt)}"
                )
    st.markdown("</div>", unsafe_allow_html=True)


def render_completed_row(task: dict) -> None:
    """Compact read-only row for completed tasks (history / All Tasks)."""
    meta = task_service.PRIORITY_META.get(task["priority"], task_service.PRIORITY_META["medium"])
    comp = du.parse_dt(task.get("completed_at"))
    created = du.parse_dt(task.get("created_at"))
    duration = tu.seconds_to_human(task.get("actual_duration"))
    basis = "tracked" if task.get("duration_basis") == "tracked" else "elapsed"
    est = f" · est. {tu.seconds_to_human(task['estimated_duration'])}" if task.get("estimated_duration") else ""
    delta = ""
    if task.get("estimated_duration") and task.get("actual_duration") is not None:
        diff = task["actual_duration"] - task["estimated_duration"]
        sign = "+" if diff >= 0 else "−"
        delta = f" ({sign}{tu.seconds_to_human(abs(diff))} vs est.)"
    carries = int(task.get("carry_forward_count") or 0)
    carry = f" · 🔁 ×{carries}" if carries else ""
    st.markdown(
        f"<div class='done-row'>"
        f"<span class='done-check'>✅</span>"
        f"<span class='done-title'>{html.escape(task['title'])}</span>"
        f"<span class='prio-badge prio-{task['priority']}'>{meta['icon']} {meta['label']}</span>"
        f"<span class='done-meta'>created {du.friendly_date(created.date()) if created else '—'}"
        f" · done {du.day_label(comp.date() if comp else None, du.today())} {tu.fmt_time(comp)}"
        f" · ⏱ {duration} ({basis}){est}{delta}{carry}</span>"
        f"</div>",
        unsafe_allow_html=True,
    )
