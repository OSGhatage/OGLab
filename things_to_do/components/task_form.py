"""Add-task form.

Submissions go through :func:`task_service.create_task` so validation,
carry-forward defaults and timer start are identical to every other entry
point.  All widgets use stable keys so headless tests (and Streamlit's
reruns) can find them.
"""
from __future__ import annotations

import streamlit as st

import utils.date_utils as du
from services import task_service


def render_add_task_form() -> None:
    with st.form("add_task_form", clear_on_submit=True, border=True):
        st.markdown("<div class='form-title'>➕ Add task</div>", unsafe_allow_html=True)
        title = st.text_input("Task title", key="tf_title", placeholder="e.g. Process bathymetry tiles")
        notes = st.text_area("Notes (optional)", key="tf_notes", height=68)
        c1, c2 = st.columns(2)
        with c1:
            priority = st.selectbox(
                "Priority", task_service.PRIORITIES, index=2,
                format_func=task_service.priority_label, key="tf_prio",
            )
        with c2:
            planned = st.date_input("Planned date", value=du.today(), key="tf_date")
        c3, c4 = st.columns(2)
        with c3:
            estimate_min = st.number_input(
                "Estimate (minutes)", min_value=0, max_value=1440, step=15, value=0, key="tf_est",
                help="Leave 0 for no estimate.",
            )
        with c4:
            start_timer = st.checkbox("⏱ Start timer on add", value=False, key="tf_timer")
        reminders = st.checkbox("🔔 Enable reminders", value=True, key="tf_reminders")
        submitted = st.form_submit_button("Add task", type="primary", use_container_width=True, key="tf_submit")
    if submitted:
        task_id, error = task_service.create_task(
            title, notes, priority, planned, float(estimate_min), bool(start_timer), bool(reminders)
        )
        if error:
            st.error(error, icon="⚠️")
        else:
            st.toast(f"Added “{(title or '').strip()}”", icon="✅")
            st.rerun()
