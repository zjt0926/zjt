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
    "assistant_prompts",
    "skill_configs",
    "conversations",
    "messages",
)

# Defaults shared with scripts/init_db.py.
DEFAULT_PROMPT = (
    "你是一名耐心的中学解题助手，用中文与学生交流；语言简洁，"
    "多鼓励学生自己动手推导。"
)
GUIDANCE_SKILL_KEY = "problem_guidance"


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
    token's class id, never a value from the request.
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
    material_ids: Optional[list[int]] = None,
) -> list[dict]:
    """Full-text search on chunks_fts (jieba-tokenized), filtered by class_id.

    When ``material_ids`` is given, results are further restricted to
    those materials (the agent-scope filter); the class filter still
    applies on top of it.
    """
    tokens = [t for t in _tokenize(query).split() if len(t) >= 2]
    if not tokens:
        return []
    fts_query = " OR ".join(tokens)

    sql = (
        "SELECT f.material_id, f.chunk_index, c.char_start, "
        "c.char_end, c.body_text, m.title, "
        "bm25(chunks_fts) AS rank "
        "FROM chunks_fts f "
        "JOIN chunks c ON c.rowid = f.rowid "
        "JOIN materials m ON m.id = f.material_id "
        "WHERE f.body_text MATCH ? AND f.class_id = ? "
    )
    params: list = [fts_query, class_id]
    if material_ids is not None:
        ids = [int(m) for m in material_ids]
        if not ids:
            return []
        sql += f"AND f.material_id IN ({','.join('?' * len(ids))}) "
        params.extend(ids)
    sql += "ORDER BY rank LIMIT ?"
    params.append(k)

    conn = get_conn(db_path)
    try:
        rows = conn.execute(sql, params).fetchall()
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
    material_ids: Optional[list[int]] = None,
) -> list[dict]:
    """Substring match on raw chunk text (case-insensitive LIKE).

    Complements FTS for content jieba cannot tokenize — e.g. access-key-like
    alphanumeric strings, where the query ("LAT") is a fragment of one long
    token and therefore invisible to the FTS index.
    """
    escaped = query.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    pattern = f"%{escaped}%"

    sql = (
        "SELECT c.material_id, c.chunk_index, c.char_start, "
        "c.char_end, c.body_text, m.title "
        "FROM chunks c "
        "JOIN materials m ON m.id = c.material_id "
        "WHERE c.class_id = ? AND c.body_text LIKE ? ESCAPE '\\' "
    )
    params: list = [class_id, pattern]
    if material_ids is not None:
        ids = [int(m) for m in material_ids]
        if not ids:
            return []
        sql += f"AND c.material_id IN ({','.join('?' * len(ids))}) "
        params.extend(ids)
    sql += "LIMIT ?"
    params.append(k)

    conn = get_conn(db_path)
    try:
        rows = conn.execute(sql, params).fetchall()
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


# ---------------------------------------------------------------------------
# Assistant (problem-solving agent): prompt, skill switches, conversations.
# ---------------------------------------------------------------------------

_ASSISTANT_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS assistant_prompts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    class_id INTEGER NOT NULL UNIQUE,
    body TEXT NOT NULL,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (class_id) REFERENCES classes(id)
);

CREATE TABLE IF NOT EXISTS skill_configs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    class_id INTEGER NOT NULL,
    skill_key TEXT NOT NULL,
    enabled INTEGER NOT NULL DEFAULT 1,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE (class_id, skill_key),
    FOREIGN KEY (class_id) REFERENCES classes(id)
);

CREATE TABLE IF NOT EXISTS conversations (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL,
    class_id INTEGER NOT NULL,
    title TEXT NOT NULL DEFAULT '新会话',
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (user_id) REFERENCES users(id),
    FOREIGN KEY (class_id) REFERENCES classes(id)
);

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


def ensure_assistant_schema(db_path: Optional[str] = None) -> None:
    """Idempotent migration for existing databases created before this lesson.

    Creates the four new tables and seeds a default prompt + enabled
    guidance skill for every class that does not have them yet.
    """
    conn = get_conn(db_path)
    try:
        conn.executescript(_ASSISTANT_SCHEMA_SQL)

        # Add the ``mode`` column to older databases and backfill it from
        # the previous canned-answer section labels.
        cols = {r["name"] for r in conn.execute("PRAGMA table_info(messages)")}
        if "mode" not in cols:
            conn.execute("ALTER TABLE messages ADD COLUMN mode TEXT")
            conn.execute(
                "UPDATE messages SET mode = 'guide' WHERE role = 'assistant' "
                "AND content LIKE '%【思路引导】%'"
            )
            conn.execute(
                "UPDATE messages SET mode = 'direct' WHERE role = 'assistant' "
                "AND mode IS NULL"
            )

        class_rows = conn.execute("SELECT id FROM classes").fetchall()
        for r in class_rows:
            cid = r["id"]
            if not conn.execute(
                "SELECT 1 FROM assistant_prompts WHERE class_id = ?", (cid,)
            ).fetchone():
                conn.execute(
                    "INSERT INTO assistant_prompts (class_id, body) VALUES (?, ?)",
                    (cid, DEFAULT_PROMPT),
                )
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


def get_prompt(class_id: int, db_path: Optional[str] = None):
    """Legacy: class-level prompt (kept for the old prompt card).

    New code reads prompts from the ``agents`` table instead.
    """
    conn = get_conn(db_path)
    try:
        row = conn.execute(
            "SELECT body, updated_at FROM assistant_prompts WHERE class_id = ?",
            (class_id,),
        ).fetchone()
        if row is None:
            return None
        return {"body": row["body"], "updated_at": row["updated_at"]}
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Agents (智能体): per-class assistants with bound skills and materials.
# ---------------------------------------------------------------------------

_AGENT_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS agents (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    class_id INTEGER NOT NULL,
    name TEXT NOT NULL,
    prompt TEXT NOT NULL DEFAULT '',
    enabled INTEGER NOT NULL DEFAULT 1,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE (class_id, name),
    FOREIGN KEY (class_id) REFERENCES classes(id)
);

CREATE TABLE IF NOT EXISTS agent_skills (
    agent_id INTEGER NOT NULL,
    skill_id INTEGER NOT NULL,
    PRIMARY KEY (agent_id, skill_id),
    FOREIGN KEY (agent_id) REFERENCES agents(id) ON DELETE CASCADE,
    FOREIGN KEY (skill_id) REFERENCES skills(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS agent_materials (
    agent_id INTEGER NOT NULL,
    material_id INTEGER NOT NULL,
    PRIMARY KEY (agent_id, material_id),
    FOREIGN KEY (agent_id) REFERENCES agents(id) ON DELETE CASCADE,
    FOREIGN KEY (material_id) REFERENCES materials(id) ON DELETE CASCADE
);
"""

# The one skill seeded for every class; its instruction is data (teacher-
# editable), routed only when the agent binds it AND its trigger words
# match the question — at most one skill hits per turn.
GUIDANCE_SKILL_NAME = "解题引导"
GUIDANCE_SKILL_TRIGGERS = "错在哪,哪一步,哪错了,检查一下,帮我看看,对吗,对不对,为什么错,思路"
GUIDANCE_SKILL_INSTRUCTION = (
    "你是解题引导教练：只做思路引导，不直接给出最终答案、最终数值或完整结论。"
    "先分析题意，再逐步指出学生的推导错在哪一步，并给下一步提示；"
    "需要引用编号资料时必须使用其编号（如 [1]），并说明该资料对应哪一步。"
    "结尾邀请学生把修正后的推导发给你检查。"
)

DEFAULT_AGENT_NAME = "解题助手"

# Built-in AI applications (智能体) seeded for every class. Each one has
# its own persona prompt; all are bound to the class's guidance skill
# and current materials so they work out of the box. Names are unique
# per class, so the migration only inserts the ones still missing.
BUILTIN_AGENTS = (
    (DEFAULT_AGENT_NAME, DEFAULT_PROMPT),
    (
        "语文写作助手",
        "你是一名语文写作指导教师，擅长审题立意、结构梳理与语言润色；"
        "点评要具体到句子，先肯定优点再给出可执行的修改建议，不代替学生重写整篇作文。",
    ),
    (
        "编程辅导助手",
        "你是一名编程辅导老师，擅长 Python 与基础算法；"
        "先判断学生的错误属于语法、逻辑还是边界条件，再用提问引导其自行修正，"
        "必要时给出最小可运行示例，不直接交付完整作业答案。",
    ),
)


def ensure_agent_schema(db_path: Optional[str] = None) -> None:
    """Idempotent migration to the agent model.

    Creates ``agents`` / ``agent_skills`` / ``agent_materials``, extends
    ``skills`` with instruction/triggers/enabled, adds ``conversations
    .agent_id``, then seeds a default skill + agent per class and
    backfills existing conversations to the class's default agent.
    Never drops data — safe to run on every boot.
    """
    conn = get_conn(db_path)
    try:
        conn.executescript(_AGENT_SCHEMA_SQL)

        skill_cols = {r["name"] for r in conn.execute("PRAGMA table_info(skills)")}
        for col, ddl in (
            ("instruction", "ALTER TABLE skills ADD COLUMN instruction TEXT"),
            ("triggers", "ALTER TABLE skills ADD COLUMN triggers TEXT"),
            ("enabled", "ALTER TABLE skills ADD COLUMN enabled INTEGER NOT NULL DEFAULT 1"),
        ):
            if col not in skill_cols:
                conn.execute(ddl)

        conv_cols = {
            r["name"] for r in conn.execute("PRAGMA table_info(conversations)")
        }
        if "agent_id" not in conv_cols:
            conn.execute("ALTER TABLE conversations ADD COLUMN agent_id INTEGER")

        # Seed the guidance skill per class (class name unique; skip if a
        # skill of the same name already exists in the class).
        for c in conn.execute("SELECT id FROM classes").fetchall():
            cid = c["id"]
            if not conn.execute(
                "SELECT 1 FROM skills WHERE class_id = ? AND name = ?",
                (cid, GUIDANCE_SKILL_NAME),
            ).fetchone():
                conn.execute(
                    "INSERT INTO skills (class_id, name, description, instruction, "
                    "triggers, enabled) VALUES (?, ?, ?, ?, ?, 1)",
                    (cid, GUIDANCE_SKILL_NAME, "命中时只给思路引导，不写最终答案",
                     GUIDANCE_SKILL_INSTRUCTION, GUIDANCE_SKILL_TRIGGERS),
                )

            # Built-in AI applications per class (bound to the guidance
            # skill and all materials the class currently has). Insert
            # each built-in whose name is still missing, so an existing
            # database gains the new apps without duplicating the old one.
            skill_row = conn.execute(
                "SELECT id FROM skills WHERE class_id = ? AND name = ?",
                (cid, GUIDANCE_SKILL_NAME),
            ).fetchone()
            for agent_name, agent_prompt in BUILTIN_AGENTS:
                if conn.execute(
                    "SELECT 1 FROM agents WHERE class_id = ? AND name = ?",
                    (cid, agent_name),
                ).fetchone():
                    continue
                cur = conn.execute(
                    "INSERT INTO agents (class_id, name, prompt, enabled) "
                    "VALUES (?, ?, ?, 1)",
                    (cid, agent_name, agent_prompt),
                )
                agent_id = cur.lastrowid
                if skill_row:
                    conn.execute(
                        "INSERT OR IGNORE INTO agent_skills (agent_id, skill_id) "
                        "VALUES (?, ?)",
                        (agent_id, skill_row["id"]),
                    )
                for m in conn.execute(
                    "SELECT id FROM materials WHERE class_id = ?", (cid,)
                ).fetchall():
                    conn.execute(
                        "INSERT OR IGNORE INTO agent_materials (agent_id, material_id) "
                        "VALUES (?, ?)",
                        (agent_id, m["id"]),
                    )

        # Backfill old conversations to their class's default agent so
        # they stay usable after the upgrade.
        conn.execute(
            "UPDATE conversations SET agent_id = "
            "(SELECT a.id FROM agents a WHERE a.class_id = conversations.class_id "
            " ORDER BY a.id LIMIT 1) "
            "WHERE agent_id IS NULL"
        )
        conn.commit()
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# MCP servers, discovered tools, and call audit.
# ---------------------------------------------------------------------------

_MCP_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS mcp_servers (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    class_id INTEGER NOT NULL,
    name TEXT NOT NULL,
    base_url TEXT NOT NULL,
    auth_token TEXT NOT NULL,
    connected INTEGER NOT NULL DEFAULT 0,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE (class_id, name),
    FOREIGN KEY (class_id) REFERENCES classes(id)
);

CREATE TABLE IF NOT EXISTS mcp_server_tools (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    server_id INTEGER NOT NULL,
    name TEXT NOT NULL,
    description TEXT,
    input_schema TEXT,
    discovered_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE (server_id, name),
    FOREIGN KEY (server_id) REFERENCES mcp_servers(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS mcp_call_audit (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    class_id INTEGER NOT NULL,
    server_id INTEGER,
    server_name TEXT,
    tool_name TEXT NOT NULL,
    status TEXT NOT NULL,
    user_id INTEGER NOT NULL,
    request_args TEXT,
    response_text TEXT,
    error_message TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (class_id) REFERENCES classes(id),
    FOREIGN KEY (user_id) REFERENCES users(id)
);
"""


def ensure_mcp_schema(db_path: Optional[str] = None) -> None:
    """Idempotent migration for MCP support.

    Creates ``mcp_servers`` / ``mcp_server_tools`` / ``mcp_call_audit``
    and extends ``skills`` with a ``tools`` column (a JSON list of tool
    references: the built-in ``knowledge_search`` or ``tool_name@server``
    for MCP tools). Safe to run on every boot.
    """
    conn = get_conn(db_path)
    try:
        conn.executescript(_MCP_SCHEMA_SQL)

        skill_cols = {r["name"] for r in conn.execute("PRAGMA table_info(skills)")}
        if "tools" not in skill_cols:
            conn.execute("ALTER TABLE skills ADD COLUMN tools TEXT")

        conn.commit()
    finally:
        conn.close()


def get_agent(agent_id: int, db_path: Optional[str] = None) -> Optional[dict]:
    """Raw agent row by id (no class filter; caller enforces visibility)."""
    conn = get_conn(db_path)
    try:
        row = conn.execute(
            "SELECT id, class_id, name, prompt, enabled, created_at "
            "FROM agents WHERE id = ?",
            (agent_id,),
        ).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def list_agents(
    class_id: int,
    include_disabled: bool = False,
    db_path: Optional[str] = None,
) -> list[dict]:
    """Agents of one class; students get ``include_disabled=False``."""
    conn = get_conn(db_path)
    try:
        sql = (
            "SELECT id, class_id, name, prompt, enabled, created_at "
            "FROM agents WHERE class_id = ?"
        )
        if not include_disabled:
            sql += " AND enabled = 1"
        rows = conn.execute(sql + " ORDER BY id", (class_id,)).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def create_agent(
    class_id: int,
    name: str,
    prompt: str,
    enabled: bool = True,
    db_path: Optional[str] = None,
) -> Optional[int]:
    """Create an agent. Returns None when the class name already exists."""
    conn = get_conn(db_path)
    try:
        if conn.execute(
            "SELECT 1 FROM agents WHERE class_id = ? AND name = ?",
            (class_id, name),
        ).fetchone():
            return None
        cur = conn.execute(
            "INSERT INTO agents (class_id, name, prompt, enabled) VALUES (?, ?, ?, ?)",
            (class_id, name, prompt, 1 if enabled else 0),
        )
        conn.commit()
        return cur.lastrowid
    finally:
        conn.close()


def update_agent_fields(
    agent_id: int,
    fields: dict,
    db_path: Optional[str] = None,
) -> None:
    """Patch name / prompt / enabled (whichever are present)."""
    sets, params = [], []
    for col in ("name", "prompt", "enabled"):
        if col in fields:
            val = fields[col]
            if col == "enabled":
                val = 1 if val else 0
            sets.append(f"{col} = ?")
            params.append(val)
    if not sets:
        return
    conn = get_conn(db_path)
    try:
        conn.execute(
            f"UPDATE agents SET {', '.join(sets)} WHERE id = ?", (*params, agent_id)
        )
        conn.commit()
    finally:
        conn.close()


def set_agent_skills(agent_id: int, skill_ids: list[int], db_path=None) -> None:
    """Replace the agent's skill binding set."""
    conn = get_conn(db_path)
    try:
        conn.execute("DELETE FROM agent_skills WHERE agent_id = ?", (agent_id,))
        for sid in skill_ids:
            conn.execute(
                "INSERT OR IGNORE INTO agent_skills (agent_id, skill_id) VALUES (?, ?)",
                (agent_id, int(sid)),
            )
        conn.commit()
    finally:
        conn.close()


def set_agent_materials(agent_id: int, material_ids: list[int], db_path=None) -> None:
    """Replace the agent's material binding set."""
    conn = get_conn(db_path)
    try:
        conn.execute("DELETE FROM agent_materials WHERE agent_id = ?", (agent_id,))
        for mid in material_ids:
            conn.execute(
                "INSERT OR IGNORE INTO agent_materials (agent_id, material_id) "
                "VALUES (?, ?)",
                (agent_id, int(mid)),
            )
        conn.commit()
    finally:
        conn.close()


def agent_bound_skill_ids(agent_id: int, db_path=None) -> list[int]:
    conn = get_conn(db_path)
    try:
        rows = conn.execute(
            "SELECT skill_id FROM agent_skills WHERE agent_id = ?", (agent_id,)
        ).fetchall()
        return [r["skill_id"] for r in rows]
    finally:
        conn.close()


def agent_bound_material_ids(agent_id: int, db_path=None) -> list[int]:
    conn = get_conn(db_path)
    try:
        rows = conn.execute(
            "SELECT material_id FROM agent_materials WHERE agent_id = ?", (agent_id,)
        ).fetchall()
        return [r["material_id"] for r in rows]
    finally:
        conn.close()


def get_agent_skills(agent_id: int, db_path=None) -> list[dict]:
    """The agent's bound skills with their routing fields."""
    conn = get_conn(db_path)
    try:
        rows = conn.execute(
            "SELECT s.id, s.class_id, s.name, s.instruction, s.triggers, s.enabled, s.tools "
            "FROM agent_skills bs JOIN skills s ON s.id = bs.skill_id "
            "WHERE bs.agent_id = ? ORDER BY s.id",
            (agent_id,),
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def get_agent_materials(agent_id: int, db_path=None) -> list[dict]:
    """The agent's bound materials (id + title)."""
    conn = get_conn(db_path)
    try:
        rows = conn.execute(
            "SELECT m.id, m.title FROM agent_materials bm "
            "JOIN materials m ON m.id = bm.material_id "
            "WHERE bm.agent_id = ? ORDER BY m.id",
            (agent_id,),
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def agent_payload(agent: dict, db_path=None) -> dict:
    """Serialize an agent with its bindings (for API responses)."""
    return {
        **agent,
        "skills": get_agent_skills(agent["id"], db_path),
        "materials": get_agent_materials(agent["id"], db_path),
    }


def _split_triggers(triggers: Optional[str]) -> list[str]:
    import re
    return [t for t in re.split(r"[,，、;；\s]+", triggers or "") if t]


def route_skill(agent_id: int, question: str, db_path=None) -> Optional[dict]:
    """Pick at most ONE skill for this turn.

    Candidates: skills bound to the agent AND themselves enabled whose
    trigger words (适用条件) appear in the question. Ties resolve to the
    lowest skill id, so routing is deterministic.
    """
    q = (question or "").lower()
    best: Optional[dict] = None
    for s in get_agent_skills(agent_id, db_path):
        if not s.get("enabled"):
            continue
        if any(t.lower() in q for t in _split_triggers(s.get("triggers"))):
            if best is None or s["id"] < best["id"]:
                best = s
    return best


# ---------------------------------------------------------------------------
# Skills (技能): per-class, teacher-authored or uploaded files.
# ---------------------------------------------------------------------------

def list_skills(class_id: int, db_path=None) -> list[dict]:
    conn = get_conn(db_path)
    try:
        rows = conn.execute(
            "SELECT id, class_id, name, description, instruction, triggers, enabled, tools "
            "FROM skills WHERE class_id = ? ORDER BY id",
            (class_id,),
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def get_skill(skill_id: int, db_path=None) -> Optional[dict]:
    """Raw skill row by id (no class filter; caller enforces visibility)."""
    conn = get_conn(db_path)
    try:
        row = conn.execute(
            "SELECT id, class_id, name, description, instruction, triggers, enabled, tools "
            "FROM skills WHERE id = ?",
            (skill_id,),
        ).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def create_skill(
    class_id: int,
    name: str,
    triggers: str,
    instruction: str,
    tools: Optional[str] = None,
    db_path=None,
) -> Optional[int]:
    """Create a skill. Returns None when the class name already exists."""
    conn = get_conn(db_path)
    try:
        if conn.execute(
            "SELECT 1 FROM skills WHERE class_id = ? AND name = ?",
            (class_id, name),
        ).fetchone():
            return None
        cur = conn.execute(
            "INSERT INTO skills (class_id, name, instruction, triggers, enabled, tools) "
            "VALUES (?, ?, ?, ?, 1, ?)",
            (class_id, name, instruction, triggers, tools),
        )
        conn.commit()
        return cur.lastrowid
    finally:
        conn.close()


def update_skill_fields(skill_id: int, fields: dict, db_path=None) -> None:
    sets, params = [], []
    for col in ("name", "instruction", "triggers", "enabled", "tools"):
        if col in fields:
            val = fields[col]
            if col == "enabled":
                val = 1 if val else 0
            sets.append(f"{col} = ?")
            params.append(val)
    if not sets:
        return
    conn = get_conn(db_path)
    try:
        conn.execute(
            f"UPDATE skills SET {', '.join(sets)} WHERE id = ?", (*params, skill_id)
        )
        conn.commit()
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Conversations (locked to one agent) and messages.
# ---------------------------------------------------------------------------

def create_conversation(
    user_id: int,
    class_id: int,
    agent_id: int,
    db_path: Optional[str] = None,
) -> int:
    conn = get_conn(db_path)
    try:
        cur = conn.execute(
            "INSERT INTO conversations (user_id, class_id, agent_id) VALUES (?, ?, ?)",
            (user_id, class_id, agent_id),
        )
        conn.commit()
        return cur.lastrowid
    finally:
        conn.close()


def list_conversations(user_id: int, db_path: Optional[str] = None):
    """The user's own conversations, with the bound agent's name."""
    conn = get_conn(db_path)
    try:
        rows = conn.execute(
            "SELECT c.id, c.user_id, c.class_id, c.agent_id, c.title, c.created_at, "
            "a.name AS agent_name, a.enabled AS agent_enabled "
            "FROM conversations c LEFT JOIN agents a ON a.id = c.agent_id "
            "WHERE c.user_id = ? ORDER BY c.id DESC",
            (user_id,),
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def get_conversation(conv_id: int, db_path: Optional[str] = None):
    conn = get_conn(db_path)
    try:
        row = conn.execute(
            "SELECT id, user_id, class_id, agent_id, title, created_at "
            "FROM conversations WHERE id = ?",
            (conv_id,),
        ).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def rename_conversation(
    conv_id: int, title: str, db_path: Optional[str] = None
) -> None:
    conn = get_conn(db_path)
    try:
        conn.execute(
            "UPDATE conversations SET title = ? WHERE id = ?", (title, conv_id)
        )
        conn.commit()
    finally:
        conn.close()


def list_messages(conv_id: int, limit: int = 20, db_path: Optional[str] = None):
    """Return the most recent ``limit`` messages, oldest first (for context)."""
    conn = get_conn(db_path)
    try:
        rows = conn.execute(
            "SELECT id, conversation_id, role, content, citations, mode, created_at "
            "FROM messages WHERE conversation_id = ? "
            "ORDER BY id DESC LIMIT ?",
            (conv_id, limit),
        ).fetchall()
        return [dict(r) for r in reversed(rows)]
    finally:
        conn.close()


def insert_message(
    conv_id: int,
    role: str,
    content: str,
    citations: Optional[str] = None,
    mode: Optional[str] = None,
    db_path: Optional[str] = None,
) -> int:
    conn = get_conn(db_path)
    try:
        cur = conn.execute(
            "INSERT INTO messages (conversation_id, role, content, citations, mode) "
            "VALUES (?, ?, ?, ?, ?)",
            (conv_id, role, content, citations, mode),
        )
        conn.commit()
        return cur.lastrowid
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# MCP server / tool / audit helpers.
# ---------------------------------------------------------------------------

def list_mcp_servers(class_id: int, db_path=None) -> list[dict]:
    """All MCP servers registered for the class (token NEVER returned)."""
    conn = get_conn(db_path)
    try:
        rows = conn.execute(
            "SELECT id, class_id, name, base_url, connected, created_at "
            "FROM mcp_servers WHERE class_id = ? ORDER BY id",
            (class_id,),
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def get_mcp_server(server_id: int, db_path=None) -> Optional[dict]:
    """Raw server row including auth_token (caller must not leak it)."""
    conn = get_conn(db_path)
    try:
        row = conn.execute(
            "SELECT id, class_id, name, base_url, auth_token, connected, created_at "
            "FROM mcp_servers WHERE id = ?",
            (server_id,),
        ).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def get_mcp_server_by_name(class_id: int, name: str, db_path=None) -> Optional[dict]:
    """Look up a server by its class-scoped display name."""
    conn = get_conn(db_path)
    try:
        row = conn.execute(
            "SELECT id, class_id, name, base_url, auth_token, connected, created_at "
            "FROM mcp_servers WHERE class_id = ? AND name = ?",
            (class_id, name),
        ).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def create_mcp_server(
    class_id: int,
    name: str,
    base_url: str,
    auth_token: str,
    db_path=None,
) -> Optional[int]:
    """Register a server. Returns None if the class already has this name."""
    conn = get_conn(db_path)
    try:
        if conn.execute(
            "SELECT 1 FROM mcp_servers WHERE class_id = ? AND name = ?",
            (class_id, name),
        ).fetchone():
            return None
        cur = conn.execute(
            "INSERT INTO mcp_servers (class_id, name, base_url, auth_token, connected) "
            "VALUES (?, ?, ?, ?, 0)",
            (class_id, name, base_url, auth_token),
        )
        conn.commit()
        return cur.lastrowid
    finally:
        conn.close()


def set_mcp_connected(server_id: int, connected: bool, db_path=None) -> None:
    conn = get_conn(db_path)
    try:
        conn.execute(
            "UPDATE mcp_servers SET connected = ? WHERE id = ?",
            (1 if connected else 0, server_id),
        )
        conn.commit()
    finally:
        conn.close()


def delete_mcp_server(server_id: int, db_path=None) -> None:
    """Cascades to mcp_server_tools via FK."""
    conn = get_conn(db_path)
    try:
        conn.execute("DELETE FROM mcp_servers WHERE id = ?", (server_id,))
        conn.commit()
    finally:
        conn.close()


def set_server_tools(server_id: int, tools: list[dict], db_path=None) -> None:
    """Replace the cached tool list for a server (idempotent re-discover)."""
    import json as _json

    conn = get_conn(db_path)
    try:
        conn.execute("DELETE FROM mcp_server_tools WHERE server_id = ?", (server_id,))
        for t in tools:
            conn.execute(
                "INSERT INTO mcp_server_tools (server_id, name, description, input_schema) "
                "VALUES (?, ?, ?, ?)",
                (
                    server_id,
                    t.get("name"),
                    t.get("description"),
                    _json.dumps(t.get("inputSchema") or {}, ensure_ascii=False),
                ),
            )
        conn.commit()
    finally:
        conn.close()


def list_server_tools(server_id: int, db_path=None) -> list[dict]:
    import json as _json

    conn = get_conn(db_path)
    try:
        rows = conn.execute(
            "SELECT id, server_id, name, description, input_schema, discovered_at "
            "FROM mcp_server_tools WHERE server_id = ? ORDER BY name",
            (server_id,),
        ).fetchall()
        out = []
        for r in rows:
            d = dict(r)
            try:
                d["input_schema"] = _json.loads(d["input_schema"] or "{}")
            except (TypeError, ValueError):
                d["input_schema"] = {}
            out.append(d)
        return out
    finally:
        conn.close()


def get_server_tool(server_id: int, tool_name: str, db_path=None) -> Optional[dict]:
    conn = get_conn(db_path)
    try:
        row = conn.execute(
            "SELECT id, server_id, name, description, input_schema, discovered_at "
            "FROM mcp_server_tools WHERE server_id = ? AND name = ?",
            (server_id, tool_name),
        ).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def insert_mcp_audit(
    class_id: int,
    server_id: Optional[int],
    server_name: Optional[str],
    tool_name: str,
    status: str,
    user_id: int,
    request_args: Optional[str] = None,
    response_text: Optional[str] = None,
    error_message: Optional[str] = None,
    db_path=None,
) -> int:
    conn = get_conn(db_path)
    try:
        cur = conn.execute(
            "INSERT INTO mcp_call_audit "
            "(class_id, server_id, server_name, tool_name, status, user_id, "
            " request_args, response_text, error_message) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                class_id,
                server_id,
                server_name,
                tool_name,
                status,
                user_id,
                request_args,
                response_text,
                error_message,
            ),
        )
        conn.commit()
        return cur.lastrowid
    finally:
        conn.close()


def list_mcp_audit(class_id: int, limit: int = 100, db_path=None) -> list[dict]:
    conn = get_conn(db_path)
    try:
        rows = conn.execute(
            "SELECT id, class_id, server_id, server_name, tool_name, status, "
            "user_id, request_args, response_text, error_message, created_at "
            "FROM mcp_call_audit WHERE class_id = ? "
            "ORDER BY id DESC LIMIT ?",
            (class_id, limit),
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Prompt templates (提示词模板): a class-scoped library of reusable
# persona prompts. An agent created "from" a template receives a copy of
# the body as its own prompt, so later template edits never silently
# change existing agents.
# ---------------------------------------------------------------------------

_PROMPT_TEMPLATE_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS prompt_templates (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    class_id INTEGER NOT NULL,
    name TEXT NOT NULL,
    description TEXT NOT NULL DEFAULT '',
    body TEXT NOT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE (class_id, name),
    FOREIGN KEY (class_id) REFERENCES classes(id)
);
"""

BUILTIN_PROMPT_TEMPLATES = (
    (
        "苏格拉底式启发",
        "不直接给答案，用连续追问带学生自己推出结论",
        "你是一位苏格拉底式导师。绝不直接给出最终答案或结论；"
        "先用一个问题确认学生已经理解到哪一步，再根据其回答逐步追问，"
        "每次只推进一小步。学生卡住时给出方向性提示而不是结果，"
        "并在结尾请学生复述自己的推理链条。",
    ),
    (
        "严谨学科导师",
        "分点作答、给出依据、计算写清关键步骤",
        "你是一位严谨的学科导师。回答必须分点陈述并给出依据："
        "定义先写清楚，推理说明理由，计算写出关键中间步骤与单位。"
        "不确定时明确说明假设，不编造公式或数据；"
        "最后用一句话总结结论。",
    ),
    (
        "简明讲解员",
        "一句话结论 + 最多三步解释，面向初学者",
        "你是一位简明讲解员，面向初学者。先用一句话给出结论，"
        "再用不超过三步解释原因，每步不超过两句话；"
        "避免堆砌术语，必须使用术语时随即用日常语言解释。"
        "最后给一个可以立刻自测的小问题。",
    ),
)


def ensure_prompt_template_schema(db_path: Optional[str] = None) -> None:
    """Idempotent migration for the prompt-template library.

    Creates the table and seeds the built-in templates for every class,
    skipping any template whose class-scoped name already exists.
    """
    conn = get_conn(db_path)
    try:
        conn.executescript(_PROMPT_TEMPLATE_SCHEMA_SQL)
        for c in conn.execute("SELECT id FROM classes").fetchall():
            cid = c["id"]
            for name, description, body in BUILTIN_PROMPT_TEMPLATES:
                if conn.execute(
                    "SELECT 1 FROM prompt_templates WHERE class_id = ? AND name = ?",
                    (cid, name),
                ).fetchone():
                    continue
                conn.execute(
                    "INSERT INTO prompt_templates (class_id, name, description, body) "
                    "VALUES (?, ?, ?, ?)",
                    (cid, name, description, body),
                )
        conn.commit()
    finally:
        conn.close()


def list_prompt_templates(class_id: int, db_path=None) -> list[dict]:
    conn = get_conn(db_path)
    try:
        rows = conn.execute(
            "SELECT id, class_id, name, description, body, created_at, updated_at "
            "FROM prompt_templates WHERE class_id = ? ORDER BY id",
            (class_id,),
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def get_prompt_template(template_id: int, db_path=None) -> Optional[dict]:
    conn = get_conn(db_path)
    try:
        row = conn.execute(
            "SELECT id, class_id, name, description, body, created_at, updated_at "
            "FROM prompt_templates WHERE id = ?",
            (template_id,),
        ).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def create_prompt_template(
    class_id: int,
    name: str,
    description: str,
    body: str,
    db_path=None,
) -> Optional[int]:
    """Insert a template; returns None when the class name already exists."""
    conn = get_conn(db_path)
    try:
        if conn.execute(
            "SELECT 1 FROM prompt_templates WHERE class_id = ? AND name = ?",
            (class_id, name),
        ).fetchone():
            return None
        cur = conn.execute(
            "INSERT INTO prompt_templates (class_id, name, description, body) "
            "VALUES (?, ?, ?, ?)",
            (class_id, name, description, body),
        )
        conn.commit()
        return cur.lastrowid
    finally:
        conn.close()


def update_prompt_template_fields(template_id: int, fields: dict, db_path=None) -> None:
    """Patch the given columns and bump ``updated_at``."""
    allowed = {"name", "description", "body"}
    sets = [f"{k} = ?" for k in fields if k in allowed]
    params = [fields[k] for k in fields if k in allowed]
    if not sets:
        return
    sets.append("updated_at = CURRENT_TIMESTAMP")
    params.append(template_id)
    conn = get_conn(db_path)
    try:
        conn.execute(
            f"UPDATE prompt_templates SET {', '.join(sets)} WHERE id = ?", params
        )
        conn.commit()
    finally:
        conn.close()


def delete_prompt_template(template_id: int, db_path=None) -> None:
    conn = get_conn(db_path)
    try:
        conn.execute("DELETE FROM prompt_templates WHERE id = ?", (template_id,))
        conn.commit()
    finally:
        conn.close()
