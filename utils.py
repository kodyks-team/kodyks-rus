import secrets
from functools import wraps

from flask import session, request, abort, g, redirect, url_for

from config import (
    THEME_COOKIE, ROUNDS_OPTIONS,
    APP_NAME, APP_BRAND_HTML, APP_FOOTER,
    MODULE_SPELLING_NAME, MODULE_SPELLING_DESC
)
from db import get_db


def csrf_token():
    """Проверяет наличие токена, если его нет, то создаёт и сохраняет его."""
    if "_csrf" not in session:
        session["_csrf"] = secrets.token_hex(16)
    return session["_csrf"]


def check_csrf():
    """Проверяет CSRF-токен из формы. Прерывает запрос с 400, если токен неверный."""
    token = request.form.get("_csrf", "")
    if not token or not secrets.compare_digest(token, session.get("_csrf", "")):
        abort(400, "CSRF token missing or invalid")


def current_theme():
    """Возвращает текущую тему из cookie ('dark' или 'light'). 
    Если cookie нет или значение некорректно, возвращает 'dark'."""
    t = request.cookies.get(THEME_COOKIE, "dark")
    return t if t in ("dark", "light") else "dark"


def inject_theme():
    """Добавляет переменные в контекст всех шаблонов."""
    return {
        "theme": current_theme(),
        "rounds_options": ROUNDS_OPTIONS,
        # Брендинг
        "app_name": APP_NAME,
        "app_brand_html": APP_BRAND_HTML,
        "app_footer": APP_FOOTER,
        # Модули
        "module_spelling_name": MODULE_SPELLING_NAME,
        "module_spelling_desc": MODULE_SPELLING_DESC,
    }


def load_user():
    """Загружает данные текущего пользователя из БД в g.user.
    Вызывается перед каждым запросом (через before_request)."""
    g.user = None
    uid = session.get("user_id")
    if uid:
        row = get_db().execute("SELECT * FROM users WHERE id=?", (uid,)).fetchone()
        if row is None:
            session.clear()
        else:
            g.user = row


def login_required(f):
    """Декоратор: проверяет авторизацию пользователя.
    Если вход не выполнен — перенаправляет на страницу логина."""
    @wraps(f)
    def wrapper(*a, **kw):
        if not g.user:
            return redirect(url_for("login", next=request.path))
        return f(*a, **kw)
    return wrapper


def admin_required(f):
    """Декоратор: проверяет, что вошёл администратор.
    Если нет прав — возвращает ошибку 403."""
    @wraps(f)
    def wrapper(*a, **kw):
        if not g.user or not g.user["is_admin"]:
            abort(403)
        return f(*a, **kw)
    return wrapper