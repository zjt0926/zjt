"""Authentication: login, logout, session, and access decorators."""

from functools import wraps

import bcrypt
from flask import (
    Blueprint,
    current_app,
    flash,
    redirect,
    render_template,
    request,
    session,
    url_for,
)

from .db import get_user_by_username

bp = Blueprint("auth", __name__)


def _verify_password(plaintext: str, password_hash: str) -> bool:
    """Constant-time bcrypt password verification."""
    try:
        return bcrypt.checkpw(
            plaintext.encode("utf-8"), password_hash.encode("utf-8")
        )
    except (ValueError, TypeError):
        return False


def login_required(api: bool = False):
    """Decorator: require a valid session.

    - For page routes (``api=False``): redirect to ``/login`` with a 302
      and never leak business data.
    - For API routes (``api=True``): return HTTP 401 with a JSON body.
    """

    def decorator(view):
        @wraps(view)
        def wrapped(*args, **kwargs):
            if "user_id" not in session:
                if api:
                    return {"error": "unauthorized"}, 401
                return redirect(url_for("auth.login"))
            return view(*args, **kwargs)

        return wrapped

    return decorator


def role_required(role: str, api: bool = False):
    """Decorator: require a valid session whose role equals ``role``.

    Returns 403 (or redirect-friendly 403 page) when the session role
    does not match. Always checks login first.
    """

    def decorator(view):
        @wraps(view)
        def wrapped(*args, **kwargs):
            if "user_id" not in session:
                if api:
                    return {"error": "unauthorized"}, 401
                return redirect(url_for("auth.login"))
            if session.get("role") != role:
                return {"error": "forbidden"}, 403
            return view(*args, **kwargs)

        return wrapped

    return decorator


@bp.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        username = request.form.get("username", "").strip()
        password = request.form.get("password", "")

        user = get_user_by_username(username)
        if user and _verify_password(password, user["password_hash"]):
            session.clear()
            session["user_id"] = user["id"]
            session["role"] = user["role"]
            session["class_id"] = user["class_id"]
            return redirect(url_for("materials.list_materials_view"))

        # Generic failure message — do not reveal whether the user exists.
        flash("用户名或密码错误", "error")
        return render_template("login.html"), 401

    return render_template("login.html")


@bp.route("/logout", methods=["GET", "POST"])
def logout():
    session.clear()
    return redirect(url_for("auth.login"))
