import sqlite3
import secrets
import unicodedata
from urllib.parse import unquote, urlsplit

from flask import abort, render_template, request, redirect, url_for, flash, session
from werkzeug.security import generate_password_hash, check_password_hash

from config import PASSWORD_MIN_LENGTH, PASSWORD_MAX_LENGTH, normalize_username
from db import get_db
from security import (
    create_user_session,
    login_is_limited,
    record_login_attempt,
    registration_is_limited,
    record_registration_attempt,
    revoke_current_session,
)
from utils import check_csrf


DUMMY_HASH = generate_password_hash("dummy-password-for-timing")


def _safe_next_url(value):
    """Разрешает только локальные URL, чтобы исключить open redirect."""
    if not isinstance(value, str) or not value.startswith("/"):
        return None
    decoded = value
    for _ in range(8):
        next_value = unquote(decoded)
        if next_value == decoded:
            break
        decoded = next_value
    else:
        return None
    if (
        not decoded.startswith("/")
        or decoded.startswith("//")
        or "\\" in decoded
        or any(ord(char) < 32 or ord(char) == 127 for char in decoded)
    ):
        return None
    parsed = urlsplit(decoded)
    if parsed.scheme or parsed.netloc or not parsed.path.startswith("/"):
        return None
    return value


def register_routes(app):
    """Регистрирует маршруты регистрации, входа и выхода."""

    @app.route("/register", methods=["GET", "POST"])
    def register():
        """Создаёт пользовательскую учётную запись."""
        if g_user():
            return redirect(url_for("index"))
        if request.method == "POST":
            check_csrf()
            db = get_db()
            db.execute("BEGIN IMMEDIATE")
            if registration_is_limited(db):
                db.rollback()
                abort(429)
            record_registration_attempt(db)
            username = unicodedata.normalize("NFKC", request.form.get("username", "")).strip()
            password = request.form.get("password", "")
            password2 = request.form.get("password2", "")
            errors = []
            if not (3 <= len(username) <= 24):
                errors.append("Имя: от 3 до 24 символов")
            if not all(char.isalnum() or char in "_-" for char in username):
                errors.append("Имя: только буквы, цифры, _ и -")
            if not PASSWORD_MIN_LENGTH <= len(password) <= PASSWORD_MAX_LENGTH:
                errors.append(
                    f"Пароль: от {PASSWORD_MIN_LENGTH} до {PASSWORD_MAX_LENGTH} символов"
                )
            if password != password2:
                errors.append("Пароли не совпадают")
            if errors:
                for error in errors:
                    flash(error, "error")
                return render_template("register.html", username=username), 400

            try:
                cursor = db.execute(
                    "INSERT INTO users (username, username_key, password_hash) VALUES (?, ?, ?)",
                    (username, normalize_username(username), generate_password_hash(password)),
                )
                create_user_session(cursor.lastrowid)
                db.commit()
            except sqlite3.IntegrityError:
                db.rollback()
                flash("Такое имя уже занято", "error")
                return render_template("register.html", username=username), 400
            flash("Добро пожаловать!", "ok")
            return redirect(url_for("index"))
        return render_template("register.html")

    @app.route("/login", methods=["GET", "POST"])
    def login():
        """Проверяет пароль с защитой от перебора и создаёт серверную сессию."""
        if g_user():
            return redirect(url_for("index"))
        if request.method == "POST":
            check_csrf()
            username = request.form.get("username", "").strip()[:128]
            password = request.form.get("password", "")
            db = get_db()

            # Блокировка записи делает проверку лимита и фиксацию попытки атомарными.
            db.execute("BEGIN IMMEDIATE")
            if login_is_limited(db, username):
                db.rollback()
                abort(429)
            record_login_attempt(db, username)

            row = db.execute(
                "SELECT id, username, password_hash FROM users WHERE username_key=?",
                (normalize_username(username),),
            ).fetchone()
            password_size_ok = isinstance(password, str) and len(password) <= PASSWORD_MAX_LENGTH
            hash_to_check = row["password_hash"] if row else DUMMY_HASH
            supplied_password = password if password_size_ok else ""
            try:
                password_ok = check_password_hash(hash_to_check, supplied_password)
            except (TypeError, ValueError):
                password_ok = False
            if not (password_ok and row is not None and password_size_ok):
                flash("Неверное имя или пароль", "error")
                return render_template("login.html", username=username), 401

            # Повторная проверка под write-lock закрывает гонку со сменой пароля.
            db.execute("BEGIN IMMEDIATE")
            current_account = db.execute(
                "SELECT password_hash FROM users WHERE id=?", (row["id"],)
            ).fetchone()
            current_hash = current_account["password_hash"] if current_account else None
            if (
                not isinstance(current_hash, str)
                or not secrets.compare_digest(current_hash, hash_to_check)
            ):
                db.rollback()
                flash("Неверное имя или пароль", "error")
                return render_template("login.html", username=username), 401

            create_user_session(row["id"])
            db.commit()
            target = _safe_next_url(request.args.get("next"))
            return redirect(target or url_for("index"))
        return render_template("login.html")

    @app.route("/logout", methods=["POST"])
    def logout():
        """Отзывает серверную сессию и очищает cookie."""
        check_csrf()
        revoke_current_session()
        session.clear()
        return redirect(url_for("index"))


def g_user():
    """Возвращает текущего авторизованного пользователя (или None)."""
    from flask import g
    return g.user
