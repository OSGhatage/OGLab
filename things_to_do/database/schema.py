"""DDL for the initial schema plus default settings and the achievement catalog.

Timestamps are stored as ISO-8601 strings in *local* time; calendar dates as
``YYYY-MM-DD``.  Durations are stored in seconds (timers accumulate in
milliseconds so pause/resume is exact).
"""
from __future__ import annotations

SCHEMA_VERSION = 1

DDL: list[str] = [
    """
    CREATE TABLE IF NOT EXISTS tasks (
        id TEXT PRIMARY KEY,
        title TEXT NOT NULL,
        description TEXT NOT NULL DEFAULT '',
        priority TEXT NOT NULL DEFAULT 'medium'
            CHECK (priority IN ('immediate', 'high', 'medium', 'low')),
        status TEXT NOT NULL DEFAULT 'active' CHECK (status IN ('active', 'completed')),
        created_at TEXT NOT NULL,
        original_date TEXT NOT NULL,
        planned_date TEXT NOT NULL,
        last_active_date TEXT NOT NULL,
        carry_forward_count INTEGER NOT NULL DEFAULT 0,
        start_time TEXT,
        paused_at TEXT,
        is_paused INTEGER NOT NULL DEFAULT 0,
        accumulated_ms INTEGER NOT NULL DEFAULT 0,
        completed_at TEXT,
        estimated_duration INTEGER,
        actual_duration INTEGER,
        duration_basis TEXT,
        reminder_enabled INTEGER NOT NULL DEFAULT 1,
        reminder_interval INTEGER NOT NULL DEFAULT 7200,
        last_reminder_at TEXT,
        reminders_sent INTEGER NOT NULL DEFAULT 0,
        escalation_level INTEGER NOT NULL DEFAULT 0,
        snooze_until TEXT,
        estimate_warning_sent INTEGER NOT NULL DEFAULT 0
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_tasks_status ON tasks(status, planned_date)",
    "CREATE INDEX IF NOT EXISTS idx_tasks_completed ON tasks(completed_at)",
    """
    CREATE TABLE IF NOT EXISTS task_events (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        task_id TEXT NOT NULL REFERENCES tasks(id) ON DELETE CASCADE,
        event_type TEXT NOT NULL,
        occurred_at TEXT NOT NULL,
        details TEXT
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_events_task ON task_events(task_id, occurred_at)",
    """
    CREATE TABLE IF NOT EXISTS notifications (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        ntype TEXT NOT NULL,
        title TEXT NOT NULL,
        body TEXT NOT NULL DEFAULT '',
        task_id TEXT,
        created_at TEXT NOT NULL,
        is_read INTEGER NOT NULL DEFAULT 0,
        is_dismissed INTEGER NOT NULL DEFAULT 0,
        snooze_until TEXT
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_notifications_read ON notifications(is_read, is_dismissed)",
    """
    CREATE TABLE IF NOT EXISTS xp_transactions (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        task_id TEXT,
        kind TEXT NOT NULL,
        points INTEGER NOT NULL,
        reason TEXT NOT NULL,
        reward_key TEXT UNIQUE,
        created_at TEXT NOT NULL
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_xp_created ON xp_transactions(created_at)",
    """
    CREATE TABLE IF NOT EXISTS achievements (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        key TEXT NOT NULL UNIQUE,
        name TEXT NOT NULL,
        description TEXT NOT NULL,
        icon TEXT NOT NULL DEFAULT '🏅',
        xp INTEGER NOT NULL DEFAULT 0,
        unlocked_at TEXT
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS settings (
        key TEXT PRIMARY KEY,
        value TEXT NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS streaks (
        id INTEGER PRIMARY KEY CHECK (id = 1),
        current INTEGER NOT NULL DEFAULT 0,
        longest INTEGER NOT NULL DEFAULT 0,
        last_date TEXT
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS migrations (
        version INTEGER PRIMARY KEY,
        applied_at TEXT NOT NULL
    )
    """,
]

DEFAULT_SETTINGS: dict = {
    "notifications": {
        "enabled": True,
        "sound": True,
        "high_only": False,
        "default_interval": 7200,          # 2 hours
        "high_priority_interval": 7200,    # 2 hours for High / Immediate
        "long_task_threshold_hours": 4,
        "quiet_start": "22:00",
        "quiet_end": "07:00",
        "summary_hour": "21:00",
    },
    "rewards": {
        "xp": {"low": 5, "medium": 10, "high": 20, "immediate": 30},
        "bonuses": {"before_estimate": 5, "on_planned_date": 5, "high_priority": 5},
        "daily_milestone_count": 5,
        "daily_milestone_xp": 10,
        "level_thresholds": [0, 100, 250, 500, 900, 1400, 2000, 2700, 3500, 4400],
    },
}

# (key, name, description, icon, xp bonus)
ACHIEVEMENT_CATALOG: list[tuple[str, str, str, str, int]] = [
    ("first_task", "First Task", "Complete your first task", "🌱", 10),
    ("tasks_10", "Momentum", "Complete 10 tasks", "💪", 25),
    ("tasks_100", "Centurion", "Complete 100 tasks", "🏛️", 100),
    ("streak_3", "Warming Up", "Reach a 3-day streak", "🔥", 25),
    ("streak_7", "On Fire", "Reach a 7-day streak", "🚀", 50),
    ("streak_30", "Unstoppable", "Reach a 30-day streak", "👑", 150),
    ("first_high", "Big Game", "Complete a high or immediate priority task", "🎯", 20),
]
