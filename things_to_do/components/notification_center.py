"""In-app notification center.

The bell shows the unread count from SQLite, so the state is shared by all
pages and survives restarts.  Snooze actions defer the underlying task's
reminders (30 min / 1 h / 2 h / tomorrow); dismissing hides the notification
without touching the task.
"""
from __future__ import annotations

from datetime import timedelta

import streamlit as st

import utils.date_utils as du
import utils.time_utils as tu
from components import _compat as cx
from services import notification_service, settings_service

TYPE_ICONS = {
    "reminder": "🔔",
    "escalation": "⚠️",
    "overdue": "⏰",
    "summary": "🌙",
    "achievement": "🏆",
    "streak": "🔥",
    "info": "ℹ️",
}

REMINDER_LIKE = ("reminder", "escalation", "overdue")

_BEEP_HTML = """
<html><head><meta name="viewport" content="width=0, initial-scale=1">
<script>
(function () {
  try {
    var Ctx = window.AudioContext || window.webkitAudioContext;
    if (!Ctx) return;
    var ctx = new Ctx();
    var o = ctx.createOscillator();
    var g = ctx.createGain();
    o.connect(g); g.connect(ctx.destination);
    o.type = "sine"; o.frequency.value = 880;
    g.gain.setValueAtTime(0.0001, ctx.currentTime);
    g.gain.exponentialRampToValueAtTime(0.15, ctx.currentTime + 0.03);
    g.gain.exponentialRampToValueAtTime(0.0001, ctx.currentTime + 0.5);
    o.start(); o.stop(ctx.currentTime + 0.55);
  } catch (e) { /* autoplay blocked — graceful no-op */ }
})();
</script></head><body style="margin:0;height:0"></body></html>
"""

_BROWSER_PERMISSION_HTML = """
<html><head><style>
  body { font-family: inherit; font-size: 0.85rem; color: #334155; padding: 4px; }
  button { margin-top: 6px; padding: 4px 10px; border-radius: 8px; border: 1px solid #c7d2fe;
           background: #eef2ff; color: #4338ca; font-weight: 600; cursor: pointer; }
  .ok { color: #16a34a; font-weight: 700; } .bad { color: #dc2626; font-weight: 700; }
  .warn { color: #d97706; font-weight: 700; }
  .note { font-size: 0.72rem; color: #94a3b8; margin-top: 8px; line-height: 1.4; }
</style></head><body>
<b>Browser notification permission:</b> <span id="np">checking…</span>
<button onclick="req()">Request permission</button>
<div class="note">Streamlit embeds this panel in a sandboxed frame — some
browsers always report <b>denied</b> inside iframes. The in-app notification
center above works regardless, and reminders never depend on this.</div>
<script>
function render() {
  var p = ("Notification" in window) ? Notification.permission : "unsupported";
  var el = document.getElementById("np");
  el.textContent = p;
  el.className = p === "granted" ? "ok" : (p === "denied" ? "bad" : "warn");
}
function req() {
  if (!("Notification" in window)) { render(); return; }
  Notification.requestPermission().then(function () {
    render();
    try {
      if (Notification.permission === "granted") {
        new Notification("Things To Do", { body: "Browser notifications enabled!" });
      }
    } catch (e) { /* iframe may block constructor — fine, in-app center covers it */ }
  });
}
render();
</script></body></html>
"""


def render_bell() -> None:
    unread = notification_service.unread_count()
    label = f"🔔 {unread} new" if unread else "🔔"
    with st.popover(label):
        _render_feed(limit=20, key_prefix="bell_")


def render_recent_section(limit: int = 5) -> None:
    with st.expander(f"🔔 Notifications ({notification_service.unread_count()} unread)"):
        _render_feed(limit=limit, key_prefix="feed_")


def _snooze(notification: dict, minutes: int | None) -> None:
    until = du.next_midnight(du.now()) if minutes is None else du.now() + timedelta(minutes=minutes)
    notification_service.snooze(notification["id"], notification.get("task_id"), until)
    st.toast(f"Snoozed until {tu.fmt_time(until)}" if minutes else "Snoozed until tomorrow", icon="💤")
    st.rerun()


def _render_feed(limit: int = 20, key_prefix: str = "feed_") -> None:
    """Render the notification feed.

    *key_prefix* keeps widget keys unique when the feed is rendered in more
    than one place on the same page (header bell + Today section).
    """
    items = notification_service.list_recent(limit=limit)
    if not items:
        st.markdown(
            "<div class='notif-empty'>📭 No notifications yet — reminders, "
            "overdue warnings, summaries and achievements will appear here.</div>",
            unsafe_allow_html=True,
        )
        return
    if notification_service.unread_count():
        if st.button("Mark all as read", use_container_width=True, key=f"{key_prefix}mark_all"):
            notification_service.mark_read(None)
            st.rerun()
    for item in items:
        icon = TYPE_ICONS.get(item["ntype"], "🔔")
        unread_dot = " <span class='unread-dot'></span>" if not item["is_read"] else ""
        st.markdown(
            f"<div class='notif-row {'notif-unread' if not item['is_read'] else ''}'>"
            f"<div class='notif-title'>{icon} {item['title']}{unread_dot}</div>"
            + (f"<div class='notif-body'>{item['body']}</div>" if item.get("body") else "")
            + f"<div class='notif-time'>{tu.time_ago(item['created_at'])}</div></div>",
            unsafe_allow_html=True,
        )
        if item["ntype"] in REMINDER_LIKE and item.get("task_id"):
            cols = st.columns([1.1, 1.0, 0.9, 0.9, 1.3])
            with cols[0]:
                if not item["is_read"] and st.button("Mark read", key=f"{key_prefix}read_{item['id']}"):
                    notification_service.mark_read(item["id"])
                    st.rerun()
            with cols[1]:
                if st.button("Dismiss", key=f"{key_prefix}dismiss_{item['id']}"):
                    notification_service.dismiss(item["id"])
                    st.rerun()
            for col, (label, minutes) in zip(
                cols[2:], [("💤 30m", 30), ("💤 1h", 60), ("💤 2h", 120), ("💤 Tomorrow", None)]
            ):
                with col:
                    if st.button(label, key=f"{key_prefix}snooze_{item['id']}_{minutes}"):
                        _snooze(item, minutes)


def play_sound_if_new_notifications() -> None:
    """Play a short in-app beep when the unread count increased since last check."""
    unread = notification_service.unread_count()
    prev = st.session_state.get("ttd_last_unread")
    sound_on = settings_service.get_section("notifications").get("sound", True)
    if prev is not None and unread > prev and sound_on:
        cx.html(_BEEP_HTML, height=0)
    st.session_state.ttd_last_unread = unread


def render_browser_permission() -> None:
    cx.html(_BROWSER_PERMISSION_HTML, height=190)
