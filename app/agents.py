"""Agent (智能体) and skill (技能) management routes.

Authorization is enforced server-side:
- Agents:  teachers create / update / toggle and bind skills+materials;
           students only list the class's ENABLED agents and read them.
- Skills:  teachers author (write) or upload a skill file; students may
           list the class's skills.
- Bindings: a skill/material from ANOTHER class, or one that does not
           exist, is treated exactly like "not found" (404) — never 403,
           so the API does not leak other classes' ids.

Agent ids follow the same 404 rule: a disabled, foreign-class or
nonexistent agent all return the identical ``{"error": "not found"}``.

Skill ``tools`` field
---------------------
A skill may carry a ``tools`` list. The built-in ``knowledge_search``
tool is always available. MCP tools are referenced as
``tool_name@server_name``; saving such a reference requires the named
server to belong to the caller's class, be connected, and have the tool
discovered (cached). Otherwise the save is rejected with 400.
"""

import json
from typing import Optional

from flask import Blueprint, g, jsonify, request

from . import db
from .auth import login_required, role_required
from .openapi import api_spec

bp = Blueprint("agents", __name__)

AGENT_NAME_MAX = 50
PROMPT_MAX = 2000
SKILL_NAME_MAX = 50
TRIGGER_MAX = 200
INSTRUCTION_MAX = 4000
SKILL_FILE_MAX_BYTES = 200_000
TOOLS_MAX = 10
NOT_FOUND = ({"error": "not found"}, 404)

# The one tool that is always available (built into the assistant turn).
BUILTIN_TOOLS = {"knowledge_search"}


def _clean(value, limit: int) -> str:
    return (value or "").strip()[:limit] if value else ""


# ---------------------------------------------------------------------------
# Agents
# ---------------------------------------------------------------------------

@bp.route("/api/agents", methods=["GET"])
@login_required
@api_spec(
    summary="列出本班智能体",
    tags=["智能体"],
    description="教师可见全部（含停用）；学生仅见已启用的。",
)
def list_agents():
    """Class agents. Students only see enabled ones; teachers see all."""
    class_id = g.current_user["class_id"]
    include_disabled = g.current_user["role"] == "teacher"
    agents = [
        db.agent_payload(a) for a in db.list_agents(class_id, include_disabled)
    ]
    return jsonify({"class_id": class_id, "agents": agents})


@bp.route("/api/agents/<int:agent_id>", methods=["GET"])
@login_required
@api_spec(
    summary="查看单个智能体",
    tags=["智能体"],
    description="未启用、他班或不存在的智能体返回完全一致的 404。",
    responses={"200": {"description": "智能体详情"}, "404": {"description": "视为不存在"}},
)
def get_agent(agent_id: int):
    """One agent with bindings; same 404 for disabled/foreign/missing."""
    agent_row = db.get_agent(agent_id)
    if (
        agent_row is None
        or agent_row["class_id"] != g.current_user["class_id"]
        or (
            g.current_user["role"] != "teacher"
            and not agent_row["enabled"]
        )
    ):
        return NOT_FOUND
    return jsonify({"agent": db.agent_payload(agent_row)})


@bp.route("/api/agents", methods=["POST"])
@role_required("teacher")
@api_spec(
    summary="创建智能体（教师）",
    tags=["智能体"],
    description="班内名称唯一；绑定他班或不存在的技能/材料返回 404。",
    requestBody={
        "required": True,
        "content": {
            "application/json": {
                "schema": {
                    "type": "object",
                    "required": ["name", "prompt"],
                    "properties": {
                        "name": {"type": "string", "example": "数学助手"},
                        "prompt": {"type": "string", "example": "你是一名严谨的数学解题助手…"},
                        "template_id": {
                            "type": "integer",
                            "description": "可选：用本班提示词模板的正文作为 prompt（prompt 为空时生效）",
                        },
                        "enabled": {"type": "boolean", "default": True},
                        "skill_ids": {"type": "array", "items": {"type": "integer"}},
                        "material_ids": {"type": "array", "items": {"type": "integer"}},
                    },
                }
            }
        },
        "responses": {"201": {"description": "创建成功"}, "409": {"description": "班内名称重复"}},
    },
)
def create_agent():
    """Teacher: create an agent, optionally binding skills and materials."""
    class_id = g.current_user["class_id"]
    data = request.get_json(silent=True) or {}

    name = _clean(data.get("name"), AGENT_NAME_MAX)
    prompt = _clean(data.get("prompt"), PROMPT_MAX)
    if not name:
        return jsonify({"error": "智能体名称不能为空"}), 400

    # Optional: seed the agent prompt from a class prompt template.
    # The body is copied now, so later template edits do not affect this
    # agent (the template is only a starting point).
    template_id = data.get("template_id")
    if template_id not in (None, ""):
        try:
            template_id = int(template_id)
        except (TypeError, ValueError):
            return jsonify({"error": "模板 id 无效"}), 400
        template = db.get_prompt_template(template_id)
        if template is None or template["class_id"] != class_id:
            return NOT_FOUND
        if not prompt:
            prompt = _clean(template["body"], PROMPT_MAX)

    if not prompt:
        return jsonify({"error": "角色提示词不能为空"}), 400

    skill_ids = data.get("skill_ids") or []
    material_ids = data.get("material_ids") or []
    invalid = _check_bindings(class_id, skill_ids, material_ids)
    if invalid is not None:
        return NOT_FOUND

    agent_id = db.create_agent(
        class_id, name, prompt, enabled=bool(data.get("enabled", True))
    )
    if agent_id is None:
        return jsonify({"error": "本班已有同名智能体"}), 409
    db.set_agent_skills(agent_id, skill_ids)
    db.set_agent_materials(agent_id, material_ids)
    return jsonify({"agent": db.agent_payload(db.get_agent(agent_id))}), 201


@bp.route("/api/agents/<int:agent_id>/update", methods=["POST"])
@role_required("teacher")
@api_spec(
    summary="修改智能体（教师）",
    tags=["智能体"],
    description=(
        "部分更新：name/prompt/enabled 任意组合；skill_ids / material_ids "
        "出现即整体替换绑定集合。已有会话仍锁定原智能体记录，改动从下一轮生效。"
    ),
    requestBody={"required": False, "content": {"application/json": {"schema": {
        "type": "object",
        "properties": {
            "name": {"type": "string"},
            "prompt": {"type": "string"},
            "template_id": {
                "type": "integer",
                "description": "可选：用本班提示词模板正文替换 prompt（仅当未同时提交 prompt 时生效）",
            },
            "enabled": {"type": "boolean"},
            "skill_ids": {"type": "array", "items": {"type": "integer"}},
            "material_ids": {"type": "array", "items": {"type": "integer"}},
        },
    }}}},
)
def update_agent(agent_id: int):
    """Teacher: patch name/prompt/enabled and/or replace the bindings.

    ``skill_ids`` / ``material_ids`` (when present) replace the whole
    binding set; any id outside the class is 404 (treated as nonexistent).
    """
    agent_row = db.get_agent(agent_id)
    if agent_row is None or agent_row["class_id"] != g.current_user["class_id"]:
        return NOT_FOUND

    data = request.get_json(silent=True) or {}
    fields: dict = {}
    if "name" in data:
        name = _clean(data.get("name"), AGENT_NAME_MAX)
        if not name:
            return jsonify({"error": "智能体名称不能为空"}), 400
        existing = db.list_agents(g.current_user["class_id"], include_disabled=True)
        if any(
            a["name"] == name and a["id"] != agent_id for a in existing
        ):
            return jsonify({"error": "本班已有同名智能体"}), 409
        fields["name"] = name
    if "prompt" in data:
        prompt = _clean(data.get("prompt"), PROMPT_MAX)
        if not prompt:
            return jsonify({"error": "角色提示词不能为空"}), 400
        fields["prompt"] = prompt
    elif "template_id" in data and data.get("template_id") not in (None, ""):
        try:
            template_id = int(data.get("template_id"))
        except (TypeError, ValueError):
            return jsonify({"error": "模板 id 无效"}), 400
        template = db.get_prompt_template(template_id)
        if template is None or template["class_id"] != g.current_user["class_id"]:
            return NOT_FOUND
        fields["prompt"] = _clean(template["body"], PROMPT_MAX)
    if "enabled" in data:
        fields["enabled"] = bool(data.get("enabled"))

    if "skill_ids" in data or "material_ids" in data:
        skill_ids = data.get("skill_ids", db.agent_bound_skill_ids(agent_id))
        material_ids = data.get(
            "material_ids", db.agent_bound_material_ids(agent_id)
        )
        invalid = _check_bindings(
            g.current_user["class_id"], skill_ids, material_ids
        )
        if invalid is not None:
            return NOT_FOUND
        if "skill_ids" in data:
            db.set_agent_skills(agent_id, skill_ids)
        if "material_ids" in data:
            db.set_agent_materials(agent_id, material_ids)

    db.update_agent_fields(agent_id, fields)
    return jsonify({"agent": db.agent_payload(db.get_agent(agent_id))})


@bp.route("/api/agents/<int:agent_id>/toggle", methods=["POST"])
@role_required("teacher")
@api_spec(summary="启停智能体（教师）", tags=["智能体"])
def toggle_agent(agent_id: int):
    """Teacher: enable/disable an agent (students never see disabled)."""
    agent_row = db.get_agent(agent_id)
    if agent_row is None or agent_row["class_id"] != g.current_user["class_id"]:
        return NOT_FOUND
    db.update_agent_fields(agent_id, {"enabled": not agent_row["enabled"]})
    return jsonify({"agent": db.agent_payload(db.get_agent(agent_id))})


def _check_bindings(class_id: int, skill_ids, material_ids) -> Optional[str]:
    """Return an error marker when any id is foreign or nonexistent.

    Binding another class's skill/material counts as "not found" per the
    spec, so the route answers 404 — never 403 — and never confirms the
    id exists elsewhere.
    """
    for sid in skill_ids or []:
        skill = db.get_skill(int(sid))
        if skill is None or skill["class_id"] != class_id:
            return "skill"
    for mid in material_ids or []:
        material = db.get_material(int(mid))
        if material is None or material["class_id"] != class_id:
            return "material"
    return None


# ---------------------------------------------------------------------------
# Skills
# ---------------------------------------------------------------------------

def _parse_tools(raw_tools) -> list[str]:
    """Normalize the incoming tools value to a list of trimmed strings."""
    if raw_tools is None:
        return []
    if isinstance(raw_tools, str):
        # Accept a comma-separated string for convenience.
        return [t.strip() for t in raw_tools.split(",") if t.strip()]
    if isinstance(raw_tools, list):
        return [str(t).strip() for t in raw_tools if str(t).strip()]
    return []


def _validate_tools(class_id: int, tools: list[str]) -> Optional[str]:
    """Validate each tool reference.

    Returns an error message string when any reference is invalid, else
    ``None``. Rules:
      * ``knowledge_search`` is always valid (built-in).
      * ``tool_name@server_name`` requires the server to belong to the
        class, be connected, and have the tool discovered (cached).
      * Anything else (no @, unknown format) is rejected.
    """
    if len(tools) > TOOLS_MAX:
        return f"单个技能最多绑定 {TOOLS_MAX} 个工具"
    for ref in tools:
        if ref in BUILTIN_TOOLS:
            continue
        if "@" not in ref:
            return f"无效的工具引用「{ref}」：内置工具仅支持 knowledge_search，" \
                   f"MCP 工具须为「工具名@服务器名」格式"
        tool_name, server_name = ref.split("@", 1)
        if not tool_name or not server_name:
            return f"无效的工具引用「{ref}」"
        server = db.get_mcp_server_by_name(class_id, server_name)
        if server is None:
            return f"服务器「{server_name}」不存在于本班"
        if not server["connected"]:
            return f"服务器「{server_name}」未连接，请先连接并发现工具"
        if db.get_server_tool(server["id"], tool_name) is None:
            return f"工具「{tool_name}」未在服务器「{server_name}」中发现"
    return None


@bp.route("/api/skills", methods=["GET"])
@login_required
@api_spec(summary="列出本班技能", tags=["技能"])
def list_skills():
    """Class skills. Students may list them too (per the permission table)."""
    return jsonify({"skills": db.list_skills(g.current_user["class_id"])})


@bp.route("/api/skills", methods=["POST"])
@role_required("teacher")
@api_spec(
    summary="创建技能（教师）：编写或上传文件",
    tags=["技能"],
    description=(
        "JSON 提交 {name, triggers, instruction, tools}；或 multipart 上传 "
        "file（.txt/.md，内容作为技能说明）+ name/triggers/tools 字段。"
        "triggers 为适用条件（逗号分隔的触发词），技能路由按其命中，至多命中一个。"
        "tools 为工具引用列表：内置 knowledge_search，或 MCP 工具「工具名@服务器名」。"
    ),
    requestBody={"required": True, "content": {"application/json": {"schema": {
        "type": "object",
        "required": ["name", "instruction"],
        "properties": {
            "name": {"type": "string", "example": "解题引导"},
            "triggers": {"type": "string", "example": "错在哪,哪一步,对吗"},
            "instruction": {"type": "string"},
            "tools": {
                "type": "array",
                "items": {"type": "string"},
                "example": ["knowledge_search", "formula_hint@campus-tools"],
                "description": "工具引用：knowledge_search 或 工具名@服务器名",
            },
        },
    }}}},
)
def create_skill():
    """Teacher: author a skill as JSON, or upload a skill file.

    JSON body: ``{name, triggers, instruction, tools}``.
    Multipart: fields ``name``/``triggers``/``tools`` plus a ``file``
    (.txt/.md) whose text content becomes the instruction — the skill
    text is data coming from the upload, like a material.

    ``tools`` is a list of references: the built-in ``knowledge_search``
    or ``tool_name@server_name`` for MCP tools. Every MCP reference is
    validated against the class's connected, discovered servers before
    the skill is saved.
    """
    class_id = g.current_user["class_id"]

    if request.files:
        file = request.files.get("file")
        if not file or not file.filename:
            return jsonify({"error": "未选择技能文件"}), 400
        ext = file.filename.rsplit(".", 1)[-1].lower() if "." in file.filename else ""
        if ext not in ("txt", "md"):
            return jsonify({"error": "技能文件仅支持 .txt 和 .md"}), 400
        raw = file.read(SKILL_FILE_MAX_BYTES + 1)
        if len(raw) > SKILL_FILE_MAX_BYTES:
            return jsonify({"error": "技能文件过大（上限 200KB）"}), 400
        try:
            instruction = raw.decode("utf-8")
        except UnicodeDecodeError:
            return jsonify({"error": "技能文件编码不是 UTF-8"}), 400
        name = _clean(request.form.get("name"), SKILL_NAME_MAX)
        triggers = _clean(request.form.get("triggers"), TRIGGER_MAX)
        raw_tools = request.form.get("tools")
    else:
        data = request.get_json(silent=True) or {}
        name = _clean(data.get("name"), SKILL_NAME_MAX)
        triggers = _clean(data.get("triggers"), TRIGGER_MAX)
        instruction = _clean(data.get("instruction"), INSTRUCTION_MAX)
        raw_tools = data.get("tools")

    if not name:
        return jsonify({"error": "技能名称不能为空"}), 400
    if not instruction:
        return jsonify({"error": "技能说明不能为空"}), 400

    tools = _parse_tools(raw_tools)
    err = _validate_tools(class_id, tools)
    if err is not None:
        return jsonify({"error": err}), 400
    tools_json = json.dumps(tools, ensure_ascii=False) if tools else None

    skill_id = db.create_skill(class_id, name, triggers, instruction, tools=tools_json)
    if skill_id is None:
        return jsonify({"error": "本班已有同名技能"}), 409
    return jsonify({"skill": db.get_skill(skill_id)}), 201


@bp.route("/api/skills/<int:skill_id>/update", methods=["POST"])
@role_required("teacher")
@api_spec(
    summary="修改技能（教师）",
    tags=["技能"],
    requestBody={"required": False, "content": {"application/json": {"schema": {
        "type": "object",
        "properties": {
            "name": {"type": "string"},
            "triggers": {"type": "string"},
            "instruction": {"type": "string"},
            "enabled": {"type": "boolean"},
            "tools": {
                "type": "array",
                "items": {"type": "string"},
                "description": "工具引用列表，保存时校验 MCP 工具是否已连接并发现",
            },
        },
    }}}},
)
def update_skill(skill_id: int):
    """Teacher: patch a skill's name / triggers / instruction / enabled / tools."""
    skill = db.get_skill(skill_id)
    if skill is None or skill["class_id"] != g.current_user["class_id"]:
        return NOT_FOUND

    data = request.get_json(silent=True) or {}
    fields: dict = {}
    if "name" in data:
        name = _clean(data.get("name"), SKILL_NAME_MAX)
        if not name:
            return jsonify({"error": "技能名称不能为空"}), 400
        fields["name"] = name
    if "triggers" in data:
        fields["triggers"] = _clean(data.get("triggers"), TRIGGER_MAX)
    if "instruction" in data:
        instruction = _clean(data.get("instruction"), INSTRUCTION_MAX)
        if not instruction:
            return jsonify({"error": "技能说明不能为空"}), 400
        fields["instruction"] = instruction
    if "enabled" in data:
        fields["enabled"] = bool(data.get("enabled"))
    if "tools" in data:
        tools = _parse_tools(data.get("tools"))
        err = _validate_tools(g.current_user["class_id"], tools)
        if err is not None:
            return jsonify({"error": err}), 400
        fields["tools"] = json.dumps(tools, ensure_ascii=False) if tools else None

    db.update_skill_fields(skill_id, fields)
    return jsonify({"skill": db.get_skill(skill_id)})
