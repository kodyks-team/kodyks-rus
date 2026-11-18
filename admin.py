from flask import render_template, request, redirect, url_for, flash, g
from werkzeug.security import generate_password_hash

from db import get_db
from utils import check_csrf, admin_required


def register_routes(app):
    @app.route("/admin")
    @admin_required
    def admin():
        db = get_db()
        users = db.execute("SELECT * FROM users ORDER BY id").fetchall()
        games_count = db.execute("SELECT COUNT(*) FROM games").fetchone()[0]
        return render_template("admin.html", users=users, games_count=games_count)

    @app.route("/admin/user/<int:uid>/delete", methods=["POST"])
    @admin_required
    def admin_delete_user(uid):
        check_csrf()
        if uid == g.user["id"]:
            flash("Нельзя удалить себя", "error")
            return redirect(url_for("admin"))
        db = get_db()
        db.execute("DELETE FROM users WHERE id=?", (uid,))
        db.commit()
        flash("Пользователь удалён", "ok")
        return redirect(url_for("admin"))

    @app.route("/admin/user/<int:uid>/toggle_admin", methods=["POST"])
    @admin_required
    def admin_toggle_admin(uid):
        check_csrf()
        if uid == g.user["id"]:
            flash("Нельзя изменить свою роль", "error")
            return redirect(url_for("admin"))
        db = get_db()
        db.execute("UPDATE users SET is_admin = 1 - is_admin WHERE id=?", (uid,))
        db.commit()
        return redirect(url_for("admin"))

    @app.route("/admin/user/<int:uid>/reset_stats", methods=["POST"])
    @admin_required
    def admin_reset_stats(uid):
        check_csrf()
        db = get_db()
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
        check_csrf()
        new_password = request.form.get("new_password", "")
        if len(new_password) < 6:
            flash("Пароль: минимум 6 символов", "error")
            return redirect(url_for("admin"))
        db = get_db()
        db.execute(
            "UPDATE users SET password_hash=? WHERE id=?",
            (generate_password_hash(new_password), uid),
        )
        db.commit()
        flash("Пароль обновлён", "ok")
        return redirect(url_for("admin"))