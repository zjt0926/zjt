"""Authentication: token (JWT) issuance, verification, and access decorators.

Identity is carried in a signed JWT token. The token is handed to the
client as JSON on login and must be sent back on every protected request
in the ``Authorization: Bearer <token>`` header. No cookies are used, so
page navigation alone is never authenticated — pages fetch their data
through the JSON APIs with the header attached.
"""

from datetime import datetime, timedelta, timezone
from functools import wraps

import bcrypt
import jwt
from flask import (
    Blueprint,
    current_app,
    g,
    jsonify,
    render_template,
    request,
)

from .db import get_user_by_username
from .openapi import api_spec

bp = Blueprint("auth", __name__)


def _verify_password(plaintext: str, password_hash: str) -> bool:
    """Constant-time bcrypt password verification."""
    try:
        return bcrypt.checkpw(
            plaintext.encode("utf-8"), password_hash.encode("utf-8")
        )
    except (ValueError, TypeError):
        return False


def issue_token(user: dict) -> str:
    """Sign a JWT carrying the user's identity.

    Payload: ``user_id``, ``role``, ``class_id``, ``exp``.
    Signed with ``SECRET_KEY`` using HS256.
    """
    expires_in = timedelta(hours=current_app.config["TOKEN_EXPIRES_HOURS"])
    payload = {
        "user_id": user["id"],
        "role": user["role"],
        "class_id": user["class_id"],
        "exp": datetime.now(timezone.utc) + expires_in,
    }
    return jwt.encode(payload, current_app.config["SECRET_KEY"], algorithm="HS256")


def decode_token(token: str):
    """Decode and verify a JWT. Returns the payload dict or ``None``.

    Returns ``None`` for expired, malformed, or bad-signature tokens.
    """
    try:
        return jwt.decode(
            token, current_app.config["SECRET_KEY"], algorithms=["HS256"]
        )
    except jwt.PyJWTError:
        return None


def _extract_token() -> str | None:
    """Pull the raw token from the ``Authorization: Bearer`` header.

    Returns ``None`` when the header is absent or malformed.
    """
    auth_header = request.headers.get("Authorization", "")
    if auth_header.startswith("Bearer "):
        return auth_header[len("Bearer "):].strip() or None
    return None


def get_current_identity() -> dict | None:
    """Resolve the current user's identity from the request token.

    Returns a dict with ``user_id``, ``role``, ``class_id``, or ``None``
    when no valid token is present.
    """
    token = _extract_token()
    if not token:
        return None
    payload = decode_token(token)
    if payload is None:
        return None
    return {
        "user_id": payload.get("user_id"),
        "role": payload.get("role"),
        "class_id": payload.get("class_id"),
    }


@bp.before_app_request
def _load_current_user():
    """Populate ``g.current_user`` for every request (may be ``None``)."""
    g.current_user = get_current_identity()


def login_required(view):
    """Decorator: require a valid token, else HTTP 401 JSON.

    All protected routes are JSON endpoints; page shells are public and
    load their data client-side through these endpoints.
    """

    @wraps(view)
    def wrapped(*args, **kwargs):
        if g.current_user is None:
            return jsonify({"error": "unauthorized"}), 401
        return view(*args, **kwargs)

    return wrapped


def role_required(role: str):
    """Decorator: require a valid token whose role equals ``role``.

    Returns 401 without a token and 403 when the role does not match.
    """

    def decorator(view):
        @wraps(view)
        def wrapped(*args, **kwargs):
            if g.current_user is None:
                return jsonify({"error": "unauthorized"}), 401
            if g.current_user.get("role") != role:
                return jsonify({"error": "forbidden"}), 403
            return view(*args, **kwargs)

        return wrapped

    return decorator


@bp.route("/login", methods=["GET", "POST"])
@api_spec(
    summary="登录获取 JWT",
    tags=["认证"],
    no_auth=True,
    requestBody={
        "required": True,
        "content": {
            "application/json": {
                "schema": {
                    "type": "object",
                    "required": ["username", "password"],
                    "properties": {
                        "username": {"type": "string", "example": "teacher_a"},
                        "password": {"type": "string", "example": "teacherpass"},
                    },
                }
            }
        },
    },
    responses={
        "200": {"description": "返回 token 与用户信息"},
        "401": {"description": "用户名或密码错误"},
    },
)
def login():
    """GET renders the login form; POST returns a JWT as JSON.

    Accepts either a JSON body or a form-encoded body so the page and API
    clients share one endpoint.
    """
    if request.method == "POST":
        data = request.get_json(silent=True) or request.form
        username = (data.get("username") or "").strip()
        password = data.get("password") or ""

        user = get_user_by_username(username)
        if user and _verify_password(password, user["password_hash"]):
            return jsonify(
                {
                    "token": issue_token(user),
                    "user": {
                        "id": user["id"],
                        "username": user["username"],
                        "role": user["role"],
                        "class_id": user["class_id"],
                    },
                }
            )

        # Generic failure message — do not reveal whether the user exists.
        return jsonify({"error": "用户名或密码错误"}), 401

    return render_template("login.html")


@bp.route("/logout", methods=["GET", "POST"])
def logout():
    """Stateless tokens: nothing to revoke server-side.

    The client discards its stored token. Kept as an endpoint so the nav
    link has a target.
    """
    return jsonify({"ok": True})