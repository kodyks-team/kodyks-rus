import json

from flask import (
    render_template, request, redirect, url_for, flash, g, abort,
)

from config import ROUNDS_OPTIONS
from db import get_db
from utils import check_csrf, login_required
from words import new_game_state, norm


def register_routes(app):
    @app.route("/play")
    @login_required
    def play():
        db = get_db()
        row = db.execute(
            "SELECT state FROM active_games WHERE user_id=?", (g.user["id"],)
        ).fetchone()
        has_active = False
        if row:
            try:
                st = json.loads(row["state"])
                if not st.get("finished") and st.get("current", 0) < len(st.get("rounds", [])):
                    has_active = True
            except Exception:
                pass
        return render_template("play.html", has_active=has_active)

    @app.route("/game")
    @login_required
    def game():
        db = get_db()
        row = db.execute(
            "SELECT state FROM active_games WHERE user_id=?", (g.user["id"],)
        ).fetchone()
        if row is None:
            return redirect(url_for("play"))

        state = json.loads(row["state"])
        if state.get("finished"):
            return redirect(url_for("game_result"))

        original_current = state["current"]
        phase = None

        # Продвигаемся вперёд, пропуская уже завершённые раунды.
        # Без этого после завершения раунда приходилось бы делать лишний редирект.
        while state["current"] < len(state["rounds"]):
            rnd = state["rounds"][state["current"]]
            if rnd["guessed"] is None:
                phase = "guess"
                break
            if rnd["shown_is_correct"] is False and not rnd["fix_attempted"]:
                phase = "fix"
                break
            state["current"] += 1
        else:
            # Все раунды пройдены — завершаем игру.
            _finish_game(db, state, g.user["id"])
            db.execute(
                "UPDATE active_games SET state=?, updated_at=CURRENT_TIMESTAMP WHERE user_id=?",
                (json.dumps(state, ensure_ascii=False), g.user["id"]),
            )
            db.commit()
            return redirect(url_for("game_result"))

        # Если продвинулись вперёд — фиксируем в БД, чтобы POST-обработчики
        # не пытались обновить уже завершённый раунд.
        if state["current"] != original_current:
            db.execute(
                "UPDATE active_games SET state=?, updated_at=CURRENT_TIMESTAMP WHERE user_id=?",
                (json.dumps(state, ensure_ascii=False), g.user["id"]),
            )
            db.commit()

        rnd = state["rounds"][state["current"]]
        return render_template(
            "game.html",
            rnd=rnd,
            phase=phase,
            index=state["current"] + 1,
            total=len(state["rounds"]),
            score=state["score"],
        )

    @app.route("/game/guess", methods=["POST"])
    @login_required
    def game_guess():
        check_csrf()
        db = get_db()
        row = db.execute(
            "SELECT state FROM active_games WHERE user_id=?", (g.user["id"],)
        ).fetchone()
        if row is None:
            return redirect(url_for("play"))
        state = json.loads(row["state"])
        if state.get("finished") or state["current"] >= len(state["rounds"]):
            return redirect(url_for("game"))
        rnd = state["rounds"][state["current"]]
        if rnd["guessed"] is not None:
            return redirect(url_for("game"))

        choice = request.form.get("choice", "")
        if choice not in ("correct", "wrong"):
            abort(400)

        player_thinks_correct = (choice == "correct")
        actually_correct = rnd["shown_is_correct"]

        if player_thinks_correct == actually_correct:
            rnd["guessed"] = True
            rnd["actions_correct"] += 1
            state["score"] += 1
            flash({"kind": "guess_ok"}, "answer")
        else:
            rnd["guessed"] = False
            rnd["actions_wrong"] += 1
            flash({"kind": "guess_fail"}, "answer")

        db.execute(
            "UPDATE active_games SET state=?, updated_at=CURRENT_TIMESTAMP WHERE user_id=?",
            (json.dumps(state, ensure_ascii=False), g.user["id"]),
        )
        db.commit()
        return redirect(url_for("game"))

    @app.route("/game/fix", methods=["POST"])
    @login_required
    def game_fix():
        check_csrf()
        db = get_db()
        row = db.execute(
            "SELECT state FROM active_games WHERE user_id=?", (g.user["id"],)
        ).fetchone()
        if row is None:
            return redirect(url_for("play"))
        state = json.loads(row["state"])
        if state.get("finished") or state["current"] >= len(state["rounds"]):
            return redirect(url_for("game"))
        rnd = state["rounds"][state["current"]]

        if rnd["shown_is_correct"] or rnd["guessed"] is None or rnd["fix_attempted"]:
            return redirect(url_for("game"))

        answer = norm(request.form.get("answer", ""))
        if not answer:
            flash("Введите слово", "error")
            return redirect(url_for("game"))

        correct_norm = norm(rnd["correct"])
        ok = (answer == correct_norm)

        rnd["fix_attempted"] = True
        rnd["fix_ok"] = ok
        if ok:
            rnd["actions_correct"] += 1
            state["score"] += 1
            flash({"kind": "fix_ok", "correct": rnd["correct"]}, "answer")
        else:
            rnd["actions_wrong"] += 1
            flash({"kind": "fix_fail", "correct": rnd["correct"]}, "answer")

        db.execute(
            "UPDATE active_games SET state=?, updated_at=CURRENT_TIMESTAMP WHERE user_id=?",
            (json.dumps(state, ensure_ascii=False), g.user["id"]),
        )
        db.commit()
        return redirect(url_for("game"))

    @app.route("/game/new", methods=["POST"])
    @login_required
    def game_new():
        check_csrf()
        db = get_db()

        num_rounds = request.form.get("num_rounds", 10, type=int)
        if num_rounds not in ROUNDS_OPTIONS:
            num_rounds = 10

        row = db.execute(
            "SELECT state FROM active_games WHERE user_id=?", (g.user["id"],)
        ).fetchone()
        prev_state = json.loads(row["state"]) if row else None

        state = new_game_state(prev_state, num_rounds)

        # INSERT ... ON CONFLICT: создаёт строку, если её нет, и обновляет, если есть.
        db.execute(
            "INSERT INTO active_games (user_id, state, updated_at) "
            "VALUES (?, ?, CURRENT_TIMESTAMP) "
            "ON CONFLICT(user_id) DO UPDATE SET "
            "state = excluded.state, updated_at = CURRENT_TIMESTAMP",
            (g.user["id"], json.dumps(state, ensure_ascii=False)),
        )
        db.commit()
        return redirect(url_for("game"))

    @app.route("/game/result")
    @login_required
    def game_result():
        """Показывает результат последней завершённой игры.

        Данные берутся из таблицы games (а не из active_games), поэтому
        результат не ломается, если игрок уже запустил новую партию.
        """
        db = get_db()
        game = db.execute(
            "SELECT * FROM games WHERE user_id=? ORDER BY id DESC LIMIT 1",
            (g.user["id"],),
        ).fetchone()

        errors = []
        max_score = 0
        if game is not None:
            keys = game.keys()
            if "errors" in keys and game["errors"]:
                try:
                    errors = json.loads(game["errors"])
                except (ValueError, TypeError):
                    errors = []
            if "max_score" in keys and game["max_score"]:
                max_score = game["max_score"]
            elif "rounds" in keys:
                max_score = game["rounds"]

        return render_template(
            "result.html", game=game, errors=errors, max_score=max_score
        )


def _finish_game(db, state, user_id):
    """Считает итоги игры и пишет их в БД (games и users).

    Мутирует state: выставляет finished, max_score и errors.
    Ошибки сохраняются прямо в games.errors как JSON, чтобы страница
    результата не зависела от active_games (которую перезаписывает новая игра).
    """
    total_correct = sum(r["actions_correct"] for r in state["rounds"])
    total_wrong = sum(r["actions_wrong"] for r in state["rounds"])
    max_possible = sum(1 if r["shown_is_correct"] else 2 for r in state["rounds"])

    state["finished"] = True
    state["max_score"] = max_possible

    errors = []
    for r in state["rounds"]:
        if r["actions_wrong"] > 0:
            errors.append({
                "shown": r["shown"],
                "correct": r["correct"],
                "shown_is_correct": r["shown_is_correct"],
                "guess_wrong": r["guessed"] is False,
                "fix_wrong": r["fix_ok"] is False,
            })
    state["errors"] = errors

    db.execute(
        "INSERT INTO games (user_id, score, rounds, correct, wrong, max_score, errors) "
        "VALUES (?,?,?,?,?,?,?)",
        (user_id, state["score"], len(state["rounds"]),
         total_correct, total_wrong, max_possible,
         json.dumps(errors, ensure_ascii=False)),
    )
    db.execute(
        """UPDATE users SET
             best_score = MAX(best_score, ?),
             correct_total = correct_total + ?,
             wrong_total = wrong_total + ?
           WHERE id = ?""",
        (state["score"], total_correct, total_wrong, user_id),
    )