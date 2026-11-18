import secrets
from functools import wraps

from flask import session, request, abort, g, redirect, url_for

from config import THEME_COOKIE
from db import get_db


def csrf_token():
    if "_csrf" not in session:
        session["_csrf"] = secrets.token_hex(16)
    return session["_csrf"]


def check_csrf():
    token = request.form.get("_csrf", "")
    if not token or not secrets.compare_digest(token, session.get("_csrf", "")):
        abort(400, "CSRF token missing or invalid")


def current_theme():
    t = request.cookies.get(THEME_COOKIE, "dark")
    return t if t in ("dark", "light") else "dark"


def inject_theme():
    return {"theme": current_theme()}


def load_user():
    g.user = None
    uid = session.get("user_id")
    if uid:
        row = get_db().execute("SELECT * FROM users WHERE id=?", (uid,)).fetchone()
        if row is None:
            session.clear()
        else:
            g.user = row


def login_required(f):
    @wraps(f)
    def wrapper(*a, **kw):
        if not g.user:
            return redirect(url_for("login", next=request.path))
        return f(*a, **kw)
    return wrapper


def admin_required(f):
    @wraps(f)
    def wrapper(*a, **kw):
        if not g.user or not g.user["is_admin"]:
            abort(403)
        return f(*a, **kw)
    return wrapper