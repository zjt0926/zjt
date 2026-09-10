# -*- coding: utf-8 -*-
"""REST API 路由"""
from datetime import datetime

from flask import Blueprint, jsonify, render_template, request

from .database import get_db
from .tagger import TAG_COLORS, classify
from .time_parser import parse_text

api_bp = Blueprint("api", __name__)


def _row_to_dict(row) -> dict:
    return {
        "id": row["id"],
        "title": row["title"],
        "note": row["note"],
        "date": row["due_date"],
        "remind_at": row["remind_at"],
        "tag": row["tag"],
        "color": TAG_COLORS.get(row["tag"], "#9ca3af"),
        "important": bool(row["important"]),
        "done": bool(row["done"]),
        "reminded": bool(row["reminded"]),
        "created_at": row["created_at"],
    }


def _sort_key(t):
    return (
        t["done"],                                   # 未完成在前
        not t["important"],                          # 重要在前
        t["remind_at"] or "9999-12-31 23:59",        # 有提醒时间在前
        t["date"] or "",
    )


@api_bp.route("/")
def index():
    return render_template("index.html")


@api_bp.route("/api/tags")
def tags():
    return jsonify([{"name": k, "color": v} for k, v in TAG_COLORS.items()])


@api_bp.route("/api/parse", methods=["POST"])
def parse():
    """解析预览：快速添加时实时显示识别出的时间/标签"""
    text = (request.get_json(silent=True) or {}).get("text", "")
    result = parse_text(text)
    result["tag"] = classify(result["title"])
    result["color"] = TAG_COLORS.get(result["tag"], "#9ca3af")
    return jsonify(result)


@api_bp.route("/api/todos")
def list_todos():
    month = request.args.get("month")      # YYYY-MM
    date = request.args.get("date")        # YYYY-MM-DD
    with get_db() as db:
        if month:
            rows = db.execute(
                "SELECT * FROM todos WHERE substr(due_date, 1, 7) = ?", (month,)
            ).fetchall()
        elif date:
            rows = db.execute(
                "SELECT * FROM todos WHERE due_date = ?", (date,)
            ).fetchall()
        else:
            rows = db.execute("SELECT * FROM todos").fetchall()
    todos = sorted((_row_to_dict(r) for r in rows), key=_sort_key)
    return jsonify(todos)


@api_bp.route("/api/todos", methods=["POST"])
def create_todo():
    data = request.get_json(silent=True) or {}
    title_raw = (data.get("title") or "").strip()
    if not title_raw:
        return jsonify({"error": "标题不能为空"}), 400

    parsed = parse_text(title_raw)
    title = (data.get("title_edit") or parsed["title"]).strip() or "未命名事项"
    date = data.get("date") or parsed["date"] or datetime.now().strftime("%Y-%m-%d")
    remind_at = data.get("remind_at") if data.get("remind_at") else parsed["remind_at"]
    tag = data.get("tag") or classify(title)
    important = bool(data.get("important") or parsed["important"])
    note = data.get("note") or ""

    # 校验日期格式
    try:
        datetime.strptime(date, "%Y-%m-%d")
    except ValueError:
        date = datetime.now().strftime("%Y-%m-%d")
    if remind_at:
        try:
            datetime.strptime(remind_at, "%Y-%m-%d %H:%M")
        except ValueError:
            remind_at = None

    with get_db() as db:
        cur = db.execute(
            "INSERT INTO todos (title, note, due_date, remind_at, tag, important)"
            " VALUES (?, ?, ?, ?, ?, ?)",
            (title, note, date, remind_at, tag, int(important)),
        )
        row = db.execute("SELECT * FROM todos WHERE id = ?", (cur.lastrowid,)).fetchone()
    return jsonify(_row_to_dict(row)), 201


@api_bp.route("/api/todos/<int:todo_id>", methods=["PATCH"])
def update_todo(todo_id: int):
    data = request.get_json(silent=True) or {}
    fields = {
        "title": "title", "note": "note", "date": "due_date",
        "remind_at": "remind_at", "tag": "tag",
        "important": "important", "done": "done", "reminded": "reminded",
    }
    sets, values = [], []
    for key, col in fields.items():
        if key in data:
            v = data[key]
            if key in ("important", "done", "reminded"):
                v = int(bool(v))
            sets.append(f"{col} = ?")
            values.append(v)
    if not sets:
        return jsonify({"error": "无更新字段"}), 400
    values.append(todo_id)
    with get_db() as db:
        cur = db.execute(f"UPDATE todos SET {', '.join(sets)} WHERE id = ?", values)
        if cur.rowcount == 0:
            return jsonify({"error": "待办不存在"}), 404
        row = db.execute("SELECT * FROM todos WHERE id = ?", (todo_id,)).fetchone()
    return jsonify(_row_to_dict(row))


@api_bp.route("/api/todos/<int:todo_id>", methods=["DELETE"])
def delete_todo(todo_id: int):
    with get_db() as db:
        cur = db.execute("DELETE FROM todos WHERE id = ?", (todo_id,))
        if cur.rowcount == 0:
            return jsonify({"error": "待办不存在"}), 404
    return jsonify({"ok": True})


@api_bp.route("/api/reminders/pending")
def pending_reminders():
    """到点未提醒且未完成的待办"""
    now = datetime.now().strftime("%Y-%m-%d %H:%M")
    with get_db() as db:
        rows = db.execute(
            "SELECT * FROM todos WHERE reminded = 0 AND done = 0"
            " AND remind_at IS NOT NULL AND remind_at <= ?"
            " ORDER BY remind_at",
            (now,),
        ).fetchall()
    return jsonify([_row_to_dict(r) for r in rows])


@api_bp.route("/api/reminders/ack", methods=["POST"])
def ack_reminders():
    ids = (request.get_json(silent=True) or {}).get("ids", [])
    if not ids:
        return jsonify({"ok": True})
    with get_db() as db:
        db.execute(
            f"UPDATE todos SET reminded = 1 WHERE id IN ({','.join('?' * len(ids))})",
            ids,
        )
    return jsonify({"ok": True})
