import sqlite3

from flask import (
    abort,
    current_app,
    flash,
    g,
    redirect,
    render_template,
    request,
    url_for,
)
from werkzeug.security import generate_password_hash

from config import PASSWORD_MIN_LENGTH, PASSWORD_MAX_LENGTH
from security import load_session_user, revoke_user_sessions
from admin_console import execute_admin_command
from db import get_db
from utils import admin_required, owner_required, check_csrf


def _assert_admin_in_transaction(db):
    """Повторно проверяет роль и серверную сессию после блокировки БД."""
    fresh_user = load_session_user()

    if fresh_user is None or not fresh_user["is_admin"]:
        db.rollback()
        abort(403)

    g.user = fresh_user


def _assert_owner_in_transaction(db):
    """Повторно проверяет право владельца после блокировки БД."""
    fresh_user = load_session_user()

    if fresh_user is None or fresh_user["role"] != "owner":
        db.rollback()
        abort(403)

    g.user = fresh_user


def register_routes(app):
    @app.route("/admin")
    @admin_required
    def admin():
        """Показывает панель управления и список пользователей."""
        db = get_db()

        users = db.execute(
            """
            SELECT
                u.id,
                u.username,
                u.is_admin,
                u.role,
                u.best_score,
                u.correct_total,
                u.wrong_total,
                u.created_at,
                (
                    SELECT COUNT(*)
                    FROM games AS game
                    WHERE game.user_id=u.id
                ) AS games_count
            FROM users AS u
            ORDER BY
                CASE u.role
                    WHEN 'owner' THEN 0
                    WHEN 'admin' THEN 1
                    ELSE 2
                END,
                u.id
            """
        ).fetchall()

        games_count = db.execute(
            "SELECT COUNT(*) FROM games"
        ).fetchone()[0]

        console_result = None
        console_history = []

        # Обычные администраторы видят пользователей, но не консоль.
        if g.user["role"] == "owner":
            console_log_id = request.args.get("console_log", type=int)

            if console_log_id:
                console_result = db.execute(
                    """
                    SELECT
                        l.*,
                        COALESCE(
                            u.username,
                            'удалённый администратор'
                        ) AS admin_name
                    FROM admin_logs AS l
                    LEFT JOIN users AS u ON u.id=l.admin_id
                    WHERE l.id=?
                    """,
                    (console_log_id,),
                ).fetchone()

            console_history = db.execute(
                """
                SELECT
                    l.id,
                    l.command,
                    l.is_error,
                    l.created_at,
                    COALESCE(
                        u.username,
                        'удалённый администратор'
                    ) AS admin_name
                FROM admin_logs AS l
                LEFT JOIN users AS u ON u.id=l.admin_id
                ORDER BY l.id DESC
                LIMIT 8
                """
            ).fetchall()

        return render_template(
            "admin.html",
            users=users,
            games_count=games_count,
            console_result=console_result,
            console_history=console_history,
        )

    @app.route("/admin/console", methods=["POST"])
    @owner_required
    def admin_console():
        """Выполняет ограниченную команду владельца и сохраняет её в журнале."""
        check_csrf()

        command = request.form.get("command", "").strip()
        db = get_db()

        try:
            # Сериализуем административные операции.
            db.execute("BEGIN IMMEDIATE")
            _assert_owner_in_transaction(db)

            if len(command) > 200:
                output = "ОШИБКА: команда должна содержать не более 200 символов."
                is_error = True
            else:
                output, is_error = execute_admin_command(
                    db,
                    command,
                    g.user["id"],
                )

            db.execute(
                """
                INSERT INTO admin_logs (admin_id, command, output, is_error)
                VALUES (?, ?, ?, ?)
                """,
                (
                    g.user["id"],
                    command[:200],
                    output[:12000],
                    int(is_error),
                ),
            )

            log_id = db.execute(
                "SELECT last_insert_rowid()"
            ).fetchone()[0]

            db.commit()

        except sqlite3.Error:
            if db.in_transaction:
                db.rollback()

            current_app.logger.exception(
                "Ошибка административной консоли"
            )

            flash(
                "Не удалось выполнить команду. Подробности записаны в журнал сервера.",
                "error",
            )
            return redirect(url_for("admin"))

        return redirect(url_for("admin", console_log=log_id))

    @app.route("/admin/user/<int:uid>/delete", methods=["POST"])
    @admin_required
    def admin_delete_user(uid):
        """Удаляет пользователя с учётом его роли."""
        check_csrf()

        if uid == g.user["id"]:
            flash("Нельзя удалить собственный аккаунт.", "error")
            return redirect(url_for("admin"))

        db = get_db()
        db.execute("BEGIN IMMEDIATE")
        _assert_admin_in_transaction(db)

        target = db.execute(
            "SELECT id, role FROM users WHERE id=?",
            (uid,),
        ).fetchone()

        if target is None:
            db.rollback()
            flash("Пользователь не найден.", "error")
            return redirect(url_for("admin"))

        if target["role"] == "owner":
            db.rollback()
            flash("Аккаунт владельца нельзя удалить.", "error")
            return redirect(url_for("admin"))

        if target["role"] == "admin" and g.user["role"] != "owner":
            db.rollback()
            flash(
                "Администратор не может удалять другие административные аккаунты.",
                "error",
            )
            return redirect(url_for("admin"))

        if target["role"] == "admin":
            admins_count = db.execute(
                "SELECT COUNT(*) FROM users WHERE is_admin=1"
            ).fetchone()[0]

            if admins_count <= 1:
                db.rollback()
                flash("Нельзя удалить последнего администратора.", "error")
                return redirect(url_for("admin"))

        db.execute(
            "DELETE FROM users WHERE id=?",
            (uid,),
        )
        db.commit()

        flash("Пользователь удалён.", "ok")
        return redirect(url_for("admin"))

    @app.route("/admin/user/<int:uid>/toggle_admin", methods=["POST"])
    @owner_required
    def admin_toggle_admin(uid):
        """Назначает администратором или возвращает обычную роль."""
        check_csrf()

        desired_role = request.form.get("role", "").strip()

        if desired_role not in ("user", "admin"):
            flash("Недопустимая роль пользователя.", "error")
            return redirect(url_for("admin"))

        if uid == g.user["id"]:
            flash("Нельзя изменить собственную роль.", "error")
            return redirect(url_for("admin"))

        db = get_db()
        db.execute("BEGIN IMMEDIATE")
        _assert_owner_in_transaction(db)

        target = db.execute(
            "SELECT id, username, role FROM users WHERE id=?",
            (uid,),
        ).fetchone()

        if target is None:
            db.rollback()
            flash("Пользователь не найден.", "error")
            return redirect(url_for("admin"))

        if target["role"] == "owner":
            db.rollback()
            flash("Нельзя изменить роль владельца.", "error")
            return redirect(url_for("admin"))

        if target["role"] == desired_role:
            db.rollback()
            flash("У пользователя уже установлена эта роль.", "ok")
            return redirect(url_for("admin"))

        if target["role"] == "admin" and desired_role == "user":
            admins_count = db.execute(
                "SELECT COUNT(*) FROM users WHERE is_admin=1"
            ).fetchone()[0]

            if admins_count <= 1:
                db.rollback()
                flash(
                    "Нельзя снять права у последнего администратора.",
                    "error",
                )
                return redirect(url_for("admin"))

        db.execute(
            """
            UPDATE users
            SET role=?, is_admin=?
            WHERE id=?
            """,
            (
                desired_role,
                int(desired_role == "admin"),
                uid,
            ),
        )

        revoke_user_sessions(uid)
        db.commit()

        flash(
            f"Роль пользователя {target['username']} обновлена.",
            "ok",
        )
        return redirect(url_for("admin"))

    @app.route("/admin/user/<int:uid>/reset_stats", methods=["POST"])
    @admin_required
    def admin_reset_stats(uid):
        """Сбрасывает статистику и игровые данные пользователя."""
        check_csrf()

        db = get_db()
        db.execute("BEGIN IMMEDIATE")
        _assert_admin_in_transaction(db)

        target = db.execute(
            "SELECT id, role FROM users WHERE id=?",
            (uid,),
        ).fetchone()

        if target is None:
            db.rollback()
            flash("Пользователь не найден.", "error")
            return redirect(url_for("admin"))

        if (
            target["role"] in ("admin", "owner")
            and g.user["role"] != "owner"
        ):
            db.rollback()
            flash(
                "Администратор может изменять статистику только обычных пользователей.",
                "error",
            )
            return redirect(url_for("admin"))

        db.execute(
            """
            UPDATE users
            SET best_score=0, correct_total=0, wrong_total=0
            WHERE id=?
            """,
            (uid,),
        )

        db.execute(
            "DELETE FROM games WHERE user_id=?",
            (uid,),
        )
        db.execute(
            "DELETE FROM active_games WHERE user_id=?",
            (uid,),
        )

        db.commit()

        flash("Статистика сброшена.", "ok")
        return redirect(url_for("admin"))

    @app.route("/admin/user/<int:uid>/password", methods=["POST"])
    @admin_required
    def admin_set_password(uid):
        """Устанавливает новый пароль выбранному пользователю."""
        check_csrf()

        new_password = request.form.get("new_password", "")

        if not (
            PASSWORD_MIN_LENGTH <= len(new_password) <= PASSWORD_MAX_LENGTH
        ):
            flash(
                f"Пароль: от {PASSWORD_MIN_LENGTH} до {PASSWORD_MAX_LENGTH} символов.",
                "error",
            )
            return redirect(url_for("admin"))

        db = get_db()
        db.execute("BEGIN IMMEDIATE")
        _assert_admin_in_transaction(db)

        target = db.execute(
            "SELECT id, role FROM users WHERE id=?",
            (uid,),
        ).fetchone()

        if target is None:
            db.rollback()
            flash("Пользователь не найден.", "error")
            return redirect(url_for("admin"))

        if (
            target["role"] in ("admin", "owner")
            and g.user["role"] != "owner"
        ):
            db.rollback()
            flash(
                "Администратор может менять пароли только обычным пользователям.",
                "error",
            )
            return redirect(url_for("admin"))

        db.execute(
            "UPDATE users SET password_hash=? WHERE id=?",
            (generate_password_hash(new_password), uid),
        )

        revoke_user_sessions(uid)
        db.commit()

        flash(
            "Пароль обновлён. Все сессии пользователя завершены.",
            "ok",
        )
        return redirect(url_for("admin"))