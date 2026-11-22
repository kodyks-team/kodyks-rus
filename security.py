"""Управление серверными сессиями и ограничением попыток входа."""

import hashlib
import hmac
import secrets
import time
from urllib.parse import urlsplit

from flask import abort, current_app, request, session

from db import get_db
from config import normalize_username


def _fingerprint(value):
    """Возвращает HMAC-отпечаток значения без сохранения исходных данных."""
    key = current_app.secret_key
    if isinstance(key, str):
        key = key.encode("utf-8")

    return hmac.new(
        key,
        str(value).encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()


def _session_hash(token):
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def create_user_session(user_id):
    """Создаёт новую серверную сессию и меняет идентификатор сессии клиента."""
    db = get_db()
    now = int(time.time())

    account = db.execute(
        "SELECT is_admin FROM users WHERE id=?",
        (user_id,),
    ).fetchone()

    if account is None:
        raise ValueError("Нельзя создать сессию для несуществующего пользователя.")

    lifetime_key = (
        "ADMIN_SESSION_LIFETIME_SECONDS"
        if account["is_admin"]
        else "SESSION_LIFETIME_SECONDS"
    )

    lifetime = current_app.config[lifetime_key]
    token = secrets.token_urlsafe(32)

    db.execute(
        "DELETE FROM user_sessions WHERE expires_at <= ?",
        (now,),
    )

    db.execute(
        """
        INSERT INTO user_sessions (user_id, token_hash, created_at, expires_at)
        VALUES (?, ?, ?, ?)
        """,
        (user_id, _session_hash(token), now, now + lifetime),
    )

    # Полная очистка исключает сохранение данных старой анонимной сессии.
    session.clear()
    session["user_id"] = int(user_id)
    session["session_id"] = token
    session["_csrf"] = secrets.token_hex(32)
    session.permanent = True


def revoke_current_session():
    """Немедленно отзывает текущую сессию на сервере."""
    token = session.get("session_id")

    if isinstance(token, str) and 20 <= len(token) <= 128:
        get_db().execute(
            "DELETE FROM user_sessions WHERE token_hash=?",
            (_session_hash(token),),
        )
        get_db().commit()


def revoke_user_sessions(user_id):
    """Отзывает все активные сессии выбранного пользователя."""
    get_db().execute(
        "DELETE FROM user_sessions WHERE user_id=?",
        (user_id,),
    )


def load_session_user():
    """Проверяет подпись cookie, сессию и актуальную роль пользователя."""
    user_id = session.get("user_id")
    token = session.get("session_id")

    if user_id is None and token is None:
        return None

    if isinstance(user_id, bool) or not isinstance(user_id, int):
        session.clear()
        return None

    if not isinstance(token, str) or not 20 <= len(token) <= 128:
        session.clear()
        return None

    now = int(time.time())

    row = get_db().execute(
        """
        SELECT
            u.id,
            u.username,
            u.is_admin,
            u.role,
            u.best_score,
            u.correct_total,
            u.wrong_total,
            u.timezone
        FROM users AS u
        JOIN user_sessions AS s ON s.user_id=u.id
        WHERE u.id=? AND s.token_hash=? AND s.expires_at>?
        """,
        (user_id, _session_hash(token), now),
    ).fetchone()

    if row is None:
        session.clear()
        return None

    return row


def login_fingerprints(username):
    """Формирует обезличенные отпечатки IP и логина."""
    ip = request.remote_addr or "unknown"
    normalized_username = normalize_username(username)

    return (
        _fingerprint(f"ip:{ip}"),
        _fingerprint(f"username:{normalized_username}"),
    )


def login_is_limited(db, username):
    """Проверяет лимит попыток для пары IP/логин и одного IP."""
    ip_hash, username_hash = login_fingerprints(username)
    cutoff = int(time.time()) - current_app.config["LOGIN_RATE_WINDOW_SECONDS"]

    pair_count = db.execute(
        """
        SELECT COUNT(*)
        FROM auth_attempts
        WHERE ip_hash=? AND username_hash=? AND attempted_at>?
        """,
        (ip_hash, username_hash, cutoff),
    ).fetchone()[0]

    ip_count = db.execute(
        """
        SELECT COUNT(*)
        FROM auth_attempts
        WHERE ip_hash=? AND attempted_at>?
        """,
        (ip_hash, cutoff),
    ).fetchone()[0]

    return (
        pair_count >= current_app.config["LOGIN_RATE_LIMIT_PAIR"]
        or ip_count >= current_app.config["LOGIN_RATE_LIMIT_IP"]
    )


def record_login_attempt(db, username):
    """Записывает попытку входа без хранения IP и имени пользователя."""
    ip_hash, username_hash = login_fingerprints(username)
    now = int(time.time())

    db.execute(
        "DELETE FROM auth_attempts WHERE attempted_at<?",
        (now - 24 * 60 * 60,),
    )

    db.execute(
        """
        INSERT INTO auth_attempts (ip_hash, username_hash, attempted_at)
        VALUES (?, ?, ?)
        """,
        (ip_hash, username_hash, now),
    )

    db.commit()


def _origin_host(value):
    """Возвращает безопасно разобранные имя хоста и порт из Origin/Referer."""
    try:
        parsed = urlsplit(value)

        if parsed.scheme not in ("http", "https") or not parsed.hostname:
            return None

        if parsed.username is not None or parsed.password is not None:
            return None

        return parsed.scheme, parsed.hostname.casefold(), parsed.port

    except ValueError:
        return None


def validate_request_origin():
    """Блокирует очевидные межсайтовые запросы по Fetch Metadata и Origin."""
    fetch_site = request.headers.get("Sec-Fetch-Site", "").casefold()

    if fetch_site == "cross-site":
        abort(400, "Cross-site request rejected")

    source = request.headers.get("Origin")

    if not source:
        source = request.headers.get("Referer")

    if not source:
        return

    expected_scheme = current_app.config.get(
        "PREFERRED_URL_SCHEME",
        request.scheme,
    )

    source_host = _origin_host(source)
    request_host = _origin_host(f"{expected_scheme}://{request.host}")

    if source_host is None or request_host is None:
        abort(400, "Invalid request origin")

    source_scheme, source_name, source_port = source_host
    request_scheme, request_name, request_port = request_host

    if source_scheme != request_scheme or source_name != request_name:
        abort(400, "Cross-origin request rejected")

    # Если порт явно указан в Host, он обязан совпадать с Origin.
    if request_port:
        default_source_port = 443 if source_scheme == "https" else 80
        effective_source_port = source_port or default_source_port

        if effective_source_port != request_port:
            abort(400, "Cross-origin request rejected")

    elif source_port and source_port != (
        443 if source_scheme == "https" else 80
    ):
        abort(400, "Cross-origin request rejected")


def registration_is_limited(db):
    """Ограничивает частоту создания аккаунтов с одного IP-адреса."""
    ip_hash, _ = login_fingerprints("")
    cutoff = int(time.time()) - current_app.config["REGISTER_RATE_WINDOW_SECONDS"]

    count = db.execute(
        """
        SELECT COUNT(*)
        FROM registration_attempts
        WHERE ip_hash=? AND attempted_at>?
        """,
        (ip_hash, cutoff),
    ).fetchone()[0]

    return count >= current_app.config["REGISTER_RATE_LIMIT_IP"]


def record_registration_attempt(db):
    """Сохраняет обезличенный факт попытки регистрации."""
    ip_hash, _ = login_fingerprints("")
    now = int(time.time())

    db.execute(
        "DELETE FROM registration_attempts WHERE attempted_at<?",
        (now - 24 * 60 * 60,),
    )

    db.execute(
        """
        INSERT INTO registration_attempts (ip_hash, attempted_at)
        VALUES (?, ?)
        """,
        (ip_hash, now),
    )

    db.commit()