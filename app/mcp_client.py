"""JSON-RPC 2.0 client for talking to a self-hosted MCP server over HTTP.

The MCP server exposes a single ``POST /mcp`` endpoint. Every call is a
JSON-RPC 2.0 request object authenticated with ``Authorization: Bearer
<token>``. This module implements the three methods the host needs:

* ``initialize``  — handshake (server info + capabilities).
* ``list_tools``  — ``tools/list``, returns the discovered tool catalog.
* ``call_tool``   — ``tools/call``, invokes one tool by name with args.

All network / protocol errors raise :class:`MCPClientError` so callers can
record a ``denied`` / ``error`` audit row and surface a clear message
without breaking the rest of the assistant turn.
"""

import json
from typing import Any, Optional

import requests

DEFAULT_TIMEOUT = 15


class MCPClientError(Exception):
    """A failure to reach or parse the MCP server.

    ``friendly`` is a safe, user-facing Chinese message used in audit /
    SSE error events; ``status`` is the HTTP status (None for network
    errors) and ``code`` is the JSON-RPC error code when available.
    """

    def __init__(
        self,
        message: str,
        status: Optional[int] = None,
        code: Optional[int] = None,
    ):
        super().__init__(message)
        self.status = status
        self.code = code
        if status == 401:
            self.friendly = "MCP 服务器拒绝了请求：令牌无效（401）"
        elif status is not None and status >= 500:
            self.friendly = f"MCP 服务器暂时不可用（上游 {status}）"
        elif status is not None:
            self.friendly = f"MCP 服务器返回错误（{status}）：{message}"
        else:
            self.friendly = f"无法连接 MCP 服务器：{message}"


def _post(base_url: str, token: str, payload: dict, timeout: int) -> dict:
    """Send one JSON-RPC request and return the parsed result.

    Raises :class:`MCPClientError` on transport errors, non-2xx responses
    (including 401 from a bad token), or a JSON-RPC ``error`` envelope.
    """
    url = base_url.rstrip("/") + "/mcp"
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
        "Accept": "application/json",
    }
    try:
        resp = requests.post(
            url,
            headers=headers,
            data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            timeout=timeout,
        )
    except requests.Timeout:
        raise MCPClientError("请求超时", status=None)
    except requests.RequestException as exc:
        raise MCPClientError(str(exc), status=None)

    if resp.status_code != 200:
        body = resp.text[:300]
        resp.close()
        raise MCPClientError(body, status=resp.status_code)

    try:
        data = resp.json()
    except ValueError:
        raise MCPClientError("MCP 服务器返回了非 JSON 响应", status=resp.status_code)
    finally:
        resp.close()

    if isinstance(data, dict) and "error" in data:
        err = data["error"] or {}
        raise MCPClientError(
            str(err.get("message", "unknown error")),
            status=resp.status_code,
            code=err.get("code"),
        )
    return data.get("result") if isinstance(data, dict) else None


def initialize(base_url: str, token: str, timeout: int = DEFAULT_TIMEOUT) -> dict:
    """Perform the MCP initialize handshake.

    Returns the server info dict. Raises :class:`MCPClientError` on
    failure (used by the host to decide whether the connection succeeds).
    """
    payload = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "initialize",
        "params": {
            "protocolVersion": "2024-11-05",
            "capabilities": {},
            "clientInfo": {"name": "campusclaw", "version": "1.0.0"},
        },
    }
    result = _post(base_url, token, payload, timeout)
    return result or {}


def list_tools(
    base_url: str, token: str, timeout: int = DEFAULT_TIMEOUT
) -> list[dict]:
    """Return the list of tools advertised by the server.

    Each item has at least ``name`` and ``description`` (plus
    ``inputSchema`` when provided). Raises :class:`MCPClientError`.
    """
    payload = {"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}}
    result = _post(base_url, token, payload, timeout) or {}
    tools = result.get("tools")
    if not isinstance(tools, list):
        return []
    return tools


def call_tool(
    base_url: str,
    token: str,
    tool_name: str,
    arguments: Optional[dict] = None,
    timeout: int = DEFAULT_TIMEOUT,
) -> str:
    """Invoke a tool and return its text content.

    MCP ``tools/call`` returns ``{"content": [{"type": "text", "text":
    ...}]}``. This helper concatenates all text parts into one string so
    the host can append it directly to the user message as data.
    """
    payload = {
        "jsonrpc": "2.0",
        "id": 3,
        "method": "tools/call",
        "params": {"name": tool_name, "arguments": arguments or {}},
    }
    result = _post(base_url, token, payload, timeout) or {}
    content = result.get("content")
    if not isinstance(content, list):
        return ""
    parts: list[str] = []
    for item in content:
        if isinstance(item, dict) and item.get("type") == "text":
            parts.append(str(item.get("text", "")))
    return "".join(parts)
