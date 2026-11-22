import os
import sqlite3
import stat
import warnings

from werkzeug.security import check_password_hash, generate_password_hash

from config import DB_PATH, PASSWORD_MIN_LENGTH, PASSWORD_MAX_LENGTH, normalize_username


def _secure_database_file(database):
    """Создаёт SQLite-файл с доступом только владельцу процесса."""
    if database == ":memory:":
        return

    descriptor = None
    try:
        parent = os.path.dirname(os.path.abspath(database))
        parent_mode = os.stat(parent, follow_symlinks=False).st_mode

        if stat.S_ISLNK(parent_mode):
            raise OSError("Каталог базы данных не должен быть символической ссылкой.")

        if parent_mode & (stat.S_IWGRP | stat.S_IWOTH) and not parent_mode & stat.S_ISVTX:
            raise OSError("Каталог базы данных доступен для записи посторонним пользователям.")

        flags = os.O_CREAT | os.O_RDWR
        if hasattr(os, "O_NOFOLLOW"):
            flags |= os.O_NOFOLLOW
        if hasattr(os, "O_CLOEXEC"):
            flags |= os.O_CLOEXEC

        descriptor = os.open(database, flags, 0o600)
        file_info = os.fstat(descriptor)

        if not stat.S_ISREG(file_info.st_mode):
            raise OSError("Путь базы данных должен указывать на обычный файл.")

        if hasattr(os, "geteuid") and file_info.st_uid != os.geteuid():
            raise OSError("Файл базы данных принадлежит другому пользователю.")

        if hasattr(os, "fchmod"):
            os.fchmod(descriptor, 0o600)
        else:
            os.chmod(database, 0o600)

    except OSError as exc:
        raise RuntimeError(
            "Небезопасный путь базы данных: требуется обычный файл владельца приложения "
            "с правами 0600 в каталоге, недоступном для записи чужой группе или другим пользователям."
        ) from exc
    finally:
        if descriptor is not None:
            os.close(descriptor)


def _configure_connection(db):
    """Включает ограничения SQLite для каждого соединения."""
    db.execute("PRAGMA foreign_keys = ON")
    db.execute("PRAGMA busy_timeout = 10000")

    if hasattr(db, "setconfig"):
        db.setconfig(sqlite3.SQLITE_DBCONFIG_DEFENSIVE, True)
        db.setconfig(sqlite3.SQLITE_DBCONFIG_TRUSTED_SCHEMA, False)
    else:
        db.execute("PRAGMA trusted_schema = OFF")


def get_db():
    """Возвращает соединение с БД текущего приложения."""
    from flask import current_app, g

    if "db" not in g:
        database = current_app.config.get("DATABASE", DB_PATH)
        g.db = sqlite3.connect(database, timeout=10)
        g.db.row_factory = sqlite3.Row
        _configure_connection(g.db)

    return g.db


def close_db(exc=None):
    """Закрывает соединение с БД в конце контекста приложения."""
    from flask import g

    db = g.pop("db", None)
    if db is not None:
        db.close()


def init_db(db_path=None):
    """Создаёт таблицы и безопасно мигрирует существующую базу данных."""
    database = db_path or DB_PATH
    parent = os.path.dirname(os.path.abspath(database))
    os.makedirs(parent, exist_ok=True)

    _secure_database_file(database)

    db = sqlite3.connect(database, timeout=10)
    db.row_factory = sqlite3.Row
    _configure_connection(db)

    try:
        db.executescript("""
            CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                username TEXT UNIQUE NOT NULL,
                username_key TEXT,
                password_hash TEXT NOT NULL,
                is_admin INTEGER NOT NULL DEFAULT 0,
                role TEXT NOT NULL DEFAULT 'user'
                    CHECK (role IN ('user', 'admin', 'owner')),
                best_score INTEGER NOT NULL DEFAULT 0,
                correct_total INTEGER NOT NULL DEFAULT 0,
                wrong_total INTEGER NOT NULL DEFAULT 0,
                timezone TEXT NOT NULL DEFAULT 'Europe/Moscow',
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
                errors TEXT DEFAULT '[]',
                played_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            );

            CREATE TABLE IF NOT EXISTS active_games (
                user_id INTEGER PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
                state TEXT NOT NULL,
                updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            );

            CREATE TABLE IF NOT EXISTS admin_logs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                admin_id INTEGER REFERENCES users(id) ON DELETE SET NULL,
                command TEXT NOT NULL,
                output TEXT NOT NULL,
                is_error INTEGER NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            );

            CREATE TABLE IF NOT EXISTS user_sessions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                token_hash TEXT NOT NULL UNIQUE,
                created_at INTEGER NOT NULL,
                expires_at INTEGER NOT NULL
            );

            CREATE TABLE IF NOT EXISTS auth_attempts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                ip_hash TEXT NOT NULL,
                username_hash TEXT NOT NULL,
                attempted_at INTEGER NOT NULL
            );

            CREATE TABLE IF NOT EXISTS registration_attempts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                ip_hash TEXT NOT NULL,
                attempted_at INTEGER NOT NULL
            );

            CREATE INDEX IF NOT EXISTS idx_games_user ON games(user_id);
            CREATE INDEX IF NOT EXISTS idx_admin_logs_created
                ON admin_logs(created_at DESC);
            CREATE INDEX IF NOT EXISTS idx_users_best
                ON users(best_score DESC);
            CREATE INDEX IF NOT EXISTS idx_user_sessions_user
                ON user_sessions(user_id);
            CREATE INDEX IF NOT EXISTS idx_user_sessions_expiry
                ON user_sessions(expires_at);
            CREATE INDEX IF NOT EXISTS idx_auth_attempts_ip_time
                ON auth_attempts(ip_hash, attempted_at);
            CREATE INDEX IF NOT EXISTS idx_auth_attempts_pair_time
                ON auth_attempts(ip_hash, username_hash, attempted_at);
            CREATE INDEX IF NOT EXISTS idx_auth_attempts_time
                ON auth_attempts(attempted_at);
            CREATE INDEX IF NOT EXISTS idx_registration_attempts_ip_time
                ON registration_attempts(ip_hash, attempted_at);
            CREATE INDEX IF NOT EXISTS idx_registration_attempts_time
                ON registration_attempts(attempted_at);
        """)

        games_columns = {
            row[1] for row in db.execute("PRAGMA table_info(games)")
        }

        if "max_score" not in games_columns:
            db.execute(
                "ALTER TABLE games ADD COLUMN max_score INTEGER NOT NULL DEFAULT 0"
            )
            db.execute(
                "UPDATE games SET max_score=rounds * 2 WHERE max_score=0"
            )

        if "errors" not in games_columns:
            db.execute("ALTER TABLE games ADD COLUMN errors TEXT DEFAULT '[]'")

        user_columns = {
            row[1] for row in db.execute("PRAGMA table_info(users)")
        }

        if "username_key" not in user_columns:
            db.execute("ALTER TABLE users ADD COLUMN username_key TEXT")

        # Старые аккаунты администраторов переносятся в новую систему ролей.
        if "role" not in user_columns:
            db.execute(
                "ALTER TABLE users ADD COLUMN role TEXT NOT NULL DEFAULT 'user' "
                "CHECK (role IN ('user', 'admin', 'owner'))"
            )
            db.execute(
                """
                UPDATE users
                SET role = CASE WHEN is_admin=1 THEN 'admin' ELSE 'user' END
                """
            )

        # Нормализуем имена в Unicode casefold, чтобы регистр не создавал дубликаты.
        normalized_names = {}

        for user_row in db.execute(
            "SELECT id, username FROM users"
        ).fetchall():
            username_key = normalize_username(user_row["username"])
            previous_id = normalized_names.get(username_key)

            if previous_id is not None:
                raise RuntimeError(
                    "В базе есть имена пользователей, совпадающие после Unicode "
                    "NFKC-нормализации. "
                    f"Разберите конфликт ID {previous_id} и {user_row['id']} перед запуском."
                )

            normalized_names[username_key] = user_row["id"]
            db.execute(
                "UPDATE users SET username_key=? WHERE id=?",
                (username_key, user_row["id"]),
            )

        db.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS idx_users_username_key "
            "ON users(username_key)"
        )

        if "timezone" not in user_columns:
            db.execute(
                "ALTER TABLE users ADD COLUMN timezone TEXT NOT NULL "
                "DEFAULT 'Europe/Moscow'"
            )

        # Сохраняем совместимость старого is_admin с новой ролью.
        db.execute(
            """
            UPDATE users
            SET is_admin = CASE
                WHEN role IN ('admin', 'owner') THEN 1
                ELSE 0
            END
            """
        )

        # Старый код создавал общедоступную учётную запись admin/admin.
        # При обнаружении такого хеша запуск разрешён только после явной смены пароля.
        admin_password = os.environ.get("ADMIN_PASSWORD", "")

        weak_admins = db.execute(
            "SELECT id, password_hash FROM users WHERE is_admin=1"
        ).fetchall()

        for admin_row in weak_admins:
            try:
                is_default_password = check_password_hash(
                    admin_row["password_hash"], "admin"
                )
            except (TypeError, ValueError):
                is_default_password = False

            if is_default_password:
                if not (
                    PASSWORD_MIN_LENGTH <= len(admin_password) <= PASSWORD_MAX_LENGTH
                ):
                    raise RuntimeError(
                        "Обнаружен старый аккаунт с паролем admin. "
                        "Задайте ADMIN_PASSWORD длиной не менее 12 символов и перезапустите сайт."
                    )

                db.execute(
                    "UPDATE users SET password_hash=? WHERE id=?",
                    (generate_password_hash(admin_password), admin_row["id"]),
                )
                db.execute(
                    "DELETE FROM user_sessions WHERE user_id=?",
                    (admin_row["id"],),
                )

        admins_count = db.execute(
            "SELECT COUNT(*) FROM users WHERE is_admin=1"
        ).fetchone()[0]

        if admins_count == 0 and admin_password:
            if not (
                PASSWORD_MIN_LENGTH <= len(admin_password) <= PASSWORD_MAX_LENGTH
            ):
                raise RuntimeError(
                    "ADMIN_PASSWORD должен содержать от 12 до 1024 символов."
                )

            username = os.environ.get("ADMIN_USERNAME", "admin").strip()

            if not 3 <= len(username) <= 24 or not all(
                char.isalnum() or char in "_-" for char in username
            ):
                raise RuntimeError(
                    "ADMIN_USERNAME должен содержать 3–24 допустимых символа."
                )

            try:
                db.execute(
                    """
                    INSERT INTO users (
                        username, username_key, password_hash, is_admin, role
                    )
                    VALUES (?, ?, ?, 1, 'admin')
                    """,
                    (
                        username,
                        normalize_username(username),
                        generate_password_hash(admin_password),
                    ),
                )
            except sqlite3.IntegrityError as exc:
                raise RuntimeError(
                    "ADMIN_USERNAME уже занят. Выберите другое имя через ADMIN_USERNAME."
                ) from exc

        elif admins_count == 0 and os.environ.get(
            "APP_ENV", "production"
        ).strip().lower() != "development":
            raise RuntimeError(
                "В production нет администратора. Задайте ADMIN_PASSWORD "
                "(не короче 12 символов) и при необходимости ADMIN_USERNAME."
            )

        elif admins_count == 0:
            warnings.warn(
                "В базе нет администратора. Задайте ADMIN_PASSWORD (и при необходимости "
                "ADMIN_USERNAME), чтобы создать административную учётную запись.",
                RuntimeWarning,
            )

        # При первом переходе на новую систему ролей выбираем владельца.
        # Можно явно указать существующий аккаунт через OWNER_USERNAME.
        owner_count = db.execute(
            "SELECT COUNT(*) FROM users WHERE role='owner'"
        ).fetchone()[0]

        if owner_count > 1:
            raise RuntimeError(
                "В базе найдено несколько владельцев. Оставьте только одну роль owner."
            )

        if owner_count == 0:
            owner_username = os.environ.get("OWNER_USERNAME", "").strip()

            if owner_username:
                owner = db.execute(
                    "SELECT id FROM users WHERE username_key=?",
                    (normalize_username(owner_username),),
                ).fetchone()

                if owner is None:
                    raise RuntimeError(
                        "OWNER_USERNAME не найден в базе. Укажите существующее имя пользователя."
                    )
            else:
                owner = db.execute(
                    """
                    SELECT id
                    FROM users
                    WHERE is_admin=1
                    ORDER BY id
                    LIMIT 1
                    """
                ).fetchone()

            if owner is not None:
                db.execute(
                    "UPDATE users SET role='owner', is_admin=1 WHERE id=?",
                    (owner["id"],),
                )

        # Роль owner должна встречаться не более одного раза.
        db.execute(
            """
            CREATE UNIQUE INDEX IF NOT EXISTS idx_users_single_owner
            ON users(role)
            WHERE role='owner'
            """
        )

        # is_admin остаётся для совместимости с остальными модулями проекта.
        db.execute(
            """
            UPDATE users
            SET is_admin = CASE
                WHEN role IN ('admin', 'owner') THEN 1
                ELSE 0
            END
            """
        )

        db.commit()

    except Exception:
        db.rollback()
        raise
    finally:
        db.close()