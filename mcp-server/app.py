"""Minimal MCP (Model Context Protocol) server built with Flask.

Exposes:
  - ``GET  /health``   : liveness probe (public).
  - ``POST /mcp``      : JSON-RPC 2.0 endpoint implementing the MCP
                         ``initialize``, ``tools/list`` and ``tools/call``
                         methods.

Authentication
--------------
Every ``/mcp`` request must carry ``Authorization: Bearer <token>`` where
``<token>`` equals the ``MCP_AUTH_TOKEN`` environment variable. Missing or
wrong tokens return HTTP 401; the response body is a valid JSON-RPC error
object so clients can distinguish auth failures from protocol errors.

Tools
-----
Two teaching-oriented tools are exposed (names are lowercase, no hyphens,
so they match the host-side ``tool_name@server_name`` reference rule):

  * ``formula_hint``  : given a ``topic`` string, return a short formula
                        / key-concept hint.
  * ``unit_convert``  : convert a numeric ``value`` between ``from_unit``
                        and ``to_unit`` (length / mass / temperature).
"""

import os
from typing import Any

from flask import Flask, jsonify, request

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

AUTH_TOKEN = os.environ.get("MCP_AUTH_TOKEN", "").strip()
SERVER_NAME = os.environ.get("MCP_SERVER_NAME", "campus-tools")
SERVER_VERSION = os.environ.get("MCP_SERVER_VERSION", "1.0.0")
MCP_PROTOCOL_VERSION = "2024-11-05"

app = Flask(__name__)


# ---------------------------------------------------------------------------
# Tool implementations
# ---------------------------------------------------------------------------

# A small static knowledge base used by ``formula_hint``. Real deployments
# could query a database; here the tool simply returns a deterministic
# text result so the host-side call path is fully exercised.
_FORMULA_HINTS: dict[str, str] = {
    "二次函数": "二次函数 y=ax²+bx+c（a≠0）：顶点坐标 (-b/2a, (4ac-b²)/4a)；"
                "对称轴 x=-b/2a；判别式 Δ=b²-4ac 决定与 x 轴交点个数。",
    "一次函数": "一次函数 y=kx+b（k≠0）：斜率 k 决定增减性，截距 b 为与 y 轴交点；"
                "k>0 时 y 随 x 增大而增大，k<0 时减小。",
    "三角函数": "sin²θ + cos²θ = 1；tanθ = sinθ/cosθ；"
                "正弦定理 a/sinA = b/sinB = c/sinC = 2R。",
    "勾股定理": "直角三角形中，两直角边 a、b 与斜边 c 满足 a² + b² = c²。",
    "等差数列": "通项 aₙ = a₁ + (n-1)d；前 n 项和 Sₙ = n(a₁+aₙ)/2 = na₁ + n(n-1)d/2。",
    "等比数列": "通项 aₙ = a₁·qⁿ⁻¹；前 n 项和 Sₙ = a₁(1-qⁿ)/(1-q)（q≠1）。",
}

# Length and mass conversions are expressed as "factor to base unit".
_LENGTH_TO_METER = {
    "mm": 0.001, "cm": 0.01, "dm": 0.1, "m": 1.0,
    "km": 1000.0, "inch": 0.0254, "ft": 0.3048, "mile": 1609.344,
}
_MASS_TO_KG = {
    "mg": 1e-6, "g": 1e-3, "kg": 1.0, "t": 1000.0, "lb": 0.45359237,
}


def _tool_formula_hint(arguments: dict[str, Any]) -> dict[str, Any]:
    # Accept either an explicit ``topic`` or a free-form ``question``;
    # when only a question is supplied we scan it for a known topic so a
    # natural-language query still yields a useful hint.
    topic = (arguments.get("topic") or "").strip()
    if not topic:
        topic = (arguments.get("question") or "").strip()
    if not topic:
        return _content("请提供 topic 参数（如「二次函数」）。")

    hint = _FORMULA_HINTS.get(topic)
    if hint is None:
        for known, text in _FORMULA_HINTS.items():
            if known in topic:
                hint = text
                break
    if hint is None:
        known = "、".join(_FORMULA_HINTS.keys())
        return _content(
            f"暂未收录「{topic}」的公式提示。已收录主题：{known}。"
            "可尝试其中之一。"
        )
    return _content(hint)


def _tool_unit_convert(arguments: dict[str, Any]) -> dict[str, Any]:
    try:
        value = float(arguments.get("value"))
    except (TypeError, ValueError):
        return _content("参数 value 必须是数字。")

    from_unit = str(arguments.get("from_unit") or "").strip().lower()
    to_unit = str(arguments.get("to_unit") or "").strip().lower()
    if not from_unit or not to_unit:
        return _content("请提供 from_unit 和 to_unit（如 m、cm、kg、g、c、f）。")

    # Temperature is special (affine, not multiplicative).
    if from_unit in ("c", "f", "k") and to_unit in ("c", "f", "k"):
        result = _convert_temperature(value, from_unit, to_unit)
        return _content(f"{value} {from_unit.upper()} = {result:.4f} {to_unit.upper()}")

    if from_unit in _LENGTH_TO_METER and to_unit in _LENGTH_TO_METER:
        base = value * _LENGTH_TO_METER[from_unit]
        result = base / _LENGTH_TO_METER[to_unit]
        return _content(f"{value} {from_unit} = {result:.6f} {to_unit}")

    if from_unit in _MASS_TO_KG and to_unit in _MASS_TO_KG:
        base = value * _MASS_TO_KG[from_unit]
        result = base / _MASS_TO_KG[to_unit]
        return _content(f"{value} {from_unit} = {result:.6f} {to_unit}")

    return _content(
        f"不支持的单位转换：{from_unit} → {to_unit}。"
        "支持长度(mm/cm/dm/m/km/inch/ft/mile)、"
        "质量(mg/g/kg/t/lb)、温度(C/F/K)。"
    )


def _convert_temperature(value: float, src: str, dst: str) -> float:
    # To Celsius first.
    if src == "c":
        c = value
    elif src == "f":
        c = (value - 32) * 5.0 / 9.0
    else:  # k
        c = value - 273.15
    # From Celsius to target.
    if dst == "c":
        return c
    if dst == "f":
        return c * 9.0 / 5.0 + 32
    return c + 273.15


def _content(text: str) -> dict[str, Any]:
    """Wrap a text payload in the MCP tool-call result shape."""
    return {"content": [{"type": "text", "text": text}]}


TOOLS: list[dict[str, Any]] = [
    {
        "name": "formula_hint",
        "description": "根据学科主题（topic）返回一段公式或核心概念提示文本。",
        "inputSchema": {
            "type": "object",
            "properties": {
                "topic": {"type": "string", "description": "主题，如「二次函数」"},
            },
            "required": ["topic"],
        },
    },
    {
        "name": "unit_convert",
        "description": "在长度、质量或温度单位之间转换数值，返回带单位的文本结果。",
        "inputSchema": {
            "type": "object",
            "properties": {
                "value": {"type": "number", "description": "待转换数值"},
                "from_unit": {"type": "string", "description": "源单位（如 m、kg、c）"},
                "to_unit": {"type": "string", "description": "目标单位（如 cm、g、f）"},
            },
            "required": ["value", "from_unit", "to_unit"],
        },
    },
]

_TOOL_IMPLS = {
    "formula_hint": _tool_formula_hint,
    "unit_convert": _tool_unit_convert,
}


# ---------------------------------------------------------------------------
# JSON-RPC helpers
# ---------------------------------------------------------------------------

def _rpc_result(req_id: Any, result: Any) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "result": result, "id": req_id}


def _rpc_error(req_id: Any, code: int, message: str) -> dict[str, Any]:
    return {
        "jsonrpc": "2.0",
        "error": {"code": code, "message": message},
        "id": req_id,
    }


def _handle_request(payload: dict[str, Any]) -> tuple[dict[str, Any], int]:
    """Dispatch one JSON-RPC request. Returns (response_body, http_status)."""
    if not isinstance(payload, dict):
        return _rpc_error(None, -32600, "Invalid Request: expected a JSON object"), 400

    req_id = payload.get("id")
    method = payload.get("method")
    params = payload.get("params") or {}

    if not isinstance(method, str):
        return _rpc_error(req_id, -32600, "Invalid Request: method must be a string"), 400

    if method == "initialize":
        return _rpc_result(
            req_id,
            {
                "protocolVersion": MCP_PROTOCOL_VERSION,
                "capabilities": {"tools": {}},
                "serverInfo": {"name": SERVER_NAME, "version": SERVER_VERSION},
            },
        ), 200

    if method == "tools/list":
        return _rpc_result(req_id, {"tools": TOOLS}), 200

    if method == "tools/call":
        name = params.get("name") if isinstance(params, dict) else None
        arguments = params.get("arguments", {}) if isinstance(params, dict) else {}
        impl = _TOOL_IMPLS.get(name)
        if impl is None:
            return _rpc_error(req_id, -32601, f"Method not found: tool '{name}'"), 404
        result = impl(arguments or {})
        return _rpc_result(req_id, result), 200

    if method in ("notifications/initialized", "ping"):
        # Notifications carry no id; MCP clients send these after initialize.
        if req_id is None:
            return {}, 202
        return _rpc_result(req_id, {}), 200

    return _rpc_error(req_id, -32601, f"Method not found: {method}"), 404


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@app.route("/health", methods=["GET"])
def health():
    return jsonify({"status": "ok", "server": SERVER_NAME}), 200


@app.route("/mcp", methods=["POST"])
def mcp():
    # Auth: require a matching Bearer token. 401 with a JSON-RPC error body
    # so the host-side client can surface a clear reason.
    header = request.headers.get("Authorization", "")
    token = header[len("Bearer "):].strip() if header.startswith("Bearer ") else ""
    if not AUTH_TOKEN or token != AUTH_TOKEN:
        return jsonify(_rpc_error(None, -32001, "Unauthorized")), 401

    payload = request.get_json(silent=True)
    body, status = _handle_request(payload)
    # Notifications (id is None and the method is a notification) get an
    # empty 202 response, otherwise a JSON-RPC result/error envelope.
    if not body:
        return "", status
    return jsonify(body), status


if __name__ == "__main__":
    port = int(os.environ.get("PORT", "8090"))
    app.run(host="0.0.0.0", port=port)
