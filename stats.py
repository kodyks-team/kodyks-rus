from flask import render_template, g

from db import get_db
from utils import login_required


def register_routes(app):
    @app.route("/stats")
    @login_required
    def stats():
        db = get_db()
        games = db.execute(
            "SELECT * FROM games WHERE user_id=? ORDER BY id DESC LIMIT 20",
            (g.user["id"],),
        ).fetchall()
        total = g.user["correct_total"] + g.user["wrong_total"]
        good_pct = (g.user["correct_total"] / total * 100) if total else 0
        return render_template("stats.html", games=games, total=total, good_pct=good_pct)

    @app.route("/leaderboard")
    def leaderboard():
        db = get_db()
        rows = db.execute(
            """SELECT username, best_score, correct_total, wrong_total
               FROM users
               ORDER BY best_score DESC, correct_total DESC, id ASC
               LIMIT 50"""
        ).fetchall()
        return render_template("leaderboard.html", rows=rows)