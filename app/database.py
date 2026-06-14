"""SQLite persistence for learner progress and profile.

Kept deliberately small and dependency-free (stdlib sqlite3). A single-file DB
lives under ./data so a teacher can back it up or reset it by deleting one file.
"""
from __future__ import annotations

import json
import sqlite3
import time
from contextlib import contextmanager
from typing import Any

from .config import DB_PATH

SCHEMA = """
CREATE TABLE IF NOT EXISTS profile (
    id           INTEGER PRIMARY KEY CHECK (id = 1),
    display_name TEXT    NOT NULL DEFAULT 'Learner',
    level        TEXT    NOT NULL DEFAULT 'beginner',
    art_style    TEXT    NOT NULL DEFAULT 'watercolor_picturebook',
    tutor_gender TEXT    NOT NULL DEFAULT 'female',
    daily_goal   INTEGER NOT NULL DEFAULT 1,
    settings     TEXT    NOT NULL DEFAULT '{}'
);

CREATE TABLE IF NOT EXISTS lesson_progress (
    lesson_id   TEXT PRIMARY KEY,
    status      TEXT    NOT NULL DEFAULT 'not_started',  -- not_started|in_progress|completed
    score       INTEGER NOT NULL DEFAULT 0,              -- 0-100 best quiz score
    times_done  INTEGER NOT NULL DEFAULT 0,
    last_studied REAL
);

-- One row per study session, used for streaks and the activity heatmap.
CREATE TABLE IF NOT EXISTS activity (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    lesson_id   TEXT,
    kind        TEXT NOT NULL,        -- study|quiz|speak|chat
    detail      TEXT,
    minutes     REAL NOT NULL DEFAULT 0,
    created_at  REAL NOT NULL
);

-- Words the learner explicitly saved while reading.
CREATE TABLE IF NOT EXISTS saved_words (
    word        TEXT PRIMARY KEY,
    meaning     TEXT,
    lesson_id   TEXT,
    created_at  REAL NOT NULL
);
"""


@contextmanager
def get_conn():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def init_db() -> None:
    with get_conn() as conn:
        conn.executescript(SCHEMA)
        # Lightweight migrations for DBs created by older versions.
        cols = {r["name"] for r in conn.execute("PRAGMA table_info(profile)")}
        if "tutor_gender" not in cols:
            conn.execute("ALTER TABLE profile ADD COLUMN tutor_gender TEXT NOT NULL DEFAULT 'female'")
        cur = conn.execute("SELECT COUNT(*) AS n FROM profile")
        if cur.fetchone()["n"] == 0:
            conn.execute("INSERT INTO profile (id) VALUES (1)")


# --- Profile ---------------------------------------------------------------

def get_profile() -> dict[str, Any]:
    with get_conn() as conn:
        row = conn.execute("SELECT * FROM profile WHERE id = 1").fetchone()
        data = dict(row)
        data["settings"] = json.loads(data.get("settings") or "{}")
        return data


def update_profile(**fields: Any) -> dict[str, Any]:
    allowed = {"display_name", "level", "art_style", "tutor_gender", "daily_goal", "settings"}
    sets, vals = [], []
    for key, value in fields.items():
        if key not in allowed or value is None:
            continue
        if key == "settings":
            value = json.dumps(value, ensure_ascii=False)
        sets.append(f"{key} = ?")
        vals.append(value)
    if sets:
        with get_conn() as conn:
            conn.execute(f"UPDATE profile SET {', '.join(sets)} WHERE id = 1", vals)
    return get_profile()


# --- Progress --------------------------------------------------------------

def get_all_progress() -> dict[str, dict[str, Any]]:
    with get_conn() as conn:
        rows = conn.execute("SELECT * FROM lesson_progress").fetchall()
        return {r["lesson_id"]: dict(r) for r in rows}


def record_progress(lesson_id: str, status: str | None = None,
                    score: int | None = None) -> dict[str, Any]:
    now = time.time()
    with get_conn() as conn:
        conn.execute(
            "INSERT INTO lesson_progress (lesson_id, last_studied) VALUES (?, ?) "
            "ON CONFLICT(lesson_id) DO NOTHING",
            (lesson_id, now),
        )
        if status:
            conn.execute(
                "UPDATE lesson_progress SET status = ?, last_studied = ?, "
                "times_done = times_done + CASE WHEN ? = 'completed' THEN 1 ELSE 0 END "
                "WHERE lesson_id = ?",
                (status, now, status, lesson_id),
            )
        if score is not None:
            conn.execute(
                "UPDATE lesson_progress SET score = MAX(score, ?), last_studied = ? "
                "WHERE lesson_id = ?",
                (score, now, lesson_id),
            )
        row = conn.execute(
            "SELECT * FROM lesson_progress WHERE lesson_id = ?", (lesson_id,)
        ).fetchone()
        return dict(row)


def log_activity(kind: str, lesson_id: str | None = None,
                 detail: str | None = None, minutes: float = 0) -> None:
    with get_conn() as conn:
        conn.execute(
            "INSERT INTO activity (lesson_id, kind, detail, minutes, created_at) "
            "VALUES (?, ?, ?, ?, ?)",
            (lesson_id, kind, detail, minutes, time.time()),
        )


def get_activity(days: int = 30) -> list[dict[str, Any]]:
    cutoff = time.time() - days * 86400
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT * FROM activity WHERE created_at >= ? ORDER BY created_at DESC",
            (cutoff,),
        ).fetchall()
        return [dict(r) for r in rows]


# --- Saved words ------------------------------------------------------------

def save_word(word: str, meaning: str, lesson_id: str | None) -> None:
    with get_conn() as conn:
        conn.execute(
            "INSERT INTO saved_words (word, meaning, lesson_id, created_at) "
            "VALUES (?, ?, ?, ?) ON CONFLICT(word) DO UPDATE SET meaning = excluded.meaning",
            (word.lower().strip(), meaning, lesson_id, time.time()),
        )


def get_saved_words() -> list[dict[str, Any]]:
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT * FROM saved_words ORDER BY created_at DESC"
        ).fetchall()
        return [dict(r) for r in rows]


def delete_word(word: str) -> None:
    with get_conn() as conn:
        conn.execute("DELETE FROM saved_words WHERE word = ?", (word.lower().strip(),))
