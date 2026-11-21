import json

from flask import abort, render_template, request, redirect, url_for, flash, g

from config import ROUNDS_OPTIONS
from db import get_db
from utils import assert_current_session, check_csrf, login_required
from words import new_game_state, norm


def register_routes(app):
    @app.route("/play")
    @login_required
    def play():
        """Показывает выбор количества раундов и наличие активной игры."""
        db = get_db()
        row = db.execute(
            "SELECT state FROM active_games WHERE user_id=?", (g.user["id"],)
        ).fetchone()
        has_active = False
        if row:
            try:
                state = json.loads(row["state"])
                has_active = (
                    not state.get("finished")
                    and state.get("current", 0) < len(state.get("rounds", []))
                )
            except (TypeError, ValueError, AttributeError):
                has_active = False
        return render_template("play.html", has_active=has_active)

    @app.route("/game")
    @login_required
    def game():
        """Показывает текущее состояние игры без изменения данных на GET."""
        db = get_db()
        row = db.execute(
            "SELECT state FROM active_games WHERE user_id=?", (g.user["id"],)
        ).fetchone()
        if row is None:
            return redirect(url_for("play"))

        try:
            state = json.loads(row["state"])
            rounds = state["rounds"]
            current = state["current"]
            if not isinstance(rounds, list) or isinstance(current, bool) or not isinstance(current, int):
                raise ValueError("Некорректное состояние игры")
        except (TypeError, ValueError, KeyError):
            abort(400)

        if state.get("finished"):
            return redirect(url_for("game_result"))

        total = len(rounds)
        if current < 0:
            abort(400)
        if current >= total:
            # Поддержка состояния, сохранённого старой версией приложения:
            # завершение произойдёт только отдельным CSRF-защищённым POST.
            phase = "complete"
            rnd = None
            index = total
        else:
            rnd = rounds[current]
            index = current + 1
            if rnd.get("guessed") is None:
                phase = "guess"
            elif rnd.get("shown_is_correct") is False and not rnd.get("fix_attempted"):
                phase = "fix"
            else:
                phase = "advance"

        return render_template(
            "game.html",
            rnd=rnd,
            phase=phase,
            index=index,
            total=total,
            score=state.get("score", 0),
        )

    @app.route("/game/guess", methods=["POST"])
    @login_required
    def game_guess():
        """Сохраняет ответ игрока, не продвигая раунд через GET."""
        check_csrf()
        db = get_db()
        db.execute("BEGIN IMMEDIATE")
        assert_current_session(db)
        row = db.execute(
            "SELECT state FROM active_games WHERE user_id=?", (g.user["id"],)
        ).fetchone()
        if row is None:
            db.rollback()
            return redirect(url_for("play"))
        try:
            state = json.loads(row["state"])
            current = state["current"]
            rnd = state["rounds"][current]
        except (TypeError, ValueError, KeyError, IndexError):
            db.rollback()
            abort(400)
        if state.get("finished") or rnd.get("guessed") is not None:
            db.rollback()
            return redirect(url_for("game"))

        choice = request.form.get("choice", "")
        if choice not in ("correct", "wrong"):
            db.rollback()
            abort(400)

        player_thinks_correct = choice == "correct"
        actually_correct = bool(rnd["shown_is_correct"])
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
        """Проверяет исправление слова и защищает от повторной отправки."""
        check_csrf()
        db = get_db()
        db.execute("BEGIN IMMEDIATE")
        assert_current_session(db)
        row = db.execute(
            "SELECT state FROM active_games WHERE user_id=?", (g.user["id"],)
        ).fetchone()
        if row is None:
            db.rollback()
            return redirect(url_for("play"))
        try:
            state = json.loads(row["state"])
            current = state["current"]
            rnd = state["rounds"][current]
        except (TypeError, ValueError, KeyError, IndexError):
            db.rollback()
            abort(400)

        if (
            state.get("finished")
            or rnd.get("shown_is_correct")
            or rnd.get("guessed") is None
            or rnd.get("fix_attempted")
        ):
            db.rollback()
            return redirect(url_for("game"))

        raw_answer = request.form.get("answer", "")
        if len(raw_answer) > 64:
            db.rollback()
            abort(400)
        answer = norm(raw_answer)
        if not answer:
            db.rollback()
            flash("Введите слово", "error")
            return redirect(url_for("game"))

        ok = answer == norm(rnd["correct"])
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

    @app.route("/game/advance", methods=["POST"])
    @login_required
    def game_advance():
        """Продвигает завершённый раунд только по CSRF-защищённому POST."""
        check_csrf()
        db = get_db()
        db.execute("BEGIN IMMEDIATE")
        assert_current_session(db)
        row = db.execute(
            "SELECT state FROM active_games WHERE user_id=?", (g.user["id"],)
        ).fetchone()
        if row is None:
            db.rollback()
            return redirect(url_for("play"))

        try:
            state = json.loads(row["state"])
            rounds = state["rounds"]
            current = state["current"]
            if not isinstance(rounds, list) or isinstance(current, bool) or not isinstance(current, int):
                raise ValueError("Некорректное состояние игры")
        except (TypeError, ValueError, KeyError):
            db.rollback()
            abort(400)

        if state.get("finished"):
            db.rollback()
            return redirect(url_for("game_result"))
        if current < 0:
            db.rollback()
            abort(400)
        if current < len(rounds):
            rnd = rounds[current]
            completed = (
                rnd.get("guessed") is not None
                and (bool(rnd.get("shown_is_correct")) or bool(rnd.get("fix_attempted")))
            )
            if not completed:
                db.rollback()
                abort(400)
            state["current"] += 1

        finished_now = state["current"] >= len(rounds)
        if finished_now:
            _finish_game(db, state, g.user["id"])

        db.execute(
            "UPDATE active_games SET state=?, updated_at=CURRENT_TIMESTAMP WHERE user_id=?",
            (json.dumps(state, ensure_ascii=False), g.user["id"]),
        )
        db.commit()
        if finished_now:
            return redirect(url_for("game_result"))
        return redirect(url_for("game"))

    @app.route("/game/new", methods=["POST"])
    @login_required
    def game_new():
        """Создаёт новую игру и сохраняет её в активном состоянии."""
        check_csrf()
        db = get_db()
        db.execute("BEGIN IMMEDIATE")
        assert_current_session(db)
        num_rounds = request.form.get("num_rounds", 10, type=int)
        if num_rounds not in ROUNDS_OPTIONS:
            num_rounds = 10

        row = db.execute(
            "SELECT state FROM active_games WHERE user_id=?", (g.user["id"],)
        ).fetchone()
        prev_state = None
        if row:
            try:
                prev_state = json.loads(row["state"])
            except (TypeError, ValueError):
                prev_state = None

        state = new_game_state(prev_state, num_rounds)
        db.execute(
            "INSERT INTO active_games (user_id, state, updated_at) "
            "VALUES (?, ?, CURRENT_TIMESTAMP) "
            "ON CONFLICT(user_id) DO UPDATE SET "
            "state=excluded.state, updated_at=CURRENT_TIMESTAMP",
            (g.user["id"], json.dumps(state, ensure_ascii=False)),
        )
        db.commit()
        return redirect(url_for("game"))

    @app.route("/game/result")
    @login_required
    def game_result():
        """Показывает результат последней завершённой игры пользователя."""
        db = get_db()
        game_row = db.execute(
            "SELECT * FROM games WHERE user_id=? ORDER BY id DESC LIMIT 1",
            (g.user["id"],),
        ).fetchone()

        row = db.execute(
            "SELECT state FROM active_games WHERE user_id=?", (g.user["id"],)
        ).fetchone()
        errors = []
        max_score = 0
        if row:
            try:
                state = json.loads(row["state"])
                errors = state.get("errors", [])
                max_score = state.get("max_score")
                if max_score is None:
                    max_score = len(state.get("rounds", [])) * 2
            except (TypeError, ValueError, AttributeError):
                errors = []

        return render_template(
            "result.html", game=game_row, errors=errors, max_score=max_score
        )


def _finish_game(db, state, user_id):
    """Считает итоги игры и пишет их в БД."""
    total_correct = sum(round_data["actions_correct"] for round_data in state["rounds"])
    total_wrong = sum(round_data["actions_wrong"] for round_data in state["rounds"])
    max_possible = sum(
        1 if round_data["shown_is_correct"] else 2 for round_data in state["rounds"]
    )

    state["finished"] = True
    state["max_score"] = max_possible

    db.execute(
        "INSERT INTO games (user_id, score, rounds, correct, wrong, max_score) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (
            user_id,
            state["score"],
            len(state["rounds"]),
            total_correct,
            total_wrong,
            max_possible,
        ),
    )
    db.execute(
        """UPDATE users SET
             best_score=MAX(best_score, ?),
             correct_total=correct_total + ?,
             wrong_total=wrong_total + ?
           WHERE id=?""",
        (state["score"], total_correct, total_wrong, user_id),
    )

    errors = []
    for round_data in state["rounds"]:
        if round_data["actions_wrong"] > 0:
            errors.append({
                "shown": round_data["shown"],
                "correct": round_data["correct"],
                "shown_is_correct": round_data["shown_is_correct"],
                "guess_wrong": round_data["guessed"] is False,
                "fix_wrong": round_data["fix_ok"] is False,
            })
    state["errors"] = errors
