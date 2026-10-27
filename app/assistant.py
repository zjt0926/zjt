"""Routes for the problem-solving assistant (agent-locked conversations).

- Page shell: ``/assistant``.
- Conversations: creating one REQUIRES selecting an enabled agent of the
  caller's class; the conversation stays on that agent for life (any
  ``agent_id`` inside an ask body is ignored — switching means opening a
  new conversation). History is per-user only.
- Ask: ``POST /api/conversations/<id>/ask`` returns a ``text/event-stream``
  SSE response. The FIRST event (``meta``) already carries the numbered
  citation list; the body then arrives as repeated ``delta`` chunks and a
  final ``done``. The client consumes it with fetch + a streaming reader
  so the JWT stays in the ``Authorization`` header (EventSource cannot
  set headers).

Agent management (create / update / toggle / bind skills and materials)
lives in :mod:`app.agents`.
"""

import json

from flask import (
    Blueprint,
    Response,
    g,
    jsonify,
    render_template,
    request,
)

from . import agent, db, llm
from .auth import login_required
from .openapi import api_spec

bp = Blueprint("assistant", __name__)


# ---------------------------------------------------------------------------
# Page
# ---------------------------------------------------------------------------

@bp.route("/assistant", methods=["GET"])
def assistant_view():
    """Public shell; chat data and agents are fetched via the JSON APIs."""
    return render_template("assistant.html")


# ---------------------------------------------------------------------------
# Conversations
# ---------------------------------------------------------------------------

@bp.route("/api/conversations", methods=["GET"])
@login_required
@api_spec(summary="列出我的会话", tags=["会话"])
def list_conversations():
    return jsonify(
        {"conversations": db.list_conversations(g.current_user["user_id"])}
    )


@bp.route("/api/conversations", methods=["POST"])
@login_required
@api_spec(
    summary="新建会话（选定智能体）",
    tags=["会话"],
    description=(
        "必须选定本班一个已启用的智能体；此后该会话一直用它，"
        "换智能体要新开会话。未启用/他班/不存在的智能体返回一致的 404。"
    ),
    requestBody={"required": True, "content": {"application/json": {"schema": {
        "type": "object",
        "required": ["agent_id"],
        "properties": {"agent_id": {"type": "integer", "example": 1}},
    }}}},
)
def create_conversation():
    """Create a conversation bound to one enabled agent of the class.

    The agent must exist, belong to the caller's class and be enabled —
    anything else is the same 404 as "does not exist" (no probing).
    """
    data = request.get_json(silent=True) or {}
    agent_id = data.get("agent_id")
    if not agent_id:
        return jsonify({"error": "请先选择智能体"}), 400

    agent_row = db.get_agent(int(agent_id))
    if (
        agent_row is None
        or agent_row["class_id"] != g.current_user["class_id"]
        or not agent_row["enabled"]
    ):
        return {"error": "not found"}, 404

    conv_id = db.create_conversation(
        g.current_user["user_id"], g.current_user["class_id"], agent_row["id"]
    )
    conv = db.get_conversation(conv_id)
    conv["agent_name"] = agent_row["name"]
    return jsonify({"conversation": conv}), 201


def _owned_conversation(conv_id: int):
    """Fetch the conversation if it belongs to the caller in their class."""
    conv = db.get_conversation(conv_id)
    if conv is None:
        return None
    user = g.current_user
    if conv["user_id"] != user["user_id"] or conv["class_id"] != user["class_id"]:
        return None
    return conv


@bp.route("/api/conversations/<int:conv_id>/messages", methods=["GET"])
@login_required
@api_spec(summary="加载会话历史", tags=["会话"])
def get_messages(conv_id: int):
    conv = _owned_conversation(conv_id)
    if conv is None:
        return {"error": "not found"}, 404
    agent_row = db.get_agent(conv["agent_id"]) if conv["agent_id"] else None
    conv = {**conv, "agent_name": agent_row["name"] if agent_row else None}
    return jsonify({"conversation": conv, "messages": db.list_messages(conv_id)})


# ---------------------------------------------------------------------------
# Ask (SSE)
# ---------------------------------------------------------------------------

def _sse(event: str, data: dict) -> str:
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


@bp.route("/api/conversations/<int:conv_id>/ask", methods=["POST"])
@login_required
@api_spec(
    summary="提问（SSE 流式回答）",
    tags=["会话"],
    description=(
        "回答按会话锁定的智能体生成（消息体中即使带其他 agent_id 也会被忽略）。"
        "响应为 text/event-stream：首个 meta 事件携带完整引用列表，随后 delta "
        "逐段增量、done 收尾；引用编号 [n] 与 meta.citations 一一对应。"
    ),
    requestBody={"required": True, "content": {"application/json": {"schema": {
        "type": "object",
        "required": ["question"],
        "properties": {"question": {"type": "string", "example": "这道题第二步错在哪？"}},
    }}}},
    responses={
        "200": {"description": "SSE 事件流"},
        "404": {"description": "会话不存在或不属于当前用户"},
        "503": {"description": "LLM 未配置"},
    },
)
def ask(conv_id: int):
    """Run one turn on the conversation's locked agent, streamed as SSE.

    Events:
      - ``meta``  : {agent, skill, citations, retrieval_query} — first
                    event, citations arrive before any body text
      - ``delta`` : {text}
      - ``done``  : {message_id}
      - ``error`` : {error}

    The agent comes from the conversation row, never from the message
    body — asking with another agent's id still answers with the
    conversation's own agent. No local canned-answer fallback exists:
    when the LLM is not configured, HTTP 503 is returned; when the
    upstream call fails, an ``error`` event with the reason is emitted.
    """
    conv = _owned_conversation(conv_id)
    if conv is None:
        return {"error": "not found"}, 404

    agent_row = db.get_agent(conv["agent_id"]) if conv["agent_id"] else None
    if agent_row is None:
        return jsonify({"error": "该会话未绑定有效智能体，请新建会话"}), 400

    data = request.get_json(silent=True) or {}
    question = (data.get("question") or "").strip()
    if not question:
        return jsonify({"error": "问题不能为空"}), 400
    if len(question) > 1000:
        return jsonify({"error": "问题不能超过 1000 字"}), 400

    if not llm.is_configured():
        return jsonify(
            {
                "error": "AI 助手尚未配置：请在服务端设置 LLM_BASE_URL、"
                "LLM_API_KEY、LLM_MODEL 环境变量"
            }
        ), 503

    history = db.list_messages(conv_id, limit=agent.HISTORY_LIMIT)
    try:
        turn = agent.build_turn(
            agent_row, question, history, user_id=g.current_user["user_id"]
        )
    except Exception:  # pragma: no cover - defensive
        from flask import current_app
        current_app.logger.exception("assistant turn build failed")
        return jsonify({"error": "助手处理失败，请重试"}), 500

    # Persist the user turn immediately so it survives a mid-stream drop.
    db.insert_message(conv_id, "user", question)
    if not conv["title"] or conv["title"] == "新会话":
        db.rename_conversation(conv_id, question[:20])

    def generate():
        collected: list[str] = []
        # First event: references are already complete before any text.
        yield _sse(
            "meta",
            {
                "agent": {"id": agent_row["id"], "name": agent_row["name"]},
                "skill": turn["skill"],
                "citations": turn["citations"],
                "retrieval_query": turn["retrieval_query"],
                "tool_calls": turn["tool_calls"],
            },
        )
        try:
            for piece in llm.stream_chat(turn["messages"]):
                collected.append(piece)
                yield _sse("delta", {"text": piece})
        except llm.LLMError as exc:
            yield _sse("error", {"error": exc.friendly})
            return

        answer = "".join(collected).strip()
        if not answer:
            yield _sse("error", {"error": "AI 返回了空回答"})
            return

        message_id = db.insert_message(
            conv_id,
            "assistant",
            answer,
            citations=json.dumps(turn["citations"], ensure_ascii=False),
            mode="guide" if turn["skill"] else "direct",
        )
        yield _sse("done", {"message_id": message_id})

    # stream_with_context keeps the request/app context alive while the
    # SSE iterator is consumed (it runs after the view has returned).
    from flask import stream_with_context

    return Response(
        stream_with_context(generate()),
        mimetype="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",  # ask nginx not to buffer SSE
            "Connection": "keep-alive",
        },
    )
