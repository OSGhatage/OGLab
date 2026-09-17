"""Page renderers for the six sidebar sections.

* Today             — landing page: metrics, active task cards, add-task form
* All Tasks         — search + filters across every task
* Completed History — period/priority filters, summary stats, detail table
* Calendar          — month grid with per-day details
* Analytics         — compact charts + achievements + reward history
* Settings          — notification & reward configuration, data management
"""
from __future__ import annotations

import calendar as _calendar
import json
import re
from datetime import date, time, timedelta

import streamlit as st

import utils.date_utils as du
import utils.time_utils as tu
from components import _compat as cx
from components import dashboard as dash
from components import notification_center as ncenter
from components import reward_panel as rpanel
from components import task_card as tcard
from components import task_form as tform
from database import db
from services import reward_service, settings_service, task_service
from worker import reminder_worker


# -------------------------------------------------------------------- Today
def render_today() -> None:
    tasks = task_service.get_due_tasks()
    running = any(t.get("start_time") and not t.get("is_paused") for t in tasks)

    dash.render_metrics()
    dash.render_level_strip()

    left, right = st.columns([2, 1], gap="large")
    with left:
        # Re-render the live list every 2 s while any timer runs (ticks the
        # elapsed chip + picks up new notifications); static otherwise.
        @st.fragment(run_every=2 if running else None)
        def _live_tasks():
            _render_task_list(tasks, interactive=True)
            ncenter.play_sound_if_new_notifications()

        _live_tasks()
    with right:
        tform.render_add_task_form()
        rpanel.render_today_progress()
        with st.expander("📈 Completed per day (14d)"):
            dash.render_completed_chart(14)
        ncenter.render_recent_section(5)

    upcoming = task_service.get_upcoming_tasks()
    if upcoming:
        with st.expander(f"🗓 Upcoming ({len(upcoming)})"):
            for t in upcoming:
                tcard.render_task_card(t, interactive=False)


def _render_task_list(tasks: list[dict], interactive: bool) -> None:
    if not tasks:
        st.markdown(
            "<div class='empty-state'>🌤 <b>Nothing due today.</b><br>"
            "Enjoy the calm — or add a task on the right.</div>",
            unsafe_allow_html=True,
        )
        return
    highlight_id = st.session_state.pop("ttd_highlight_task", None)
    st.markdown(
        f"<div class='section-title'>Active tasks <span class='count-badge'>{len(tasks)}</span></div>",
        unsafe_allow_html=True,
    )
    for task in tasks:
        tcard.render_task_card(task, interactive=interactive, highlight=task["id"] == highlight_id)


# ---------------------------------------------------------------- All Tasks
def render_all_tasks() -> None:
    st.markdown("## 📋 All Tasks")
    f1, f2, f3, f4 = st.columns([2, 1.4, 1, 1.2])
    with f1:
        keyword = st.text_input("Search", placeholder="Keyword in title or notes…", key="at_kw")
    with f2:
        sel = st.multiselect(
            "Priority", task_service.PRIORITIES, default=[],
            format_func=task_service.priority_label, key="at_prio",
        )
    with f3:
        status = st.selectbox("Status", ["Active", "Completed", "All"], key="at_status")
    with f4:
        date_filter = st.selectbox("Planned date", ["Any", "Today", "Overdue", "Custom"], key="at_df")

    planned_key = None
    if date_filter == "Custom":
        chosen = st.date_input("Planned date (custom)", value=du.today(), key="at_date_custom")
        planned_key = du.date_str(chosen)
    elif date_filter == "Today":
        planned_key = du.date_str(du.today())

    status_val = {"Active": "active", "Completed": "completed"}.get(status)
    tasks = task_service.list_tasks(
        status=status_val,
        keyword=keyword or None,
        priorities=list(sel) or None,
        planned_date=planned_key,
    )
    if date_filter == "Overdue":
        today_key = du.date_str(du.today())
        tasks = [t for t in tasks if t["status"] == "active" and t["planned_date"] < today_key]

    if not tasks:
        st.info("No tasks match the current filters.")
        return
    st.caption(f"{len(tasks)} task(s)")
    active = [t for t in tasks if t["status"] == task_service.ACTIVE]
    completed = [t for t in tasks if t["status"] == task_service.COMPLETED]
    for t in active:
        tcard.render_task_card(t, interactive=True)
    if completed:
        st.markdown("<div class='section-title'>Completed</div>", unsafe_allow_html=True)
        for t in completed:
            tcard.render_completed_row(t)


# ------------------------------------------------------------------ History
_PERIODS = {
    "Today": "today",
    "Yesterday": "yesterday",
    "Last 7 days": "7d",
    "This month": "month",
    "Custom range": "custom",
    "All time": "all",
}


def render_history() -> None:
    st.markdown("## 📜 Completed History")
    f1, f2, f3, f4 = st.columns(4)
    with f1:
        period = st.selectbox("Period", list(_PERIODS), key="h_period")
    with f2:
        prio = st.selectbox(
            "Priority", ["Any"] + list(task_service.PRIORITIES),
            format_func=lambda p: "Any priority" if p == "Any" else task_service.priority_label(p),
            key="h_prio",
        )
    custom_from = custom_to = None
    if period == "Custom range":
        with f3:
            custom_from = st.date_input("From", value=du.add_days(du.today(), -6), key="h_from")
        with f4:
            custom_to = st.date_input("To", value=du.today(), key="h_to")

    start, end = du.period_bounds(_PERIODS[period], du.today(), custom_from, custom_to)
    tasks = task_service.get_completed_between(start, end)
    if prio != "Any":
        tasks = [t for t in tasks if t["priority"] == prio]

    tracked_tasks = [t for t in tasks if t.get("duration_basis") == "tracked"
                     and t.get("actual_duration") is not None]
    tracked_total = sum(t["actual_duration"] for t in tracked_tasks)
    avg = tracked_total / len(tracked_tasks) if tracked_tasks else None
    est_total = sum(t["estimated_duration"] for t in tasks if t.get("estimated_duration"))
    act_total = sum(t["actual_duration"] for t in tasks if t.get("actual_duration") is not None)
    high = [t for t in tasks if t["priority"] in ("high", "immediate")]

    m1, m2, m3, m4, m5, m6 = st.columns(6)
    m1.metric("Completed", len(tasks))
    m2.metric("Total tracked", tu.seconds_to_human(tracked_total, default="0m"))
    m3.metric("Avg completion", tu.seconds_to_human(avg, default="—"))
    m4.metric("High-priority", len(high))
    m5.metric("Estimated", tu.seconds_to_human(est_total, default="—"))
    delta = None
    if est_total:
        diff = act_total - est_total
        delta = f"{'+' if diff >= 0 else '−'}{tu.seconds_to_human(abs(diff))}"
    m6.metric("Actual", tu.seconds_to_human(act_total, default="—"), delta=delta,
              delta_color="inverse" if (delta and delta.startswith("+")) else "normal")

    if tasks:
        if start and end and (end - start).days <= 31:
            days = [start + timedelta(days=i) for i in range((end - start).days + 1)]
        else:
            days = [du.add_days(du.today(), -i) for i in range(13, -1, -1)]
        dash.render_completed_chart(days)

    if not tasks:
        st.info("Nothing completed in this period yet.")
        return
    data = []
    for t in tasks:
        created = du.parse_dt(t.get("created_at"))
        comp = du.parse_dt(t.get("completed_at"))
        meta = task_service.PRIORITY_META.get(t["priority"], task_service.PRIORITY_META["medium"])
        est = t.get("estimated_duration")
        act = t.get("actual_duration")
        delta_s = "—"
        if est and act is not None:
            d = act - est
            delta_s = f"+{tu.seconds_to_human(d)}" if d >= 0 else f"−{tu.seconds_to_human(-d)}"
        data.append({
            "Task": t["title"],
            "Priority": meta["icon"] + " " + meta["label"],
            "Created": du.friendly_date(created.date()) if created else "—",
            "Completed": f"{du.day_label(comp.date() if comp else None, du.today())} {tu.fmt_time(comp)}" if comp else "—",
            "Duration": tu.seconds_to_human(act),
            "Basis": t.get("duration_basis") or "—",
            "Estimate": tu.seconds_to_human(est) if est else "—",
            "Δ est": delta_s,
            "Carries": t.get("carry_forward_count") or 0,
        })
    cx.dataframe(data, height=min(520, 60 + 35 * len(data)))


# ----------------------------------------------------------------- Calendar
def render_calendar() -> None:
    st.markdown("## 🗓 Calendar")
    today = du.today()
    st.session_state.setdefault("cal_ref", today)
    st.session_state.setdefault("cal_sel", today)
    ref: date = st.session_state.cal_ref
    sel: date = st.session_state.cal_sel

    nav1, nav2, nav3, nav4 = st.columns([1, 1, 2, 1])
    with nav1:
        if cx.button("← Prev", key="cal_prev"):
            st.session_state.cal_ref = (ref.replace(day=1) - timedelta(days=1)).replace(day=1)
            st.rerun()
    with nav2:
        if cx.button("Next →", key="cal_next"):
            st.session_state.cal_ref = (ref.replace(day=1) + timedelta(days=32)).replace(day=1)
            st.rerun()
    with nav3:
        st.markdown(f"<div class='cal-month'>{du.month_name(ref)}</div>", unsafe_allow_html=True)
    with nav4:
        if cx.button("Today", key="cal_today"):
            st.session_state.cal_ref = today
            st.session_state.cal_sel = today
            st.rerun()

    active_counts = {r["d"]: int(r["c"]) for r in db.query(
        "SELECT planned_date AS d, COUNT(*) AS c FROM tasks WHERE status = 'active' GROUP BY planned_date")}
    done_counts = {r["d"]: int(r["c"]) for r in db.query(
        "SELECT date(completed_at) AS d, COUNT(*) AS c FROM tasks WHERE status = 'completed' GROUP BY date(completed_at)")}

    st.markdown(
        "<div class='cal-weekdays'>"
        + "".join(f"<div>{w}</div>" for w in ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"])
        + "</div>",
        unsafe_allow_html=True,
    )
    first = ref.replace(day=1)
    cells: list[date | None] = [None] * first.weekday()
    cells += [date(ref.year, ref.month, n) for n in range(1, _calendar.monthrange(ref.year, ref.month)[1] + 1)]
    while len(cells) % 7:
        cells.append(None)

    for w in range(0, len(cells), 7):
        cols = st.columns(7, gap="small")
        for i, d in enumerate(cells[w:w + 7]):
            with cols[i]:
                if d is None:
                    st.write("")
                    continue
                key = du.date_str(d)
                a = active_counts.get(key, 0)
                c = done_counts.get(key, 0)
                badge = ""
                if c:
                    badge += f" ✅{c}"
                if a:
                    badge += f" 📌{a}"
                if cx.button(
                    f"{d.day}{badge}", key=f"cal_d_{key}",
                    type="primary" if d == sel else "secondary",
                    help=f"Active/planned: {a} · Completed: {c}",
                ):
                    st.session_state.cal_sel = d
                    st.rerun()

    # ----- selected day detail -----
    st.markdown(
        f"<div class='section-title'>{du.day_label(sel, today)} — {du.friendly_date(sel)}</div>",
        unsafe_allow_html=True,
    )
    planned = task_service.list_tasks(planned_date=sel)
    planned_active = [t for t in planned if t["status"] == task_service.ACTIVE]
    completed_day = task_service.get_completed_between(sel, sel)
    carried_rows = db.query(
        "SELECT e.occurred_at, e.details, t.title FROM task_events e "
        "JOIN tasks t ON t.id = e.task_id "
        "WHERE e.event_type = 'carried_forward' AND date(e.occurred_at) = ? ORDER BY e.id",
        (du.date_str(sel),),
    )

    if planned_active:
        label = "📌 Active / planned" if sel <= today else "📌 Planned (upcoming)"
        st.markdown(f"**{label} ({len(planned_active)})**")
        for t in planned_active:
            tcard.render_task_card(t, interactive=sel == today)
    if completed_day:
        st.markdown(f"**✅ Completed ({len(completed_day)})**")
        for t in completed_day:
            tcard.render_completed_row(t)
    if carried_rows:
        st.markdown(f"**🔁 Carried forward ({len(carried_rows)})**")
        for r in carried_rows:
            details = json.loads(r["details"] or "{}")
            frm = du.friendly_date(du.parse_date(details.get("from")))
            to = du.friendly_date(du.parse_date(details.get("to")))
            st.caption(f"🔁 {r['title']} — from {frm} to {to}")
    if not (planned or completed_day or carried_rows):
        st.caption("No activity on this day.")


# ---------------------------------------------------------------- Analytics
def render_analytics() -> None:
    st.markdown("## 📊 Analytics")
    total = int(db.query_one("SELECT COUNT(*) AS c FROM tasks")["c"])
    done = int(db.query_one("SELECT COUNT(*) AS c FROM tasks WHERE status = 'completed'")["c"])
    info = reward_service.level_info()
    streak = reward_service.get_streak()
    m1, m2, m3, m4 = st.columns(4)
    m1.metric("Total tasks", total)
    m2.metric("Completed", done)
    m3.metric("Level", info["level"])
    m4.metric("Longest streak", f"{streak['longest']} 🔥")

    a, b = st.columns(2)
    with a:
        st.markdown("**Completed per day**")
        dash.render_completed_chart(14)
    with b:
        st.markdown("**XP per day**")
        dash.render_xp_chart(7)
    c, d = st.columns(2)
    with c:
        st.markdown("**Active tasks by priority**")
        dash.render_priority_donut(task_service.list_tasks(status=task_service.ACTIVE))
    with d:
        st.markdown("**Estimated vs actual (minutes)**")
        dash.render_est_vs_actual(task_service.get_completed_between(None, None))

    st.markdown("**🏆 Achievements**")
    rpanel.render_achievements()
    st.markdown("**Reward history**")
    rpanel.render_reward_history(30)


# ----------------------------------------------------------------- Settings
_INTERVALS = [900, 1800, 3600, 7200, 14400, 28800, 43200, 86400]


def _iv_label(seconds: int) -> str:
    seconds = int(seconds)
    if seconds % 3600 == 0:
        return f"{seconds // 3600}h"
    return f"{seconds // 60}m"


def _nearest_index(value, options) -> int:
    try:
        v = int(value)
    except (TypeError, ValueError):
        return 3
    return min(range(len(options)), key=lambda i: abs(options[i] - v))


def render_settings() -> None:
    st.markdown("## ⚙️ Settings")
    n = settings_service.get_section("notifications")
    r = settings_service.get_section("rewards")

    col1, col2 = st.columns(2, gap="large")
    with col1:
        with st.container(border=True):
            st.markdown("### 🔔 Notifications")
            enabled = st.checkbox("Enable notifications", value=bool(n.get("enabled", True)), key="s_n_en")
            c1, c2 = st.columns(2)
            with c1:
                default_iv = st.selectbox(
                    "Default reminder interval", _INTERVALS,
                    index=_nearest_index(n.get("default_interval", 7200), _INTERVALS),
                    format_func=_iv_label, key="s_n_def",
                )
            with c2:
                high_iv = st.selectbox(
                    "High / Immediate priority interval", _INTERVALS,
                    index=_nearest_index(n.get("high_priority_interval", 7200), _INTERVALS),
                    format_func=_iv_label, key="s_n_high",
                )
            long_thr = st.number_input(
                "Long-task threshold (hours)", min_value=1, max_value=72,
                value=int(n.get("long_task_threshold_hours", 4)), key="s_n_long",
            )
            cq1, cq2 = st.columns(2)
            with cq1:
                quiet_start = st.time_input("Quiet hours start", value=time(22, 0), key="s_n_qs")
            with cq2:
                quiet_end = st.time_input("Quiet hours end", value=time(7, 0), key="s_n_qe")
            summary_hour = st.time_input("Daily summary time", value=time(21, 0), key="s_n_sum")
            sound = st.checkbox("Notification sound (in-app)", value=bool(n.get("sound", True)), key="s_n_sound")
            high_only = st.checkbox(
                "Only remind for High / Immediate priority tasks",
                value=bool(n.get("high_only", False)), key="s_n_highonly",
            )
            st.markdown("**Browser notification permission**")
            ncenter.render_browser_permission()

    with col2:
        with st.container(border=True):
            st.markdown("### 🏅 Rewards")
            xp = r.get("xp", {})
            bonuses = r.get("bonuses", {})
            xp_default = {"low": 5, "medium": 10, "high": 20, "immediate": 30}
            c1, c2 = st.columns(2)
            xp_inputs: dict[str, float] = {}
            for i, p in enumerate(task_service.PRIORITIES):
                with (c1, c2)[i % 2]:
                    xp_inputs[p] = st.number_input(
                        f"XP · {task_service.priority_label(p)}",
                        min_value=0, max_value=500,
                        value=int(xp.get(p, xp_default[p])), key=f"s_r_xp_{p}",
                    )
            b1, b2, b3 = st.columns(3)
            with b1:
                be = st.number_input("Bonus · before estimate", min_value=0, max_value=100,
                                     value=int(bonuses.get("before_estimate", 5)), key="s_r_be")
            with b2:
                od = st.number_input("Bonus · on planned date", min_value=0, max_value=100,
                                     value=int(bonuses.get("on_planned_date", 5)), key="s_r_od")
            with b3:
                hb = st.number_input("Bonus · high priority", min_value=0, max_value=100,
                                     value=int(bonuses.get("high_priority", 5)), key="s_r_hb")
            mc, mx = st.columns(2)
            with mc:
                milestone_n = st.number_input(
                    "Daily milestone (tasks per day)", min_value=2, max_value=50,
                    value=int(r.get("daily_milestone_count", 5)), key="s_r_mn",
                )
            with mx:
                milestone_xp = st.number_input(
                    "Daily milestone XP", min_value=0, max_value=200,
                    value=int(r.get("daily_milestone_xp", 10)), key="s_r_mx",
                )
            levels_text = st.text_input(
                "Level thresholds (XP, comma-separated)",
                value=", ".join(str(t) for t in r.get("level_thresholds", [])),
                key="s_r_levels",
                help="XP needed for each level, e.g. 0, 100, 250, 500, 900 …",
            )
            st.markdown("---")
            if cx.button("💾 Save settings", type="primary"):
                numbers = [int(x) for x in re.findall(r"\d+", levels_text)]
                if not numbers or numbers[0] != 0 or numbers != sorted(set(numbers)):
                    st.error("Level thresholds must start at 0 and be strictly increasing.")
                else:
                    settings_service.set_section("notifications", {
                        "enabled": bool(enabled),
                        "sound": bool(sound),
                        "high_only": bool(high_only),
                        "default_interval": int(default_iv),
                        "high_priority_interval": int(high_iv),
                        "long_task_threshold_hours": int(long_thr),
                        "quiet_start": quiet_start.strftime("%H:%M"),
                        "quiet_end": quiet_end.strftime("%H:%M"),
                        "summary_hour": summary_hour.strftime("%H:%M"),
                    })
                    settings_service.set_section("rewards", {
                        "xp": {p: int(v) for p, v in xp_inputs.items()},
                        "bonuses": {
                            "before_estimate": int(be),
                            "on_planned_date": int(od),
                            "high_priority": int(hb),
                        },
                        "daily_milestone_count": int(milestone_n),
                        "daily_milestone_xp": int(milestone_xp),
                        "level_thresholds": numbers,
                    })
                    st.toast("Settings saved", icon="✅")
                    st.rerun()

    with st.container(border=True):
        st.markdown("### 🗄 Data & background worker")
        st.caption(f"SQLite database: `{db.db_path()}` — created and migrated automatically on first run.")
        worker = reminder_worker.get_worker()
        status = "🟢 running" if (worker and worker.is_alive()) else "🔴 not running"
        last = f" · last check {tu.time_ago(du.iso_dt(worker.last_cycle_at))}" if (worker and worker.last_cycle_at) else ""
        st.caption(f"Reminder worker: {status}{last}")
        st.caption("Standalone worker (headless deployment): `python -m worker.reminder_worker`")

        st.markdown("### ⚠️ Danger zone")
        confirm = st.checkbox(
            "I understand this deletes all tasks, history, rewards and settings.", key="s_danger"
        )
        if confirm and cx.button("🗑 Reset all data"):
            task_service.reset_all()
            st.session_state.pop("s_danger", None)
            st.toast("All data reset", icon="🗑")
            st.rerun()
