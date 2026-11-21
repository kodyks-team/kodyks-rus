import sqlite3

from flask import (
    current_app, flash, g, redirect, render_template, request, url_for,
)
from werkzeug.security import generate_password_hash

from config import PASSWORD_MIN_LENGTH, PASSWORD_MAX_LENGTH
from security import load_session_user, revoke_user_sessions

from admin_console import execute_admin_command
from db import get_db
from utils import admin_required, check_csrf


def _assert_admin_in_transaction(db):
    """Повторно проверяет роль и серверную сессию после блокировки БД."""
    fresh_user = load_session_user()
    if fresh_user is None or not fresh_user["is_admin"]:
        db.rollback()
        from flask import abort
        abort(403)
    g.user = fresh_user


def register_routes(app):
    @app.route("/admin")
    @admin_required
    def admin():
        """Показывает панель управления, консоль и список пользователей."""
        db = get_db()
        users = db.execute(
            """SELECT id, username, is_admin, best_score, correct_total, wrong_total, created_at
               FROM users ORDER BY id"""
        ).fetchall()
        games_count = db.execute("SELECT COUNT(*) FROM games").fetchone()[0]
        console_log_id = request.args.get("console_log", type=int)
        console_result = None
        if console_log_id:
            console_result = db.execute(
                """SELECT l.*, COALESCE(u.username, 'удалённый администратор') AS admin_name
                   FROM admin_logs AS l
                   LEFT JOIN users AS u ON u.id=l.admin_id
                   WHERE l.id=?""",
                (console_log_id,),
            ).fetchone()
        console_history = db.execute(
            """SELECT l.id, l.command, l.is_error, l.created_at,
                      COALESCE(u.username, 'удалённый администратор') AS admin_name
               FROM admin_logs AS l
               LEFT JOIN users AS u ON u.id=l.admin_id
               ORDER BY l.id DESC
               LIMIT 8"""
        ).fetchall()
        return render_template(
            "admin.html",
            users=users,
            games_count=games_count,
            console_result=console_result,
            console_history=console_history,
        )

    @app.route("/admin/console", methods=["POST"])
    @admin_required
    def admin_console():
        """Выполняет ограниченную команду и сохраняет её в журнале."""
        check_csrf()
        command = request.form.get("command", "").strip()
        db = get_db()

        try:
            # Сериализуем админские операции, чтобы одновременно не снять права
            # у нескольких последних администраторов.
            db.execute("BEGIN IMMEDIATE")
            _assert_admin_in_transaction(db)
            if len(command) > 200:
                output, is_error = "ОШИБКА: команда должна содержать не более 200 символов.", True
            else:
                output, is_error = execute_admin_command(db, command, g.user["id"])
            db.execute(
                """INSERT INTO admin_logs (admin_id, command, output, is_error)
                   VALUES (?, ?, ?, ?)""",
                (g.user["id"], command[:200], output[:12000], int(is_error)),
            )
            log_id = db.execute("SELECT last_insert_rowid()").fetchone()[0]
            db.commit()
        except sqlite3.Error:
            if db.in_transaction:
                db.rollback()
            current_app.logger.exception("Ошибка административной консоли")
            flash("Не удалось выполнить команду. Подробности записаны в журнал сервера.", "error")
            return redirect(url_for("admin"))

        return redirect(url_for("admin", console_log=log_id))

    @app.route("/admin/user/<int:uid>/delete", methods=["POST"])
    @admin_required
    def admin_delete_user(uid):
        """Удаляет пользователя, сохраняя хотя бы одного администратора."""
        check_csrf()
        if uid == g.user["id"]:
            flash("Нельзя удалить себя", "error")
            return redirect(url_for("admin"))
        db = get_db()
        db.execute("BEGIN IMMEDIATE")
        _assert_admin_in_transaction(db)
        target = db.execute("SELECT id, is_admin FROM users WHERE id=?", (uid,)).fetchone()
        if target is None:
            db.rollback()
            flash("Пользователь не найден", "error")
            return redirect(url_for("admin"))
        if target["is_admin"] and db.execute(
            "SELECT COUNT(*) FROM users WHERE is_admin=1"
        ).fetchone()[0] <= 1:
            db.rollback()
            flash("Нельзя удалить последнего администратора", "error")
            return redirect(url_for("admin"))
        db.execute("DELETE FROM users WHERE id=?", (uid,))
        db.commit()
        flash("Пользователь удалён", "ok")
        return redirect(url_for("admin"))

    @app.route("/admin/user/<int:uid>/toggle_admin", methods=["POST"])
    @admin_required
    def admin_toggle_admin(uid):
        """Переключает права администратора у выбранного пользователя."""
        check_csrf()
        if uid == g.user["id"]:
            flash("Нельзя изменить свою роль", "error")
            return redirect(url_for("admin"))
        db = get_db()
        db.execute("BEGIN IMMEDIATE")
        _assert_admin_in_transaction(db)
        target = db.execute("SELECT id, is_admin FROM users WHERE id=?", (uid,)).fetchone()
        if target is None:
            db.rollback()
            flash("Пользователь не найден", "error")
            return redirect(url_for("admin"))
        if target["is_admin"] and db.execute(
            "SELECT COUNT(*) FROM users WHERE is_admin=1"
        ).fetchone()[0] <= 1:
            db.rollback()
            flash("Нельзя снять права у последнего администратора", "error")
            return redirect(url_for("admin"))
        db.execute("UPDATE users SET is_admin = 1 - is_admin WHERE id=?", (uid,))
        revoke_user_sessions(uid)
        db.commit()
        flash("Роль пользователя обновлена", "ok")
        return redirect(url_for("admin"))

    @app.route("/admin/user/<int:uid>/reset_stats", methods=["POST"])
    @admin_required
    def admin_reset_stats(uid):
        """Сбрасывает статистику и игровые данные пользователя."""
        check_csrf()
        db = get_db()
        db.execute("BEGIN IMMEDIATE")
        _assert_admin_in_transaction(db)
        target = db.execute("SELECT id FROM users WHERE id=?", (uid,)).fetchone()
        if target is None:
            db.rollback()
            flash("Пользователь не найден", "error")
            return redirect(url_for("admin"))
        db.execute(
            "UPDATE users SET best_score=0, correct_total=0, wrong_total=0 WHERE id=?",
            (uid,),
        )
        db.execute("DELETE FROM games WHERE user_id=?", (uid,))
        db.execute("DELETE FROM active_games WHERE user_id=?", (uid,))
        db.commit()
        flash("Статистика сброшена", "ok")
        return redirect(url_for("admin"))

    @app.route("/admin/user/<int:uid>/password", methods=["POST"])
    @admin_required
    def admin_set_password(uid):
        """Устанавливает новый пароль выбранному пользователю."""
        check_csrf()
        new_password = request.form.get("new_password", "")
        if not PASSWORD_MIN_LENGTH <= len(new_password) <= PASSWORD_MAX_LENGTH:
            flash(
                f"Пароль: от {PASSWORD_MIN_LENGTH} до {PASSWORD_MAX_LENGTH} символов",
                "error",
            )
            return redirect(url_for("admin"))
        db = get_db()
        db.execute("BEGIN IMMEDIATE")
        _assert_admin_in_transaction(db)
        cursor = db.execute(
            "UPDATE users SET password_hash=? WHERE id=?",
            (generate_password_hash(new_password), uid),
        )
        if not cursor.rowcount:
            db.rollback()
            flash("Пользователь не найден", "error")
            return redirect(url_for("admin"))
        revoke_user_sessions(uid)
        db.commit()
        flash("Пароль обновлён. Все сессии пользователя завершены.", "ok")
        return redirect(url_for("admin"))
