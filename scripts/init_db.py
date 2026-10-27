"""Initialize the CampusClaw SQLite database: create tables and seed data.

Run directly::

    python scripts/init_db.py

or import ``init_db(db_path)`` for programmatic use (e.g. from the app
factory when the DB file is missing).

Passwords are hashed with bcrypt (``$2b$`` prefix) and stored in the
``password_hash`` column — never in plain text.
"""

import os
import sqlite3
import sys

import bcrypt

# Add the project root to sys.path so ``import app`` works when this
# script is invoked directly.
_PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

DB_PATH = os.path.join(_PROJECT_ROOT, "data", "app.db")


SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS classes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL UNIQUE
);

CREATE TABLE IF NOT EXISTS users (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    username TEXT NOT NULL UNIQUE,
    password_hash TEXT NOT NULL,
    role TEXT NOT NULL CHECK (role IN ('teacher', 'student')),
    class_id INTEGER NOT NULL,
    FOREIGN KEY (class_id) REFERENCES classes(id)
);

CREATE TABLE IF NOT EXISTS lectures (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    class_id INTEGER NOT NULL,
    title TEXT NOT NULL,
    body TEXT,
    FOREIGN KEY (class_id) REFERENCES classes(id)
);

CREATE TABLE IF NOT EXISTS assignments (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    class_id INTEGER NOT NULL,
    title TEXT NOT NULL,
    body TEXT,
    FOREIGN KEY (class_id) REFERENCES classes(id)
);

CREATE TABLE IF NOT EXISTS assistants (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    class_id INTEGER NOT NULL,
    name TEXT NOT NULL,
    description TEXT,
    FOREIGN KEY (class_id) REFERENCES classes(id)
);

CREATE TABLE IF NOT EXISTS skills (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    class_id INTEGER NOT NULL,
    name TEXT NOT NULL,
    description TEXT,
    FOREIGN KEY (class_id) REFERENCES classes(id)
);

CREATE TABLE IF NOT EXISTS materials (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    class_id INTEGER NOT NULL,
    title TEXT NOT NULL,
    file_path TEXT,
    uploaded_by INTEGER,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (class_id) REFERENCES classes(id),
    FOREIGN KEY (uploaded_by) REFERENCES users(id)
);

CREATE TABLE IF NOT EXISTS knowledge_entries (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    material_id INTEGER NOT NULL,
    class_id INTEGER NOT NULL,
    body_text TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (material_id) REFERENCES materials(id) ON DELETE CASCADE,
    FOREIGN KEY (class_id) REFERENCES classes(id)
);

-- Per-chunk storage with character ranges for source tracing.
-- Excerpt text comes from here (not from the vector store).
CREATE TABLE IF NOT EXISTS chunks (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    material_id INTEGER NOT NULL,
    class_id INTEGER NOT NULL,
    chunk_index INTEGER NOT NULL,
    char_start INTEGER NOT NULL,
    char_end INTEGER NOT NULL,
    body_text TEXT NOT NULL,
    FOREIGN KEY (material_id) REFERENCES materials(id) ON DELETE CASCADE,
    FOREIGN KEY (class_id) REFERENCES classes(id)
);

-- Full-text index for keyword search (standalone, fed with jieba-tokenized text).
CREATE VIRTUAL TABLE IF NOT EXISTS chunks_fts USING fts5(
    body_text,
    material_id UNINDEXED,
    class_id UNINDEXED,
    chunk_index UNINDEXED
);

-- Per-class assistant persona prompt, written by the teacher. Read on
-- every turn, so a saved prompt takes effect from the next conversation
-- turn. One row per class.
CREATE TABLE IF NOT EXISTS assistant_prompts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    class_id INTEGER NOT NULL UNIQUE,
    body TEXT NOT NULL,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (class_id) REFERENCES classes(id)
);

-- Per-class, per-skill enable switches. Only 'problem_guidance' is
-- populated this lesson; the other two skills stay reserved.
CREATE TABLE IF NOT EXISTS skill_configs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    class_id INTEGER NOT NULL,
    skill_key TEXT NOT NULL,
    enabled INTEGER NOT NULL DEFAULT 1,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE (class_id, skill_key),
    FOREIGN KEY (class_id) REFERENCES classes(id)
);

-- A student's continuous Q&A thread. Owned by one user in one class.
CREATE TABLE IF NOT EXISTS conversations (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL,
    class_id INTEGER NOT NULL,
    title TEXT NOT NULL DEFAULT '新会话',
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (user_id) REFERENCES users(id),
    FOREIGN KEY (class_id) REFERENCES classes(id)
);

-- Individual turns inside a conversation. citations is a JSON list of
-- {material_id, title, chunk_index, char_start, char_end} (may be empty).
CREATE TABLE IF NOT EXISTS messages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    conversation_id INTEGER NOT NULL,
    role TEXT NOT NULL CHECK (role IN ('user', 'assistant')),
    content TEXT NOT NULL,
    citations TEXT,
    mode TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (conversation_id) REFERENCES conversations(id) ON DELETE CASCADE
);
"""

DEFAULT_PROMPT = (
    "你是一名耐心的中学解题助手，用中文与学生交流；语言简洁，"
    "多鼓励学生自己动手推导。"
)

# The one skill implemented this lesson; enabled by default.
GUIDANCE_SKILL_KEY = "problem_guidance"


SEED_USERS = [
    # (username, plaintext_password, role, class_name)
    ("teacher_a", "teacherpass", "teacher", "班级 A"),
    ("student_a1", "studentpass", "student", "班级 A"),
    ("student_b1", "studentpass", "student", "班级 B"),
]

SEED_MATERIALS = [
    # (class_name, title)
    ("班级 A", "A 班第一讲：函数入门讲义"),
    ("班级 B", "B 班第一讲：代数基础讲义"),
]


def _hash(password: str) -> str:
    return bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")


def init_db(db_path: str = DB_PATH) -> None:
    """Create tables and seed sample data at ``db_path``.

    Idempotent for schema (CREATE TABLE IF NOT EXISTS); seed data is
    only inserted when the target table is empty so re-running does not
    duplicate rows.
    """
    os.makedirs(os.path.dirname(os.path.abspath(db_path)), exist_ok=True)
    conn = sqlite3.connect(db_path)
    try:
        conn.executescript(SCHEMA_SQL)

        # Classes
        class_ids: dict[str, int] = {}
        for name in ("班级 A", "班级 B"):
            row = conn.execute("SELECT id FROM classes WHERE name = ?", (name,)).fetchone()
            if row:
                class_ids[name] = row[0]
            else:
                cur = conn.execute("INSERT INTO classes (name) VALUES (?)", (name,))
                class_ids[name] = cur.lastrowid

        # Users
        for username, password, role, class_name in SEED_USERS:
            existing = conn.execute(
                "SELECT id FROM users WHERE username = ?", (username,)
            ).fetchone()
            if existing:
                continue
            conn.execute(
                "INSERT INTO users (username, password_hash, role, class_id) "
                "VALUES (?, ?, ?, ?)",
                (username, _hash(password), role, class_ids[class_name]),
            )

        # Materials (seed two distinguishable titles, one per class)
        for class_name, title in SEED_MATERIALS:
            existing = conn.execute(
                "SELECT id FROM materials WHERE title = ?", (title,)
            ).fetchone()
            if existing:
                continue
            conn.execute(
                "INSERT INTO materials (class_id, title, file_path) VALUES (?, ?, ?)",
                (class_ids[class_name], title, None),
            )

        # Placeholder rows for the four other core tables (0~1 row each).
        for class_name, cid in class_ids.items():
            if not conn.execute("SELECT 1 FROM lectures WHERE class_id = ?", (cid,)).fetchone():
                conn.execute(
                    "INSERT INTO lectures (class_id, title) VALUES (?, ?)",
                    (cid, f"{class_name}讲义占位"),
                )
            if not conn.execute("SELECT 1 FROM assignments WHERE class_id = ?", (cid,)).fetchone():
                conn.execute(
                    "INSERT INTO assignments (class_id, title) VALUES (?, ?)",
                    (cid, f"{class_name}作业占位"),
                )
            if not conn.execute("SELECT 1 FROM assistants WHERE class_id = ?", (cid,)).fetchone():
                conn.execute(
                    "INSERT INTO assistants (class_id, name) VALUES (?, ?)",
                    (cid, f"{class_name}助手占位"),
                )
            if not conn.execute("SELECT 1 FROM skills WHERE class_id = ?", (cid,)).fetchone():
                conn.execute(
                    "INSERT INTO skills (class_id, name) VALUES (?, ?)",
                    (cid, f"{class_name}技能占位"),
                )

            # Assistant persona prompt (one per class; teacher edits later).
            if not conn.execute(
                "SELECT 1 FROM assistant_prompts WHERE class_id = ?", (cid,)
            ).fetchone():
                conn.execute(
                    "INSERT INTO assistant_prompts (class_id, body) VALUES (?, ?)",
                    (cid, DEFAULT_PROMPT),
                )

            # The problem-guidance skill, enabled by default.
            if not conn.execute(
                "SELECT 1 FROM skill_configs WHERE class_id = ? AND skill_key = ?",
                (cid, GUIDANCE_SKILL_KEY),
            ).fetchone():
                conn.execute(
                    "INSERT INTO skill_configs (class_id, skill_key, enabled) "
                    "VALUES (?, ?, 1)",
                    (cid, GUIDANCE_SKILL_KEY),
                )

        conn.commit()
    finally:
        conn.close()


if __name__ == "__main__":
    path = sys.argv[1] if len(sys.argv) > 1 else DB_PATH
    init_db(path)
    print(f"Initialized database at {path}")
