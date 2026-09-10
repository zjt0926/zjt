# -*- coding: utf-8 -*-
"""SQLite 数据库初始化与连接"""
import sqlite3
import sys
from pathlib import Path


def _data_dir() -> Path:
    """数据目录：打包后放在 exe 旁边（可写、持久），源码运行位于项目根"""
    if getattr(sys, "frozen", False):
        base = Path(sys.executable).resolve().parent
    else:
        base = Path(__file__).resolve().parent.parent
    return base / "data"


DB_PATH = _data_dir() / "today.db"


def get_db() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_db():
    with get_db() as db:
        db.execute(
            """
            CREATE TABLE IF NOT EXISTS todos (
                id         INTEGER PRIMARY KEY AUTOINCREMENT,
                title      TEXT NOT NULL,
                note       TEXT NOT NULL DEFAULT '',
                due_date   TEXT,                -- 日历日期 YYYY-MM-DD
                remind_at  TEXT,                -- 提醒时间 YYYY-MM-DD HH:MM
                tag        TEXT NOT NULL DEFAULT '其他',
                important  INTEGER NOT NULL DEFAULT 0,
                done       INTEGER NOT NULL DEFAULT 0,
                reminded   INTEGER NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL DEFAULT (datetime('now', 'localtime'))
            )
            """
        )
        db.execute(
            "CREATE INDEX IF NOT EXISTS idx_todos_due ON todos(due_date)"
        )
