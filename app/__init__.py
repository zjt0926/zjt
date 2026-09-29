"""CampusClaw Flask application package."""

import os

from flask import Flask

from .db import get_conn, init_db_if_needed


def create_app() -> Flask:
    """Create and configure the Flask application.

    The session signing key MUST be provided via the ``SECRET_KEY``
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

    # Ensure data directories exist.
    os.makedirs(os.path.dirname(os.path.abspath(app.config["DATABASE"])), exist_ok=True)
    os.makedirs(app.config["UPLOAD_FOLDER"], exist_ok=True)
    os.makedirs(app.config["VECTOR_DB_PATH"], exist_ok=True)

    # Initialize the database (idempotent: only seeds when missing).
    with app.app_context():
        init_db_if_needed(app.config["DATABASE"])

    # Session cookie hardening.
    app.config.update(
        SESSION_COOKIE_HTTPONLY=True,
        SESSION_COOKIE_SAMESITE="Lax",
    )

    from .auth import bp as auth_bp
    from .materials import bp as materials_bp
    from .search import bp as search_bp

    app.register_blueprint(auth_bp)
    app.register_blueprint(materials_bp)
    app.register_blueprint(search_bp)

    @app.route("/health", methods=["GET"])
    def health():
        return {"status": "ok"}, 200

    @app.route("/", methods=["GET"])
    def index():
        from flask import redirect, url_for
        return redirect(url_for("materials.list_materials_view"))

    return app


__all__ = ["create_app", "get_conn"]
