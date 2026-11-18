import os
import json
import random
import secrets
import sqlite3
from functools import wraps

from flask import (
    Flask, render_template, request, redirect, url_for,
    session, flash, g, abort, send_from_directory,
)
from werkzeug.security import generate_password_hash, check_password_hash

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(BASE_DIR, "game.db")

# Сколько последних слов не повторяем ни при каких обстоятельствах
# (чтобы слово не встретилось чаще, чем раз в 15 раундов)
NO_REPEAT_WINDOW = 15

app = Flask(__name__)
app.config.update(
    SECRET_KEY=os.environ.get("SECRET_KEY", "dev-secret-change-me"),
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE="Lax",
    SESSION_COOKIE_SECURE=os.environ.get("HTTPS") == "1",
    PERMANENT_SESSION_LIFETIME=60 * 60 * 24 * 30,
    MAX_CONTENT_LENGTH=64 * 1024,
)

DUMMY_HASH = generate_password_hash("dummy-password-for-timing")

# ---------- Тема ----------
THEME_COOKIE = "theme"
THEME_COOKIE_MAX_AGE = 60 * 60 * 24 * 365  # 1 год


def current_theme():
    t = request.cookies.get(THEME_COOKIE, "dark")
    return t if t in ("dark", "light") else "dark"


@app.context_processor
def inject_theme():
    return {"theme": current_theme()}


# ---------- БД ----------
def get_db():
    if "db" not in g:
        g.db = sqlite3.connect(DB_PATH)
        g.db.row_factory = sqlite3.Row
        g.db.execute("PRAGMA foreign_keys = ON")
    return g.db


@app.teardown_appcontext
def close_db(exc=None):
    db = g.pop("db", None)
    if db is not None:
        db.close()


def init_db():
    db = sqlite3.connect(DB_PATH)
    db.executescript("""
    CREATE TABLE IF NOT EXISTS users (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        username TEXT UNIQUE NOT NULL,
        password_hash TEXT NOT NULL,
        is_admin INTEGER NOT NULL DEFAULT 0,
        best_score INTEGER NOT NULL DEFAULT 0,
        correct_total INTEGER NOT NULL DEFAULT 0,
        wrong_total INTEGER NOT NULL DEFAULT 0,
        created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
    );
    CREATE TABLE IF NOT EXISTS games (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
        score INTEGER NOT NULL,
        rounds INTEGER NOT NULL,
        correct INTEGER NOT NULL,
        wrong INTEGER NOT NULL,
        max_score INTEGER NOT NULL DEFAULT 0,
        played_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
    );
    CREATE TABLE IF NOT EXISTS active_games (
        user_id INTEGER PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
        state TEXT NOT NULL,
        updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
    );
    CREATE INDEX IF NOT EXISTS idx_games_user ON games(user_id);
    CREATE INDEX IF NOT EXISTS idx_users_best ON users(best_score DESC);
    """)
    
    # Дополнительная защита для уже существующих баз
    columns = [row[1] for row in db.execute("PRAGMA table_info(games)").fetchall()]
    if "max_score" not in columns:
        db.execute("ALTER TABLE games ADD COLUMN max_score INTEGER NOT NULL DEFAULT 0")
        db.execute("UPDATE games SET max_score = rounds * 2 WHERE max_score = 0")

    if db.execute("SELECT COUNT(*) FROM users WHERE is_admin=1").fetchone()[0] == 0:
        db.execute(
            "INSERT INTO users (username, password_hash, is_admin) VALUES (?,?,1)",
            ("admin", generate_password_hash(os.environ.get("ADMIN_PASSWORD", "admin"))),
        )
    db.commit()
    db.close()


init_db()


# ---------- Favicon ----------
@app.route("/favicon.ico")
def favicon():
    return send_from_directory(
        os.path.join(app.root_path, "static"),
        "favicon.ico",
        mimetype="image/vnd.microsoft.icon",
    )


# ---------- Задания (true/false) ----------
_pairs_cache = None


def load_pairs():
    global _pairs_cache
    if _pairs_cache is None:
        with open(os.path.join(BASE_DIR, "true.json"), encoding="utf-8") as f:
            true_words = json.load(f)
        with open(os.path.join(BASE_DIR, "false.json"), encoding="utf-8") as f:
            false_data = json.load(f)

        if len(true_words) != len(false_data):
            raise RuntimeError(
                f"true.json ({len(true_words)}) и false.json ({len(false_data)}) "
                f"должны иметь одинаковую длину"
            )

        pairs = []
        for idx, (t, f_variants) in enumerate(zip(true_words, false_data)):
            t = (t or "").strip()
            if not t:
                continue
            if isinstance(f_variants, str):
                variants = [f_variants]
            elif isinstance(f_variants, list):
                variants = f_variants
            else:
                raise RuntimeError(
                    f"false.json[{idx}] должен быть строкой или массивом строк"
                )
            clean = []
            seen = set()
            for v in variants:
                v = (v or "").replace(" ", "").replace("\u00a0", "").strip()
                if not v:
                    continue
                if v.lower() == t.lower():
                    continue
                if v.lower() in seen:
                    continue
                seen.add(v.lower())
                clean.append(v)
            if not clean:
                clean = [t + "ъ"]
            pairs.append({"word": t, "false_variants": clean})
        _pairs_cache = pairs
    return _pairs_cache


def norm(s: str) -> str:
    s = (s or "").replace("ё", "е").lower()
    return "".join(ch for ch in s if ch.isalpha())


def _pick_rounds(state, count):
    all_pairs = load_pairs()
    total = len(all_pairs)

    recent = list(state.get("recent", []))
    recent_set = set(recent)

    pool = [i for i in range(total) if i not in recent_set]

    if len(pool) < count:
        hard_block = set(recent[-10:])
        pool = [i for i in range(total) if i not in hard_block]

    random.shuffle(pool)
    chosen = pool[:count]

    recent.extend(chosen)
    state["recent"] = recent[-NO_REPEAT_WINDOW:]

    return chosen


def _make_round(word_idx):
    pair = load_pairs()[word_idx]
    correct = pair["word"]
    variants = pair["false_variants"]

    if variants and random.random() < 0.5:
        shown = random.choice(variants)
        shown_is_correct = False
    else:
        shown = correct
        shown_is_correct = True

    return {
        "word_idx": word_idx,
        "shown": shown,
        "correct": correct,
        "shown_is_correct": shown_is_correct,
        "guessed": None,
        "fix_attempted": False,
        "fix_ok": None,
        "actions_correct": 0,
        "actions_wrong": 0,
    }


def new_game_state(prev_state, num_rounds):
    base = {
        "rounds": [],
        "current": 0,
        "score": 0,
        "finished": False,
        "recent": list(prev_state.get("recent", [])) if prev_state else [],
        "errors": [],
    }
    chosen = _pick_rounds(base, num_rounds)
    base["rounds"] = [_make_round(i) for i in chosen]
    return base


# ---------- CSRF ----------
def csrf_token():
    if "_csrf" not in session:
        session["_csrf"] = secrets.token_hex(16)
    return session["_csrf"]


def check_csrf():
    token = request.form.get("_csrf", "")
    if not token or not secrets.compare_digest(token, session.get("_csrf", "")):
        abort(400, "CSRF token missing or invalid")


app.jinja_env.globals["csrf_token"] = csrf_token


# ---------- Auth helpers ----------
@app.before_request
def load_user():
    g.user = None
    uid = session.get("user_id")
    if uid:
        row = get_db().execute("SELECT * FROM users WHERE id=?", (uid,)).fetchone()
        if row is None:
            session.clear()
        else:
            g.user = row


def login_required(f):
    @wraps(f)
    def wrapper(*a, **kw):
        if not g.user:
            return redirect(url_for("login", next=request.path))
        return f(*a, **kw)
    return wrapper


def admin_required(f):
    @wraps(f)
    def wrapper(*a, **kw):
        if not g.user or not g.user["is_admin"]:
            abort(403)
        return f(*a, **kw)
    return wrapper


# ---------- Публичные страницы ----------
@app.route("/")
def index():
    return render_template("index.html")


@app.route("/register", methods=["GET", "POST"])
def register():
    if g.user:
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
    if g.user:
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
    check_csrf()
    session.clear()
    return redirect(url_for("index"))


# ---------- Тема ----------
@app.route("/theme/toggle", methods=["POST"])
def theme_toggle():
    check_csrf()
    current = current_theme()
    new_theme = "light" if current == "dark" else "dark"
    target = request.form.get("next") or request.referrer or url_for("index")
    if not target.startswith("/") or target.startswith("//"):
        target = url_for("index")
    resp = redirect(target)
    resp.set_cookie(
        THEME_COOKIE,
        new_theme,
        max_age=THEME_COOKIE_MAX_AGE,
        httponly=False,
        samesite="Lax",
        secure=(os.environ.get("HTTPS") == "1"),
    )
    return resp


# ---------- Игра ----------
@app.route("/play")
@login_required
def play():
    db = get_db()
    row = db.execute("SELECT state FROM active_games WHERE user_id=?", (g.user["id"],)).fetchone()
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
        # Если нет активной игры, перекидываем в меню
        return redirect(url_for("play"))
    else:
        state = json.loads(row["state"])
        if state.get("finished"):
            return redirect(url_for("game_result"))

    if state["current"] >= len(state["rounds"]):
        state["finished"] = True
        db.execute(
            "UPDATE active_games SET state=?, updated_at=CURRENT_TIMESTAMP WHERE user_id=?",
            (json.dumps(state, ensure_ascii=False), g.user["id"]),
        )
        db.commit()
        return redirect(url_for("game_result"))

    rnd = state["rounds"][state["current"]]

    if rnd["guessed"] is None:
        phase = "guess"
    elif rnd["shown_is_correct"] is False and not rnd["fix_attempted"]:
        phase = "fix"
    else:
        state["current"] += 1
        finished_now = state["current"] >= len(state["rounds"])
        if finished_now:
            state["finished"] = True

            total_correct = sum(r["actions_correct"] for r in state["rounds"])
            total_wrong   = sum(r["actions_wrong"]   for r in state["rounds"])
            max_possible  = sum(1 if r["shown_is_correct"] else 2 for r in state["rounds"])
            state["max_score"] = max_possible

            db.execute(
                "INSERT INTO games (user_id, score, rounds, correct, wrong, max_score) VALUES (?,?,?,?,?,?)",
                (g.user["id"], state["score"], len(state["rounds"]),
                 total_correct, total_wrong, max_possible),
            )
            db.execute(
                """UPDATE users SET
                     best_score = MAX(best_score, ?),
                     correct_total = correct_total + ?,
                     wrong_total = wrong_total + ?
                   WHERE id = ?""",
                (state["score"], total_correct, total_wrong, g.user["id"]),
            )

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
            "UPDATE active_games SET state=?, updated_at=CURRENT_TIMESTAMP WHERE user_id=?",
            (json.dumps(state, ensure_ascii=False), g.user["id"]),
        )
        db.commit()
        if finished_now:
            return redirect(url_for("game_result"))
        return redirect(url_for("game"))

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
    if num_rounds not in (10, 15, 20, 25, 30, 40, 50):
        num_rounds = 10
        
    row = db.execute(
        "SELECT state FROM active_games WHERE user_id=?", (g.user["id"],)
    ).fetchone()
    prev_state = json.loads(row["state"]) if row else None
    
    state = new_game_state(prev_state, num_rounds)
    
    db.execute(
        "UPDATE active_games SET state=?, updated_at=CURRENT_TIMESTAMP WHERE user_id=?",
        (json.dumps(state, ensure_ascii=False), g.user["id"]),
    )
    db.commit()
    return redirect(url_for("game"))


@app.route("/game/result")
@login_required
def game_result():
    db = get_db()
    game = db.execute(
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
            st = json.loads(row["state"])
            errors = st.get("errors", [])
            max_score = st.get("max_score", st.get("rounds", 10) * 2)
        except (ValueError, KeyError):
            errors = []

    return render_template("result.html", game=game, errors=errors, max_score=max_score)


# ---------- Статистика / рейтинг ----------
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


# ---------- Админка ----------
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


# ---------- Ошибки ----------
@app.errorhandler(400)
@app.errorhandler(403)
@app.errorhandler(404)
def handle_error(e):
    code = getattr(e, "code", 400)
    msgs = {400: "Некорректный запрос", 403: "Доступ запрещён", 404: "Страница не найдена"}
    return render_template("error.html", code=code, message=msgs.get(code, "Ошибка")), code


if __name__ == "__main__":
    app.run(debug=True)