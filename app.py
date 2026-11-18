import os

from flask import (
    Flask, render_template, redirect, url_for, request, send_from_directory,
)

from config import THEME_COOKIE, THEME_COOKIE_MAX_AGE
from db import init_db, close_db
from utils import (
    csrf_token, current_theme, inject_theme, load_user, check_csrf,
)

import auth
import game
import stats
import admin


def create_app():
    app = Flask(__name__)
    app.config.update(
        SECRET_KEY=os.environ.get("SECRET_KEY", "dev-secret-change-me"),
        SESSION_COOKIE_HTTPONLY=True,
        SESSION_COOKIE_SAMESITE="Lax",
        SESSION_COOKIE_SECURE=os.environ.get("HTTPS") == "1",
        PERMANENT_SESSION_LIFETIME=60 * 60 * 24 * 30,
        MAX_CONTENT_LENGTH=64 * 1024,
    )

    # Jinja + контекст
    app.jinja_env.globals["csrf_token"] = csrf_token
    app.context_processor(inject_theme)

    # Жизненный цикл запроса
    app.before_request(load_user)
    app.teardown_appcontext(close_db)

    # ----- Служебные маршруты -----
    @app.route("/")
    def index():
        return render_template("index.html")

    @app.route("/favicon.ico")
    def favicon():
        return send_from_directory(
            os.path.join(app.root_path, "static"),
            "favicon.ico",
            mimetype="image/vnd.microsoft.icon",
        )

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

    # ----- Модули -----
    auth.register_routes(app)
    game.register_routes(app)
    stats.register_routes(app)
    admin.register_routes(app)

    # ----- Обработчики ошибок -----
    @app.errorhandler(400)
    @app.errorhandler(403)
    @app.errorhandler(404)
    def handle_error(e):
        code = getattr(e, "code", 400)
        msgs = {
            400: "Некорректный запрос",
            403: "Доступ запрещён",
            404: "Страница не найдена",
        }
        return render_template("error.html", code=code, message=msgs.get(code, "Ошибка")), code

    return app


app = create_app()
init_db()


if __name__ == "__main__":
    app.run(debug=True)