"""CampusClaw Flask application package."""

import os

from flask import Flask

from .db import (
    ensure_agent_schema,
    ensure_assistant_schema,
    ensure_mcp_schema,
    ensure_prompt_template_schema,
    get_conn,
    init_db_if_needed,
)
from .openapi import api_spec


def create_app() -> Flask:
    """Create and configure the Flask application.

    The token signing key MUST be provided via the ``SECRET_KEY``
    environment variable. Per the security spec, the application must
    not silently fall back to a hard-coded default, so we raise here
    if it is missing or empty.
    """
    app = Flask(__name__, instance_relative_config=True)

    secret_key = os.environ.get("SECRET_KEY")
    if not secret_key:
        raise RuntimeError(
            "SECRET_KEY environment variable is required and must not be empty. "
            "See .env.example and docker-compose.yml."
        )
    app.config["SECRET_KEY"] = secret_key

    # Token (JWT) expiration in hours. Default 24h; invalid values fall back.
    try:
        app.config["TOKEN_EXPIRES_HOURS"] = int(
            os.environ.get("TOKEN_EXPIRES_HOURS", 24)
        )
    except (TypeError, ValueError):
        app.config["TOKEN_EXPIRES_HOURS"] = 24

    # Persistent data locations (overridable for tests / compose).
    app.config["DATABASE"] = os.environ.get(
        "DATABASE_PATH", os.path.join(app.root_path, "..", "data", "app.db")
    )
    app.config["UPLOAD_FOLDER"] = os.environ.get(
        "UPLOAD_FOLDER", os.path.join(app.root_path, "..", "uploads")
    )
    app.config["VECTOR_DB_PATH"] = os.environ.get(
        "VECTOR_DB_PATH", os.path.join(app.root_path, "..", "data", "chroma")
    )
    app.config["MAX_CONTENT_LENGTH"] = 16 * 1024 * 1024  # 16 MB

    # Optional external LLM (any OpenAI-compatible Chat Completions API:
    # DeepSeek, SiliconFlow, OpenRouter, vLLM...). When unset, the ask
    # endpoint returns a clear "not configured" error rather than silently
    # falling back to a local canned answer.
    app.config["LLM_API_KEY"] = os.environ.get("LLM_API_KEY", "").strip()
    app.config["LLM_BASE_URL"] = os.environ.get("LLM_BASE_URL", "").strip()
    app.config["LLM_MODEL"] = os.environ.get("LLM_MODEL", "").strip()
    try:
        app.config["LLM_TIMEOUT"] = int(os.environ.get("LLM_TIMEOUT", 60))
    except (TypeError, ValueError):
        app.config["LLM_TIMEOUT"] = 60

    # Ensure data directories exist.
    os.makedirs(os.path.dirname(os.path.abspath(app.config["DATABASE"])), exist_ok=True)
    os.makedirs(app.config["UPLOAD_FOLDER"], exist_ok=True)
    os.makedirs(app.config["VECTOR_DB_PATH"], exist_ok=True)

    # Initialize the database (idempotent: only seeds when missing), then
    # run migrations: the assistant-feature tables for older databases and
    # the agent model (agents / agent_skills / agent_materials) on top.
    with app.app_context():
        init_db_if_needed(app.config["DATABASE"])
        ensure_assistant_schema(app.config["DATABASE"])
        ensure_prompt_template_schema(app.config["DATABASE"])
        ensure_agent_schema(app.config["DATABASE"])
        ensure_mcp_schema(app.config["DATABASE"])

    from .agents import bp as agents_bp
    from .assistant import bp as assistant_bp
    from .auth import bp as auth_bp
    from .materials import bp as materials_bp
    from .mcp import bp as mcp_bp
    from .openapi import bp as openapi_bp
    from .prompts import bp as prompts_bp
    from .search import bp as search_bp

    app.register_blueprint(auth_bp)
    app.register_blueprint(materials_bp)
    app.register_blueprint(search_bp)
    app.register_blueprint(assistant_bp)
    app.register_blueprint(agents_bp)
    app.register_blueprint(mcp_bp)
    app.register_blueprint(prompts_bp)
    app.register_blueprint(openapi_bp)

    @app.route("/health", methods=["GET"])
    @api_spec(summary="健康检查", tags=["系统"], no_auth=True)
    def health():
        return {"status": "ok"}, 200

    @app.route("/", methods=["GET"])
    def index():
        from flask import redirect, url_for
        return redirect(url_for("assistant.assistant_view"))

    return app


__all__ = ["create_app", "get_conn"]
