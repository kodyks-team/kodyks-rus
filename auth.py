import sqlite3

from flask import render_template, request, redirect, url_for, flash, session
from werkzeug.security import generate_password_hash, check_password_hash

from db import get_db
from utils import check_csrf


DUMMY_HASH = generate_password_hash("dummy-password-for-timing")


def register_routes(app):
    """Обработка форм."""
    
    @app.route("/register", methods=["GET", "POST"])
    def register():
        """Обработка формы создания пользователя."""

        if g_user():
            return redirect(url_for("index"))
        if request.method == "POST":
            check_csrf()
            username = request.form.get("username", "").strip()
            password = request.form.get("password", "")
            password2 = request.form.get("password2", "")
            errors = []
            if not (3 <= len(username) <= 24):
                errors.append("Имя: от 3 до 24 символов")
            if not all(c.isalnum() or c in "_-" for c in username):
                errors.append("Имя: только буквы, цифры, _ и -")
            if len(password) < 6:
                errors.append("Пароль: минимум 6 символов")
            if password != password2:
                errors.append("Пароли не совпадают")
            if errors:
                for e in errors:
                    flash(e, "error")
                return render_template("register.html", username=username), 400
            db = get_db()
            try:
                db.execute(
                    "INSERT INTO users (username, password_hash) VALUES (?,?)",
                    (username, generate_password_hash(password)),
                )
                db.commit()
            except sqlite3.IntegrityError:
                flash("Такое имя уже занято", "error")
                return render_template("register.html", username=username), 400
            row = db.execute("SELECT * FROM users WHERE username=?", (username,)).fetchone()
            session.clear()
            session["user_id"] = row["id"]
            session.permanent = True
            flash("Добро пожаловать!", "ok")
            return redirect(url_for("index"))
        return render_template("register.html")

    @app.route("/login", methods=["GET", "POST"])
    def login():
        """Обработка формы входа пользователя / админа."""

        if g_user():
            return redirect(url_for("index"))
        if request.method == "POST":
            check_csrf()
            username = request.form.get("username", "").strip()
            password = request.form.get("password", "")
            db = get_db()
            row = db.execute("SELECT * FROM users WHERE username=?", (username,)).fetchone()
            hash_to_check = row["password_hash"] if row else DUMMY_HASH
            ok = check_password_hash(hash_to_check, password) and row is not None
            if not ok:
                flash("Неверное имя или пароль", "error")
                return render_template("login.html", username=username), 401
            session.clear()
            session["user_id"] = row["id"]
            session.permanent = True
            return redirect(request.args.get("next") or url_for("index"))
        return render_template("login.html")

    @app.route("/logout", methods=["POST"])
    def logout():
        """Обработка выхода из аккаунта."""

        check_csrf()
        session.clear()
        return redirect(url_for("index"))


def g_user():
    """Возвращает текущего авторизованного пользователя (или None)."""

    from flask import g
    return g.user