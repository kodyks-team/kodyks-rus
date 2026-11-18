from flask import render_template, request, g
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from db import get_db
from utils import login_required


def register_routes(app):
    @app.route("/stats")
    @login_required
    def stats():
        """Страница со статистикой, 3 диаграммами и умным рейтингом."""

        db = get_db()
        
        period = request.args.get("period", "week")
        if period not in ("day", "week", "month", "year"):
            period = "week"

        user_tz = ZoneInfo(g.user["timezone"])
        now_local = datetime.now(user_tz)
        
        if period == "day":
            start_local = now_local - timedelta(days=1)
        elif period == "week":
            start_local = now_local - timedelta(days=7)
        elif period == "month":
            start_local = now_local - timedelta(days=30)
        else:  # year
            start_local = now_local - timedelta(days=365)
            
        start_utc = start_local.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")

        games = db.execute(
            "SELECT * FROM games WHERE user_id=? AND played_at >= ? ORDER BY played_at ASC",
            (g.user["id"], start_utc),
        ).fetchall()

        total_correct = sum(game["correct"] for game in games)
        total_wrong = sum(game["wrong"] for game in games)
        total_actions = total_correct + total_wrong
        kpi_pct = (total_correct / total_actions * 100) if total_actions else 0

        chart_labels = []
        chart_line_data = []
        chart_bar_correct = []
        chart_bar_wrong = []

        for game in games:  # <--- ИСПРАВЛЕНО ЗДЕСЬ
            # Переводим время игры в часовой пояс пользователя для подписи
            utc_dt = datetime.strptime(game["played_at"], "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)
            local_dt = utc_dt.astimezone(user_tz)

            if period == "day":
                chart_labels.append(local_dt.strftime("%H:%M"))
            elif period == "year":
                chart_labels.append(local_dt.strftime("%d.%m.%y"))
            else:
                chart_labels.append(local_dt.strftime("%d.%m %H:%M"))

            acts = game["correct"] + game["wrong"]
            pct = (game["correct"] / acts * 100) if acts else 0
            
            chart_line_data.append(round(pct, 1))
            chart_bar_correct.append(game["correct"])
            chart_bar_wrong.append(game["wrong"])

        # 6. Собираем умный рейтинг (Топ-20 или 7 выше + юзер + 7 ниже)
        all_users = db.execute("""
            SELECT id, username, best_score, correct_total, wrong_total 
            FROM users 
            ORDER BY best_score DESC, correct_total DESC, id ASC
        """).fetchall()
        
        user_index = next((i for i, u in enumerate(all_users) if u["id"] == g.user["id"]), -1)
        
        if user_index < 20:
            # Если пользователь в топ-20 — показываем весь топ-20
            leaderboard = all_users[:20]
            show_top_gap = False
            show_bottom_gap = len(all_users) > 20
        else:
            # Иначе показываем 7 выше, себя, 7 ниже
            start = max(0, user_index - 7)
            end = min(len(all_users), user_index + 8)
            leaderboard = all_users[start:end]
            show_top_gap = start > 0
            show_bottom_gap = end < len(all_users)

        # Данные для передачи в Chart.js
        chart_data = {
            "pie": {"correct": total_correct, "wrong": total_wrong},
            "line": {"labels": chart_labels, "data": chart_line_data},
            "bar": {"labels": chart_labels, "correct": chart_bar_correct, "wrong": chart_bar_wrong},
            "avg_kpi": round(kpi_pct, 1)
        }

        return render_template(
            "stats.html",
            period=period,
            chart_data=chart_data,
            leaderboard=leaderboard,
            user_index=user_index,
            show_top_gap=show_top_gap,
            show_bottom_gap=show_bottom_gap,
            total_games=len(games)
        )

    @app.route("/leaderboard")
    def leaderboard():
        """Полный топ-50 для отдельной страницы."""

        db = get_db()
        rows = db.execute(
            """SELECT username, best_score, correct_total, wrong_total
               FROM users
               ORDER BY best_score DESC, correct_total DESC, id ASC
               LIMIT 50"""
        ).fetchall()
        return render_template("leaderboard.html", rows=rows)