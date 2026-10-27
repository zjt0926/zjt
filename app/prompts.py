"""Prompt template library (提示词模板).

A template is a class-scoped, reusable persona prompt. Creating an agent
"from" a template copies the template body into the agent's own prompt,
so the template is only a starting point — later template edits never
silently change agents that were already created.

Authorization mirrors the rest of the platform: any signed-in member of
the class may list templates; only teachers may create / update / delete.
A template belonging to another class (or a missing id) is always 404,
never 403, so the API does not leak other classes' records.
"""

from flask import Blueprint, g, jsonify, request

from . import db
from .auth import login_required, role_required
from .openapi import api_spec

bp = Blueprint("prompts", __name__)

NAME_MAX = 50
DESCRIPTION_MAX = 200
BODY_MAX = 2000
NOT_FOUND = ({"error": "not found"}, 404)


def _clean(value, limit: int) -> str:
    return (value or "").strip()[:limit] if value else ""


def _owned_template(template_id: int):
    """Return the template only when it exists in the caller's class."""
    row = db.get_prompt_template(template_id)
    if row is None or row["class_id"] != g.current_user["class_id"]:
        return None
    return row


@bp.route("/api/prompt-templates", methods=["GET"])
@login_required
@api_spec(
    summary="列出本班提示词模板",
    tags=["提示词模板"],
    description="返回本班全部模板（含内置模板与教师自建模板）。",
)
def list_templates():
    return jsonify(
        {"templates": db.list_prompt_templates(g.current_user["class_id"])}
    )


@bp.route("/api/prompt-templates", methods=["POST"])
@role_required("teacher")
@api_spec(
    summary="创建提示词模板（教师）",
    tags=["提示词模板"],
    description="班内名称唯一；body 即提示词正文，可作为新建智能体的起点。",
    requestBody={
        "required": True,
        "content": {
            "application/json": {
                "schema": {
                    "type": "object",
                    "required": ["name", "body"],
                    "properties": {
                        "name": {"type": "string", "example": "苏格拉底式启发"},
                        "description": {
                            "type": "string",
                            "example": "不直接给答案，用追问带学生推结论",
                        },
                        "body": {
                            "type": "string",
                            "example": "你是一位苏格拉底式导师。绝不直接给出最终答案……",
                        },
                    },
                }
            }
        },
        "responses": {
            "201": {"description": "创建成功"},
            "409": {"description": "班内名称重复"},
        },
    },
)
def create_template():
    data = request.get_json(silent=True) or {}
    name = _clean(data.get("name"), NAME_MAX)
    description = _clean(data.get("description"), DESCRIPTION_MAX)
    body = _clean(data.get("body"), BODY_MAX)
    if not name:
        return jsonify({"error": "模板名称不能为空"}), 400
    if not body:
        return jsonify({"error": "模板内容不能为空"}), 400

    template_id = db.create_prompt_template(
        g.current_user["class_id"], name, description, body
    )
    if template_id is None:
        return jsonify({"error": "本班已有同名模板"}), 409
    return jsonify({"template": db.get_prompt_template(template_id)}), 201


@bp.route("/api/prompt-templates/<int:template_id>/update", methods=["POST"])
@role_required("teacher")
@api_spec(
    summary="修改提示词模板（教师）",
    tags=["提示词模板"],
    description=(
        "部分更新 name/description/body。模板修改不影响已创建智能体，"
        "因为创建时已复制正文；只影响之后新建或重新选用模板的智能体。"
    ),
    requestBody={
        "required": False,
        "content": {
            "application/json": {
                "schema": {
                    "type": "object",
                    "properties": {
                        "name": {"type": "string"},
                        "description": {"type": "string"},
                        "body": {"type": "string"},
                    },
                }
            }
        },
    },
    responses={"200": {"description": "已更新"}, "404": {"description": "视为不存在"}},
)
def update_template(template_id: int):
    if _owned_template(template_id) is None:
        return NOT_FOUND

    data = request.get_json(silent=True) or {}
    fields: dict = {}
    if "name" in data:
        name = _clean(data.get("name"), NAME_MAX)
        if not name:
            return jsonify({"error": "模板名称不能为空"}), 400
        for t in db.list_prompt_templates(g.current_user["class_id"]):
            if t["name"] == name and t["id"] != template_id:
                return jsonify({"error": "本班已有同名模板"}), 409
        fields["name"] = name
    if "description" in data:
        fields["description"] = _clean(data.get("description"), DESCRIPTION_MAX)
    if "body" in data:
        body = _clean(data.get("body"), BODY_MAX)
        if not body:
            return jsonify({"error": "模板内容不能为空"}), 400
        fields["body"] = body

    db.update_prompt_template_fields(template_id, fields)
    return jsonify({"template": db.get_prompt_template(template_id)})


@bp.route("/api/prompt-templates/<int:template_id>", methods=["DELETE"])
@role_required("teacher")
@api_spec(
    summary="删除提示词模板（教师）",
    tags=["提示词模板"],
    description="只删除模板本身；已经由该模板创建的智能体保留其提示词副本。",
    responses={"200": {"description": "已删除"}, "404": {"description": "视为不存在"}},
)
def delete_template(template_id: int):
    if _owned_template(template_id) is None:
        return NOT_FOUND
    db.delete_prompt_template(template_id)
    return jsonify({"ok": True})
