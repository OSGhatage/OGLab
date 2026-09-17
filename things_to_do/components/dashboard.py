"""Shared dashboard widgets: header, metric strip, level strip, charts."""
from __future__ import annotations

import plotly.graph_objects as go
import streamlit as st

import utils.date_utils as du
import utils.time_utils as tu
from components import _compat as cx
from components import notification_center
from services import reward_service, task_service

_COLORS = {"immediate": "#7c3aed", "high": "#ef4444", "medium": "#f59e0b", "low": "#22c55e"}


def render_header() -> None:
    """App name, current date, productivity summary and the notification bell."""
    stats = task_service.today_stats()
    streak = reward_service.get_streak()
    if stats["planned_total"] == 0:
        summary = "No tasks for today yet — add your first one."
    else:
        parts = [f"{stats['completed_today']} of {stats['planned_total']} tasks done"]
        if stats["tracked_seconds"]:
            parts.append(f"{tu.seconds_to_human(stats['tracked_seconds'])} tracked")
        if streak["current"]:
            parts.append(f"🔥 {streak['current']}-day streak")
        summary = " · ".join(parts)
    left, right = st.columns([3, 1], vertical_alignment="bottom")
    with left:
        st.markdown('<div class="app-title">✅ Things To Do</div>', unsafe_allow_html=True)
        st.markdown(
            f'<div class="app-subtitle">{du.today().strftime("%A, %B %d, %Y")} · {summary}</div>',
            unsafe_allow_html=True,
        )
    with right:
        notification_center.render_bell()


def render_metrics() -> None:
    stats = task_service.today_stats()
    rate = f"{stats['rate']:.0f}%" if stats["rate"] is not None else "—"
    m1, m2, m3, m4, m5 = st.columns(5)
    m1.metric("Today's tasks", stats["planned_total"])
    m2.metric("Completed", stats["completed_today"])
    m3.metric("Remaining", stats["remaining"])
    m4.metric("Completion rate", rate)
    m5.metric("Time tracked", tu.seconds_to_human(stats["tracked_seconds"], default="0m"))


def render_level_strip() -> None:
    """Level / XP progress and streak cards."""
    info = reward_service.level_info()
    streak = reward_service.get_streak()
    bar = int(round(info["pct"]))
    col1, col2 = st.columns([3, 2])
    with col1:
        st.markdown(
            f"""
            <div class="level-card">
              <div class="level-head">
                <span class="level-name">🏅 Level {info['level']}</span>
                <span class="level-xp">{info['total']} / {info['next']} XP</span>
              </div>
              <div class="xp-track"><div class="xp-fill" style="width:{bar}%"></div></div>
              <div class="level-foot">{info['remaining']} XP until next level
                &nbsp;·&nbsp; today +{reward_service.xp_today()}
                &nbsp;·&nbsp; week +{reward_service.xp_this_week()}
                &nbsp;·&nbsp; month +{reward_service.xp_this_month()}</div>
            </div>
            """,
            unsafe_allow_html=True,
        )
    with col2:
        unit = "day" if streak["current"] == 1 else "days"
        st.markdown(
            f"""
            <div class="level-card">
              <div class="level-head">
                <span class="level-name">🔥 Streak</span>
                <span class="level-xp">🏆 Best: {streak['longest']} days</span>
              </div>
              <div class="streak-num">{streak['current']} <span class="streak-unit">consecutive {unit}</span></div>
            </div>
            """,
            unsafe_allow_html=True,
        )


# ------------------------------------------------------------------ charts
def _base_layout(fig: go.Figure, height: int = 220) -> None:
    fig.update_layout(
        height=height,
        margin=dict(l=0, r=0, t=8, b=0),
        plot_bgcolor="rgba(0,0,0,0)",
        paper_bgcolor="rgba(0,0,0,0)",
        xaxis=dict(showgrid=False, tickangle=-45),
        yaxis=dict(showgrid=False),
        showlegend=False,
    )


def render_completed_chart(days: int | list = 14) -> None:
    if isinstance(days, list):
        dates = list(days)
    else:
        ref = du.today()
        dates = [du.add_days(ref, -i) for i in range(days - 1, -1, -1)]
    counts = task_service.count_completed_per_day(dates)
    x = [d.strftime("%a %d") for d in dates]
    y = [counts[du.date_str(d)] for d in dates]
    fig = go.Figure(go.Bar(x=x, y=y, marker_color="#6366f1", marker_line_width=0))
    _base_layout(fig)
    fig.update_yaxes(range=[0, max(max(y), 1) * 1.15], dtick=1)
    cx.plotly(fig)


def render_xp_chart(days: int = 7) -> None:
    ref = du.today()
    series = reward_service.xp_by_day(days, ref)
    keys = sorted(series)
    x = [du.parse_date(k).strftime("%a %d") for k in keys]
    y = [series[k] for k in keys]
    fig = go.Figure(go.Scatter(
        x=x, y=y, mode="lines+markers",
        line=dict(color="#f59e0b", width=3),
        fill="tozeroy", fillcolor="rgba(245,158,11,0.15)",
    ))
    _base_layout(fig)
    fig.update_yaxes(range=[0, max(max(y), 1) * 1.25], dtick=1)
    cx.plotly(fig)


def render_priority_donut(tasks: list[dict]) -> None:
    if not tasks:
        st.caption("No active tasks right now.")
        return
    counts: dict[str, int] = {}
    for t in tasks:
        counts[t["priority"]] = counts.get(t["priority"], 0) + 1
    order = [p for p in ("immediate", "high", "medium", "low") if counts.get(p)]
    labels = [task_service.PRIORITY_META[p]["icon"] + " " + task_service.PRIORITY_META[p]["label"] for p in order]
    values = [counts[p] for p in order]
    fig = go.Figure(go.Pie(
        labels=labels, values=values, hole=0.55,
        marker_colors=[_COLORS[p] for p in order],
        textinfo="percent",
    ))
    fig.update_layout(
        height=260, margin=dict(l=0, r=0, t=8, b=0),
        showlegend=True, legend=dict(orientation="h", y=1.15),
    )
    cx.plotly(fig)


def render_est_vs_actual(tasks: list[dict]) -> None:
    rows = [t for t in tasks if t.get("estimated_duration") and t.get("actual_duration") is not None][-8:]
    if not rows:
        st.caption("Complete a few tasks with estimates to compare planned vs actual time.")
        return
    x = [t["title"][:18] for t in rows]
    fig = go.Figure()
    fig.add_bar(x=x, y=[(t["estimated_duration"] or 0) / 60 for t in rows], name="Estimated", marker_color="#c7d2fe")
    fig.add_bar(x=x, y=[(t["actual_duration"] or 0) / 60 for t in rows], name="Actual", marker_color="#6366f1")
    _base_layout(fig, height=280)
    fig.update_layout(barmode="group", showlegend=True, legend=dict(orientation="h", y=1.2),
                      yaxis_title="minutes", yaxis=dict(showgrid=False, title="minutes"))
    cx.plotly(fig)
