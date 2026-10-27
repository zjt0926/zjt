"""OpenAPI 3 description generated from per-route code annotations.

Views annotate themselves with :func:`api_spec` (a plain dict of
OpenAPI operation fragments — summary, tags, requestBody, parameters,
responses). At request time :func:`build_openapi` walks the app's URL
map, matches annotated view functions to their rules and emits an
OpenAPI 3.0 document at ``GET /api/openapi.json``. The human-facing
Swagger UI is served at ``/docs`` and loads the same JSON, so the docs
can be used to try the main endpoints directly.

Every annotated operation is secured with ``bearerAuth`` (the JWT)
unless the annotation sets ``no_auth=True`` (login / health).
"""

import re

from flask import Blueprint, jsonify, render_template

bp = Blueprint("openapi", __name__)


def api_spec(**operation):
    """Attach an OpenAPI operation object (dict fragments) to a view.

    Usage::

        @bp.route("/api/agents", methods=["POST"])
        @api_spec(summary="创建智能体", tags=["智能体"],
                  requestBody={...}, no_auth=False)
        def create_agent(): ...
    """

    def deco(fn):
        fn._openapi_operation = operation
        return fn

    return deco


_PARAM_RE = re.compile(r"<(?:(?P<converter>[^:<>]+):)?(?P<name>[^<>]+)>")


def _path_item(rule):
    """Convert a Flask rule to ``/path/{param}`` + OpenAPI path params."""
    path = _PARAM_RE.sub(lambda m: "{" + m.group("name") + "}", rule.rule)
    params = [
        {
            "name": m.group("name"),
            "in": "path",
            "required": True,
            "schema": {
                "type": "integer" if m.group("converter") == "int" else "string"
            },
        }
        for m in _PARAM_RE.finditer(rule.rule)
    ]
    return path, params


def build_openapi(app) -> dict:
    """Assemble the OpenAPI document from annotated views."""
    paths: dict = {}
    for rule in app.url_map.iter_rules():
        if rule.endpoint.startswith("static"):
            continue
        view = app.view_functions.get(rule.endpoint)
        base_op = getattr(view, "_openapi_operation", None)
        if base_op is None:
            continue

        path, path_params = _path_item(rule)
        item = paths.setdefault(path, {})
        for method in sorted(rule.methods - {"HEAD", "OPTIONS"}):
            operation = dict(base_op)
            parameters = path_params + list(operation.get("parameters", []))
            if parameters:
                operation["parameters"] = parameters
            if operation.pop("no_auth", False):
                operation.pop("security", None)
            else:
                operation["security"] = [{"bearerAuth": []}]
            operation.setdefault("responses", {"200": {"description": "成功"}})
            item[method.lower()] = operation

    return {
        "openapi": "3.0.3",
        "info": {
            "title": "CampusClaw API",
            "description": (
                "智能体解题助手：教师创建智能体并绑定技能与材料；学生选定"
                "智能体后连续追问，回答以 SSE 流式返回并携带编号引用。"
                "鉴权方式：登录后拿 JWT，所有受保护接口以 "
                "``Authorization: Bearer <token>`` 调用。"
            ),
            "version": "1.0.0",
        },
        "servers": [{"url": "/"}],
        "components": {
            "securitySchemes": {
                "bearerAuth": {
                    "type": "http",
                    "scheme": "bearer",
                    "bearerFormat": "JWT",
                }
            }
        },
        "paths": paths,
    }


@bp.route("/api/openapi.json", methods=["GET"])
def openapi_json():
    """The generated OpenAPI document (public; describes the surface only)."""
    from flask import current_app

    return jsonify(build_openapi(current_app))


@bp.route("/docs", methods=["GET"])
def docs_page():
    """Swagger UI page backed by ``/api/openapi.json``."""
    return render_template("docs.html")
