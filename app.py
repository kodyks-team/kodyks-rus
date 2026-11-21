import os
import secrets
from datetime import timedelta
from urllib.parse import unquote, urlsplit

from flask import Flask, render_template, redirect, url_for, request, send_from_directory

from config import (
    DB_PATH,
    THEME_COOKIE,
    THEME_COOKIE_MAX_AGE,
    SESSION_LIFETIME_SECONDS,
    ADMIN_SESSION_LIFETIME_SECONDS,
    LOGIN_RATE_LIMIT_PAIR,
    LOGIN_RATE_LIMIT_IP,
    LOGIN_RATE_WINDOW_SECONDS,
    REGISTER_RATE_WINDOW_SECONDS,
    REGISTER_RATE_LIMIT_IP,
)
from db import init_db, close_db
from utils import csrf_token, current_theme, inject_theme, load_user, check_csrf

import auth
import game
import stats
import admin


def _environment_bool(name, default=False):
    value = os.environ.get(name)
    if value is None:
        return default
    return value.strip().lower() in ("1", "true", "yes", "on")


def create_app(test_config=None):
    """Создаёт приложение с безопасными настройками по умолчанию."""
    is_production = (
        os.environ.get("APP_ENV", "production").strip().lower() != "development"
        or _environment_bool("HTTPS", False)
    )
    configured_secret = os.environ.get("SECRET_KEY")
    if test_config and test_config.get("SECRET_KEY"):
        configured_secret = test_config["SECRET_KEY"]
    if not configured_secret:
        if is_production and not (test_config and test_config.get("TESTING")):
            raise RuntimeError("В production обязательно задайте уникальный SECRET_KEY.")
        configured_secret = secrets.token_hex(32)
    secret_length = len(configured_secret.encode("utf-8"))
    if is_production and secret_length < 64 and not (test_config and test_config.get("TESTING")):
        raise RuntimeError(
            "В production SECRET_KEY должен содержать не менее 64 байт. "
            "Используйте secrets.token_hex(32)."
        )
    if secret_length < 32 and not (test_config and test_config.get("TESTING")):
        raise RuntimeError("SECRET_KEY должен содержать не менее 32 байт.")
    if is_production and not (test_config and test_config.get("TESTING")):
        repeated_pattern = any(
            len(configured_secret) >= 64
            and configured_secret == configured_secret[:size] * (len(configured_secret) // size)
                + configured_secret[:len(configured_secret) % size]
            for size in range(1, 33)
        )
        if len(set(configured_secret)) < 12 or repeated_pattern:
            raise RuntimeError(
                "SECRET_KEY похож на повторяющийся или предсказуемый. "
                "Сгенерируйте его через secrets.token_hex(32)."
            )
    if configured_secret.strip().lower() in {
        "secret", "changeme", "change-me", "dev-secret-change-me", "your-secret-key"
    } and not (test_config and test_config.get("TESTING")):
        raise RuntimeError("SECRET_KEY содержит известное небезопасное значение.")

    cookie_secure = (
        is_production
        or _environment_bool("COOKIE_SECURE", False)
        or _environment_bool("HTTPS", False)
    )
    app = Flask(__name__)
    app.config.update(
        SECRET_KEY=configured_secret,
        SESSION_COOKIE_NAME="__Host-kodyazyka_session" if cookie_secure else "kodyazyka_session",
        SESSION_COOKIE_HTTPONLY=True,
        SESSION_COOKIE_SECURE=cookie_secure,
        SESSION_COOKIE_SAMESITE="Lax",
        SESSION_COOKIE_PATH="/",
        SESSION_COOKIE_DOMAIN=None,
        SESSION_REFRESH_EACH_REQUEST=False,
        PERMANENT_SESSION_LIFETIME=timedelta(seconds=SESSION_LIFETIME_SECONDS),
        SESSION_LIFETIME_SECONDS=SESSION_LIFETIME_SECONDS,
        ADMIN_SESSION_LIFETIME_SECONDS=ADMIN_SESSION_LIFETIME_SECONDS,
        LOGIN_RATE_WINDOW_SECONDS=LOGIN_RATE_WINDOW_SECONDS,
        LOGIN_RATE_LIMIT_PAIR=LOGIN_RATE_LIMIT_PAIR,
        LOGIN_RATE_LIMIT_IP=LOGIN_RATE_LIMIT_IP,
        REGISTER_RATE_WINDOW_SECONDS=REGISTER_RATE_WINDOW_SECONDS,
        REGISTER_RATE_LIMIT_IP=REGISTER_RATE_LIMIT_IP,
        MAX_CONTENT_LENGTH=64 * 1024,
        MAX_FORM_MEMORY_SIZE=16 * 1024,
        MAX_FORM_PARTS=20,
        DATABASE=os.environ.get("DATABASE_PATH", DB_PATH),
        TRUSTED_HOSTS=[x.strip() for x in os.environ.get("TRUSTED_HOSTS", "").split(",") if x.strip()] or None,
        PREFERRED_URL_SCHEME="https" if cookie_secure else "http",
        DEBUG=_environment_bool("FLASK_DEBUG", False) and not is_production,
    )
    if test_config:
        app.config.update(test_config)
    if is_production and not app.config.get("TRUSTED_HOSTS"):
        raise RuntimeError(
            "В production задайте TRUSTED_HOSTS: точный домен сайта через запятую."
        )
    if is_production:
        for trusted_host in app.config.get("TRUSTED_HOSTS", []):
            try:
                parsed_host = urlsplit(f"http://{trusted_host}")
                valid_host = (
                    isinstance(trusted_host, str)
                    and bool(parsed_host.hostname)
                    and parsed_host.netloc == trusted_host
                    and parsed_host.username is None
                    and parsed_host.password is None
                    and parsed_host.path == ""
                    and parsed_host.query == ""
                    and parsed_host.fragment == ""
                    and trusted_host != "*"
                    and not trusted_host.startswith(".")
                    and not any(char in trusted_host for char in "%/\\@#?")
                    and not any(char.isspace() or ord(char) < 32 for char in trusted_host)
                )
                _ = parsed_host.port
            except ValueError:
                valid_host = False
            if not valid_host:
                raise RuntimeError(
                    "TRUSTED_HOSTS должен содержать только точные имена хостов, "
                    "без wildcard, протокола и пути."
                )
        if not app.config.get("SESSION_COOKIE_SECURE"):
            raise RuntimeError("В production SESSION_COOKIE_SECURE должен быть включён.")
        if app.config["SESSION_COOKIE_NAME"] != "__Host-kodyazyka_session":
            raise RuntimeError("В production используется защищённая cookie с префиксом __Host-.")
        if not app.config.get("SESSION_COOKIE_HTTPONLY"):
            raise RuntimeError("В production SESSION_COOKIE_HTTPONLY должен быть включён.")
        if app.config.get("SESSION_COOKIE_SAMESITE") not in ("Lax", "Strict"):
            raise RuntimeError("В production SESSION_COOKIE_SAMESITE должен быть Lax или Strict.")
        if app.config.get("SESSION_COOKIE_PATH") != "/" or app.config.get("SESSION_COOKIE_DOMAIN") is not None:
            raise RuntimeError("Для __Host-cookie обязательны Path=/ и отсутствие Domain.")

    app.jinja_env.globals["csrf_token"] = csrf_token
    app.context_processor(inject_theme)
    app.before_request(load_user)
    app.teardown_appcontext(close_db)

    @app.after_request
    def add_security_headers(response):
        """Добавляет защитные HTTP-заголовки ко всем ответам."""
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("X-Frame-Options", "DENY")
        response.headers.setdefault("Cross-Origin-Opener-Policy", "same-origin")
        response.headers.setdefault("Cross-Origin-Resource-Policy", "same-origin")
        response.headers.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
        response.headers.setdefault(
            "Permissions-Policy",
            "camera=(), microphone=(), geolocation=(), payment=(), usb=()",
        )
        response.headers.setdefault("X-Permitted-Cross-Domain-Policies", "none")
        response.headers.setdefault("Cache-Control", "no-store, max-age=0")
        response.headers.setdefault("Pragma", "no-cache")
        # Внешний источник скриптов нужен только странице статистики для Chart.js.
        # Все остальные маршруты исполняют исключительно локальный JavaScript.
        script_sources = "'self'"
        if request.endpoint == "stats":
            script_sources += " 'sha256-0q+JdOlScWOHcunpUk21uab1jW7C1deBQARHtKMcaB4='"
        response.headers.setdefault(
            "Content-Security-Policy",
            "default-src 'self'; base-uri 'self'; object-src 'none'; "
            "frame-ancestors 'none'; form-action 'self'; img-src 'self' data:; "
            "style-src 'self' 'unsafe-inline'; "
            f"script-src {script_sources}; connect-src 'self'; "
            "font-src 'self' data:",
        )
        if app.config.get("SESSION_COOKIE_SECURE"):
            response.headers.setdefault(
                "Strict-Transport-Security", "max-age=31536000; includeSubDomains"
            )
        return response

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
        target = request.form.get("next", "")
        # Принимаем только локальный абсолютный путь без обратных слешей и управляющих символов.
        decoded_target = target
        for _ in range(8):
            next_target = unquote(decoded_target)
            if next_target == decoded_target:
                break
            decoded_target = next_target
        else:
            decoded_target = "//invalid"
        if (
            not target.startswith("/")
            or not decoded_target.startswith("/")
            or decoded_target.startswith("//")
            or "\\" in decoded_target
            or any(ord(char) < 32 or ord(char) == 127 for char in decoded_target)
        ):
            target = url_for("index")
        resp = redirect(target)
        resp.set_cookie(
            THEME_COOKIE,
            new_theme,
            max_age=THEME_COOKIE_MAX_AGE,
            httponly=False,
            samesite="Lax",
            secure=app.config["SESSION_COOKIE_SECURE"],
            path="/",
        )
        return resp

    auth.register_routes(app)
    game.register_routes(app)
    stats.register_routes(app)
    admin.register_routes(app)

    @app.errorhandler(400)
    @app.errorhandler(403)
    @app.errorhandler(404)
    @app.errorhandler(429)
    def handle_error(e):
        code = getattr(e, "code", 400)
        msgs = {
            400: "Некорректный запрос",
            403: "Доступ запрещён",
            404: "Страница не найдена",
            429: "Слишком много попыток. Попробуйте позже.",
        }
        return render_template("error.html", code=code, message=msgs.get(code, "Ошибка")), code

    @app.errorhandler(500)
    def handle_server_error(e):
        app.logger.exception("Внутренняя ошибка приложения")
        return render_template("error.html", code=500, message="Внутренняя ошибка сервера"), 500

    with app.app_context():
        init_db(app.config.get("DATABASE"))

    return app


app = create_app()


if __name__ == "__main__":
    app.run(debug=app.config["DEBUG"])
