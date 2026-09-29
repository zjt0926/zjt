"""Database connection and query helpers for CampusClaw.

All material/knowledge queries take the viewer's ``class_id`` as a
parameter and never trust a client-supplied class identifier. The
helpers are intentionally thin wrappers over ``sqlite3`` with
parameterized statements to prevent SQL injection.
"""

import os
import sqlite3
from typing import Any, Optional

# Tables that must exist after init_db runs.
CORE_TABLES = (
    "classes",
    "users",
    "lectures",
    "assignments",
    "assistants",
    "skills",
    "materials",
    "knowledge_entries",
    "chunks",
    "chunks_fts",
)


def get_conn(db_path: Optional[str] = None) -> sqlite3.Connection:
    """Return a sqlite3 connection with row factory enabled.

    ``db_path`` defaults to the current Flask app's ``DATABASE`` config
    when called inside an app context.
    """
    if db_path is None:
        from flask import current_app
        db_path = current_app.config["DATABASE"]
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def list_materials(class_id: int, db_path: Optional[str] = None):
    """Return materials belonging to ``class_id`` only.

    The class filter is enforced server-side; callers must pass the
    session's class id, never a value from the request.
    """
    conn = get_conn(db_path)
    try:
        rows = conn.execute(
            "SELECT id, title, file_path, uploaded_by, created_at "
            "FROM materials WHERE class_id = ? ORDER BY id DESC",
            (class_id,),
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def get_material(material_id: int, db_path: Optional[str] = None) -> Optional[dict]:
    """Fetch a single material by id (no class filter — used for authz check)."""
    conn = get_conn(db_path)
    try:
        row = conn.execute(
            "SELECT id, class_id, title, file_path, uploaded_by, created_at "
            "FROM materials WHERE id = ?",
            (material_id,),
        ).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def get_knowledge_body(material_id: int, db_path: Optional[str] = None) -> Optional[str]:
    """Return the stored body_text of a material's knowledge entry (for in-page preview)."""
    conn = get_conn(db_path)
    try:
        row = conn.execute(
            "SELECT body_text FROM knowledge_entries WHERE material_id = ?",
            (material_id,),
        ).fetchone()
        return row["body_text"] if row else None
    finally:
        conn.close()


def get_user_by_username(username: str, db_path: Optional[str] = None) -> Optional[dict]:
    conn = get_conn(db_path)
    try:
        row = conn.execute(
            "SELECT id, username, password_hash, role, class_id FROM users WHERE username = ?",
            (username,),
        ).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def init_db_if_needed(db_path: str) -> None:
    """Create tables and seed data only if the database file is absent."""
    if os.path.exists(db_path):
        return
    # Defer the import to avoid a circular import at package load time.
    from scripts.init_db import init_db
    init_db(db_path)


def insert_material_and_knowledge(
    class_id: int,
    title: str,
    file_path: str,
    uploaded_by: int,
    body_text: str,
    db_path: Optional[str] = None,
) -> int:
    """Insert a material row and its knowledge entry in one transaction.

    Returns the new material id. Raises on failure so the caller can
    clean up the uploaded file.
    """
    conn = get_conn(db_path)
    try:
        conn.execute("BEGIN")
        cur = conn.execute(
            "INSERT INTO materials (class_id, title, file_path, uploaded_by) "
            "VALUES (?, ?, ?, ?)",
            (class_id, title, file_path, uploaded_by),
        )
        material_id = cur.lastrowid
        conn.execute(
            "INSERT INTO knowledge_entries (material_id, class_id, body_text) "
            "VALUES (?, ?, ?)",
            (material_id, class_id, body_text),
        )
        conn.commit()
        return material_id
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def delete_material_cascade(material_id: int, db_path: Optional[str] = None) -> None:
    """Delete a material; its knowledge_entries and chunks follow via CASCADE."""
    conn = get_conn(db_path)
    try:
        conn.execute("DELETE FROM chunks WHERE material_id = ?", (material_id,))
        conn.execute("DELETE FROM materials WHERE id = ?", (material_id,))
        conn.commit()
    finally:
        conn.close()


def _tokenize(text: str) -> str:
    """Jieba-tokenize and space-join for FTS5 indexing."""
    import jieba
    return " ".join(t for t in jieba.cut(text) if t.strip())


def insert_chunks(
    material_id: int,
    class_id: int,
    chunks: list[dict],
    db_path: Optional[str] = None,
) -> int:
    """Insert chunk rows + FTS index (jieba-tokenized).

    ``chunks`` is the output of ``chunk_text()`` — a list of dicts.
    Returns the number of chunks inserted. Existing chunks for the same
    ``material_id`` are deleted first (idempotent re-index).
    """
    conn = get_conn(db_path)
    try:
        conn.execute("DELETE FROM chunks WHERE material_id = ?", (material_id,))
        conn.execute("DELETE FROM chunks_fts WHERE material_id = ?", (material_id,))
        for i, ch in enumerate(chunks):
            cur = conn.execute(
                "INSERT INTO chunks (material_id, class_id, chunk_index, "
                "char_start, char_end, body_text) VALUES (?, ?, ?, ?, ?, ?)",
                (material_id, class_id, i, ch["char_start"], ch["char_end"], ch["text"]),
            )
            rowid = cur.lastrowid
            tokenized = _tokenize(ch["text"])
            conn.execute(
                "INSERT INTO chunks_fts (rowid, body_text, material_id, class_id, chunk_index) "
                "VALUES (?, ?, ?, ?, ?)",
                (rowid, tokenized, material_id, class_id, i),
            )
        conn.commit()
        return len(chunks)
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def keyword_search(
    class_id: int,
    query: str,
    k: int = 10,
    db_path: Optional[str] = None,
) -> list[dict]:
    """Full-text search on chunks_fts (jieba-tokenized), filtered by class_id.

    The query is also jieba-tokenized and OR-joined for MATCH.
    """
    tokens = [t for t in _tokenize(query).split() if len(t) >= 2]
    if not tokens:
        return []
    fts_query = " OR ".join(tokens)

    conn = get_conn(db_path)
    try:
        rows = conn.execute(
            "SELECT f.material_id, f.chunk_index, c.char_start, "
            "c.char_end, c.body_text, m.title, "
            "bm25(chunks_fts) AS rank "
            "FROM chunks_fts f "
            "JOIN chunks c ON c.rowid = f.rowid "
            "JOIN materials m ON m.id = f.material_id "
            "WHERE f.body_text MATCH ? AND f.class_id = ? "
            "ORDER BY rank LIMIT ?",
            (fts_query, class_id, k),
        ).fetchall()
        return [
            {
                "material_id": r["material_id"],
                "title": r["title"],
                "chunk_index": r["chunk_index"],
                "char_start": r["char_start"],
                "char_end": r["char_end"],
                "snippet": r["body_text"],
                "score": round(max(0.0, 1.0 + float(r["rank"])), 4),
            }
            for r in rows
        ]
    finally:
        conn.close()


def substring_search(
    class_id: int,
    query: str,
    k: int = 10,
    db_path: Optional[str] = None,
) -> list[dict]:
    """Substring match on raw chunk text (case-insensitive LIKE).

    Complements FTS for content jieba cannot tokenize — e.g. access-key-like
    alphanumeric strings, where the query ("LAT") is a fragment of one long
    token and therefore invisible to the FTS index.
    """
    escaped = query.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    pattern = f"%{escaped}%"

    conn = get_conn(db_path)
    try:
        rows = conn.execute(
            "SELECT c.material_id, c.chunk_index, c.char_start, "
            "c.char_end, c.body_text, m.title "
            "FROM chunks c "
            "JOIN materials m ON m.id = c.material_id "
            "WHERE c.class_id = ? AND c.body_text LIKE ? ESCAPE '\\' "
            "LIMIT ?",
            (class_id, pattern, k),
        ).fetchall()
        return [
            {
                "material_id": r["material_id"],
                "title": r["title"],
                "chunk_index": r["chunk_index"],
                "char_start": r["char_start"],
                "char_end": r["char_end"],
                "snippet": r["body_text"],
                "score": 0.5,
            }
            for r in rows
        ]
    finally:
        conn.close()


def get_chunk(
    material_id: int,
    chunk_index: int,
    class_id: Optional[int] = None,
    db_path: Optional[str] = None,
) -> Optional[dict]:
    """Retrieve a single chunk's text from the DB (for excerpt tracing)."""
    conn = get_conn(db_path)
    try:
        if class_id is not None:
            row = conn.execute(
                "SELECT c.*, m.title FROM chunks c "
                "JOIN materials m ON m.id = c.material_id "
                "WHERE c.material_id = ? AND c.chunk_index = ? AND c.class_id = ?",
                (material_id, chunk_index, class_id),
            ).fetchone()
        else:
            row = conn.execute(
                "SELECT c.*, m.title FROM chunks c "
                "JOIN materials m ON m.id = c.material_id "
                "WHERE c.material_id = ? AND c.chunk_index = ?",
                (material_id, chunk_index),
            ).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()
