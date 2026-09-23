"""Things To Do — personal task-management dashboard.

Run from the ``things_to_do/`` folder:

    streamlit run app.py

The database (``data/tasks.db``) is created and migrated automatically on
first start, and a lightweight background worker is started in-process to
handle reminders, carry-forward and the daily summary.
"""
from __future__ import annotations

from pathlib import Path

import streamlit as st

from components import dashboard as dash
from components import pages
from database import db
from services import task_service
from worker import reminder_worker

st.set_page_config(
    page_title="Things To Do",
    page_icon="✅",
    layout="wide",
    initial_sidebar_state="expanded",
)

with open(Path(__file__).resolve().parent / "assets" / "style.css", encoding="utf-8") as fh:
    st.markdown(f"<style>{fh.read()}</style>", unsafe_allow_html=True)

# ---- bootstrap (idempotent): database + background worker + day roll ----
db.init_db()
reminder_worker.ensure_worker_started()
task_service.roll_day()

# ---------------------------------------------------------------- sidebar
st.sidebar.markdown('<div class="side-brand">✅ Things To Do</div>', unsafe_allow_html=True)
PAGES = {
    "📌 Today": pages.render_today,
    "📋 All Tasks": pages.render_all_tasks,
    "📜 Completed History": pages.render_history,
    "🗓 Calendar": pages.render_calendar,
    "📊 Analytics": pages.render_analytics,
    "⚙️ Settings": pages.render_settings,
}
choice = st.sidebar.radio("Navigate", list(PAGES), label_visibility="collapsed", key="nav")
worker = reminder_worker.get_worker()
st.sidebar.caption(
    f"Worker: {'🟢 on' if (worker and worker.is_alive()) else '🔴 off'} · DB: `{db.db_path().name}`"
)

# -------------------------------------------------------------------- page
dash.render_header()
PAGES[choice]()
