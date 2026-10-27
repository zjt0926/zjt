"""Thin client for any OpenAI-compatible Chat Completions API.

Works with DeepSeek (https://api.deepseek.com), SiliconFlow, OpenRouter,
vLLM, etc. — only the base URL, model name and API key differ, and all
three come from environment variables read by the app factory.

Only streaming calls are used: ``stream_chat`` yields content deltas as
the upstream emits them, so the assistant's SSE response passes tokens
straight through to the browser.
"""

import json
from typing import Iterator, Optional

import requests

DEFAULT_TIMEOUT = 60


class LLMError(Exception):
    """Upstream call failure.

    ``status`` is the HTTP status (``None`` for network/timeout errors);
    ``friendly`` is a safe, user-facing Chinese message.
    """

    def __init__(self, message: str, status: Optional[int] = None):
        super().__init__(message)
        self.status = status
        if status == 401:
            self.friendly = "AI 服务拒绝了请求：API Key 无效或已过期（401）"
        elif status == 402:
            self.friendly = "AI 账户余额不足，请充值后再试（402）"
        elif status == 429:
            self.friendly = "AI 服务限流或额度已用尽，请稍后再试（429）"
        elif status is not None and status >= 500:
            self.friendly = f"AI 服务暂时不可用（上游 {status}），请稍后再试"
        elif status is not None:
            self.friendly = f"AI 服务返回错误（{status}）"
        else:
            self.friendly = f"无法连接 AI 服务：{message}"


def is_configured(config: Optional[dict] = None) -> bool:
    """Whether an API key/base URL/model are present in the app config."""
    if config is None:
        from flask import current_app
        config = current_app.config
    return bool(
        config.get("LLM_API_KEY")
        and config.get("LLM_BASE_URL")
        and config.get("LLM_MODEL")
    )


def stream_chat(
    messages: list[dict],
    config: Optional[dict] = None,
    timeout: Optional[int] = None,
) -> Iterator[str]:
    """Call ``POST {base_url}/chat/completions`` with ``stream=True``.

    Yields incremental content strings. Raises :class:`LLMError` for
    non-2xx responses, network problems, timeouts, or malformed streams.
    """
    if config is None:
        from flask import current_app
        config = current_app.config

    base_url = config["LLM_BASE_URL"].rstrip("/")
    api_key = config["LLM_API_KEY"]
    model = config["LLM_MODEL"]
    if timeout is None:
        timeout = config.get("LLM_TIMEOUT", DEFAULT_TIMEOUT)

    url = f"{base_url}/chat/completions"
    payload = {"model": model, "messages": messages, "stream": True}
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
        "Accept": "text/event-stream",
    }

    try:
        resp = requests.post(
            url,
            headers=headers,
            data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            stream=True,
            timeout=timeout,
        )
    except requests.Timeout as exc:
        raise LLMError("请求超时", status=None) from exc
    except requests.RequestException as exc:
        raise LLMError(str(exc), status=None) from exc

    if resp.status_code != 200:
        # Read just enough to surface the upstream error message.
        body = resp.text[:500]
        resp.close()
        raise LLMError(body, status=resp.status_code)

    # SSE has no reliable charset parameter; force UTF-8 so Chinese
    # content is not mis-decoded as ISO-8859-1.
    resp.encoding = "utf-8"
    try:
        for raw_line in resp.iter_lines(decode_unicode=True):
            if not raw_line:
                continue
            if not raw_line.startswith("data:"):
                continue
            data = raw_line[len("data:"):].strip()
            if data == "[DONE]":
                break
            try:
                chunk = json.loads(data)
            except json.JSONDecodeError:
                # Some gateways emit keep-alive comments; skip them.
                continue
            choices = chunk.get("choices") or []
            if not choices:
                continue
            delta = choices[0].get("delta") or {}
            piece = delta.get("content")
            if piece:
                yield piece
    except requests.RequestException as exc:
        raise LLMError(str(exc), status=None) from exc
    finally:
        resp.close()
