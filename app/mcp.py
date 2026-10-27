"""MCP server management routes (host side).

A teacher can register a self-hosted MCP server (name + base URL + bearer
token), connect to it (which runs the initialize handshake and caches the
discovered tool list), disconnect, and inspect the call audit log.

Security invariants
-------------------
* The bearer token is stored server-side only. ``list`` / ``get`` responses
  never include it (the ``auth_token`` column is selected out).
* Every write endpoint requires the ``teacher`` role; students get 403.
* Cross-class access is treated as 404 (same as materials/agents) so the
  API never confirms another class's server exists.
* A failed connect/discover returns a clear JSON error but leaves the
  existing retrieval + streaming chat path untouched.
"""

import json
from typing import Optional

from flask import Blueprint, g, jsonify, render_template, request

from . import db, mcp_client
from .auth import login_required, role_required
from .openapi import api_spec

bp = Blueprint("mcp", __name__)

NAME_MAX = 50
URL_MAX = 500
TOKEN_MAX = 500
NOT_FOUND = ({"error": "not found"}, 404)


def _clean(value, limit: int) -> str:
    return (value or "").strip()[:limit] if value else ""


def _public_server(row: dict) -> dict:
    """Strip the bearer token before returning a server to any client."""
    return {
        "id": row["id"],
        "class_id": row["class_id"],
        "name": row["name"],
        "base_url": row["base_url"],
        "connected": bool(row["connected"]),
        "created_at": row["created_at"],
    }


def _owned_server(server_id: int):
    """Return the server row if it belongs to the caller's class, else None."""
    row = db.get_mcp_server(server_id)
    if row is None or row["class_id"] != g.current_user["class_id"]:
        return None
    return row


# ---------------------------------------------------------------------------
# Page shell
# ---------------------------------------------------------------------------

@bp.route("/mcp", methods=["GET"])
def mcp_page():
    """Teacher-facing management page (data loaded via JSON APIs)."""
    return render_template("mcp.html")


# ---------------------------------------------------------------------------
# Servers
# ---------------------------------------------------------------------------

@bp.route("/api/mcp/servers", methods=["GET"])
@login_required
@api_spec(
    summary="列出本班 MCP 服务器",
    tags=["MCP"],
    description="响应中不包含 Bearer 令牌。",
)
def list_servers():
    return jsonify({"servers": [
        _public_server(s) for s in db.list_mcp_servers(g.current_user["class_id"])
    ]})


@bp.route("/api/mcp/servers", methods=["POST"])
@role_required("teacher")
@api_spec(
    summary="登记 MCP 服务器（教师）",
    tags=["MCP"],
    description=(
        "提交 name / base_url / auth_token。令牌仅服务端保存，响应不带回。"
        "班内 name 唯一；重复返回 409。"
    ),
    requestBody={"required": True, "content": {"application/json": {"schema": {
        "type": "object",
        "required": ["name", "base_url", "auth_token"],
        "properties": {
            "name": {"type": "string", "example": "campus-tools"},
            "base_url": {"type": "string", "example": "http://mcp-server:8090"},
            "auth_token": {"type": "string", "description": "MCP 服务器 Bearer 令牌"},
        },
    }}}},
    responses={"201": {"description": "登记成功"}, "409": {"description": "班内同名"}},
)
def create_server():
    class_id = g.current_user["class_id"]
    data = request.get_json(silent=True) or {}
    name = _clean(data.get("name"), NAME_MAX)
    base_url = _clean(data.get("base_url"), URL_MAX)
    auth_token = _clean(data.get("auth_token"), TOKEN_MAX)

    if not name:
        return jsonify({"error": "服务器名称不能为空"}), 400
    if not base_url:
        return jsonify({"error": "Base URL 不能为空"}), 400
    if not auth_token:
        return jsonify({"error": "Bearer 令牌不能为空"}), 400

    server_id = db.create_mcp_server(class_id, name, base_url, auth_token)
    if server_id is None:
        return jsonify({"error": "本班已有同名 MCP 服务器"}), 409

    row = db.get_mcp_server(server_id)
    return jsonify({"server": _public_server(row)}), 201


@bp.route("/api/mcp/servers/<int:server_id>/connect", methods=["POST"])
@role_required("teacher")
@api_spec(
    summary="连接 MCP 服务器并发现工具（教师）",
    tags=["MCP"],
    description=(
        "对服务器执行 initialize 握手，随后 tools/list 并把工具清单缓存到本系统。"
        "连接失败返回 400 与错误原因，不影响已有的检索与对话主路径。"
    ),
    responses={
        "200": {"description": "连接成功，返回缓存的工具列表"},
        "400": {"description": "连接或发现失败"},
        "404": {"description": "服务器不属于本班"},
    },
)
def connect_server(server_id: int):
    row = _owned_server(server_id)
    if row is None:
        return NOT_FOUND

    try:
        mcp_client.initialize(row["base_url"], row["auth_token"])
        tools = mcp_client.list_tools(row["base_url"], row["auth_token"])
    except mcp_client.MCPClientError as exc:
        return jsonify({"error": f"连接 MCP 服务器失败：{exc.friendly}"}), 400

    # Persist the discovered tool catalog and mark the server connected.
    db.set_server_tools(server_id, tools)
    db.set_mcp_connected(server_id, True)

    refreshed = db.get_mcp_server(server_id)
    return jsonify({
        "server": _public_server(refreshed),
        "tools": db.list_server_tools(server_id),
    })


@bp.route("/api/mcp/servers/<int:server_id>/disconnect", methods=["POST"])
@role_required("teacher")
@api_spec(
    summary="断开 MCP 服务器（教师）",
    tags=["MCP"],
    description="清空已缓存的工具清单并标记为未连接；此后该服务器的工具不可再调用。",
)
def disconnect_server(server_id: int):
    row = _owned_server(server_id)
    if row is None:
        return NOT_FOUND
    # Drop the cached tools and flip the flag; skills that referenced them
    # will be denied at save/call time.
    db.set_server_tools(server_id, [])
    db.set_mcp_connected(server_id, False)
    refreshed = db.get_mcp_server(server_id)
    return jsonify({"server": _public_server(refreshed)})


@bp.route("/api/mcp/servers/<int:server_id>/tools", methods=["GET"])
@login_required
@api_spec(
    summary="列出服务器已发现的工具",
    tags=["MCP"],
    description="仅返回本班服务器缓存的工具；未连接时列表为空。",
)
def list_server_tools(server_id: int):
    row = _owned_server(server_id)
    if row is None:
        return NOT_FOUND
    return jsonify({
        "server": _public_server(row),
        "tools": db.list_server_tools(server_id),
    })


@bp.route("/api/mcp/servers/<int:server_id>", methods=["DELETE"])
@role_required("teacher")
@api_spec(
    summary="删除 MCP 服务器登记（教师）",
    tags=["MCP"],
    description="级联删除已缓存的工具；审计记录保留 server_name 快照。",
)
def delete_server(server_id: int):
    row = _owned_server(server_id)
    if row is None:
        return NOT_FOUND
    db.delete_mcp_server(server_id)
    return jsonify({"ok": True})


# ---------------------------------------------------------------------------
# Audit
# ---------------------------------------------------------------------------

@bp.route("/api/mcp/audit", methods=["GET"])
@role_required("teacher")
@api_spec(
    summary="查看本班 MCP 工具调用审计（教师）",
    tags=["MCP"],
    description="至少含 tool@server、状态、用户与时间，按时间倒序。",
    parameters=[
        {"name": "limit", "in": "query", "schema": {"type": "integer", "default": 100}},
    ],
)
def list_audit():
    try:
        limit = int(request.args.get("limit", 100))
    except (TypeError, ValueError):
        limit = 100
    limit = max(1, min(limit, 500))

    rows = db.list_mcp_audit(g.current_user["class_id"], limit=limit)
    # Enrich with the tool@server display string for the audit table.
    enriched = []
    for r in rows:
        ref = f"{r['tool_name']}@{r['server_name']}" if r.get("server_name") else r["tool_name"]
        enriched.append({**r, "tool_ref": ref})
    return jsonify({"audit": enriched})
