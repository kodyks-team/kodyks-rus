import secrets
from functools import wraps

from flask import session, request, abort, g, redirect, url_for

from config import (
    THEME_COOKIE,
    ROUNDS_OPTIONS,
    APP_NAME,
    APP_BRAND_HTML,
    APP_FOOTER,
    MODULE_SPELLING_NAME,
    MODULE_SPELLING_DESC,
)
from db import get_db
from security import load_session_user, validate_request_origin


def csrf_token():
    """Возвращает CSRF-токен текущей сессии, создавая его при необходимости."""
    token = session.get("_csrf")

    if not isinstance(token, str) or len(token) < 32:
        token = secrets.token_hex(32)
        session["_csrf"] = token

    return token


def check_csrf():
    """Проверяет CSRF-токен и источник небезопасного HTTP-запроса."""
    validate_request_origin()

    token = request.form.get("_csrf", "")
    expected = session.get("_csrf", "")

    if (
        not isinstance(token, str)
        or not isinstance(expected, str)
        or not token
        or not secrets.compare_digest(token, expected)
    ):
        abort(400, "CSRF token missing or invalid")


def current_theme():
    """Возвращает безопасное значение темы из пользовательской cookie."""
    value = request.cookies.get(THEME_COOKIE, "dark")
    return value if value in ("dark", "light") else "dark"


def inject_theme():
    """Добавляет общие настройки приложения в контекст шаблонов."""
    return {
        "theme": current_theme(),
        "rounds_options": ROUNDS_OPTIONS,
        "app_name": APP_NAME,
        "app_brand_html": APP_BRAND_HTML,
        "app_footer": APP_FOOTER,
        "module_spelling_name": MODULE_SPELLING_NAME,
        "module_spelling_desc": MODULE_SPELLING_DESC,
    }


def load_user():
    """Проверяет серверную сессию перед каждым запросом."""
    g.user = load_session_user()


def login_required(f):
    """Перенаправляет неавторизованного пользователя на страницу входа."""
    @wraps(f)
    def wrapper(*args, **kwargs):
        if not g.user:
            return redirect(url_for("login", next=request.path))

        return f(*args, **kwargs)

    return wrapper


def assert_current_session(db):
    """Повторно проверяет сессию внутри транзакции перед изменением данных."""
    fresh_user = load_session_user()

    if (
        fresh_user is None
        or not g.user
        or fresh_user["id"] != g.user["id"]
    ):
        db.rollback()
        abort(403)

    g.user = fresh_user


def admin_required(f):
    """Разрешает выполнение маршрута только актуальному администратору."""
    @wraps(f)
    def wrapper(*args, **kwargs):
        fresh_user = load_session_user()

        if fresh_user is None or not fresh_user["is_admin"]:
            g.user = None
            abort(403)

        g.user = fresh_user
        return f(*args, **kwargs)

    return wrapper


def owner_required(f):
    """Разрешает выполнение маршрута только владельцу проекта."""
    @wraps(f)
    def wrapper(*args, **kwargs):
        fresh_user = load_session_user()

        if fresh_user is None or fresh_user["role"] != "owner":
            g.user = None
            abort(403)

        g.user = fresh_user
        return f(*args, **kwargs)

    return wrapper