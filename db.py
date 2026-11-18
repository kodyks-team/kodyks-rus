import os
import sqlite3
from flask import g
from werkzeug.security import generate_password_hash

from config import DB_PATH


def get_db():
    """Возвращает соединение с БД. Если его нет в контексте запроса (g),
    создаёт новое. Настраивает возврат строк как словарей (sqlite3.Row) 
    и включает поддержку внешних ключей."""

    if "db" not in g:
        g.db = sqlite3.connect(DB_PATH)
        g.db.row_factory = sqlite3.Row
        g.db.execute("PRAGMA foreign_keys = ON")
    return g.db


def close_db(exc=None):
    """Закрывает соединение с БД, если оно было открыто в текущем запросе.
    Вызывается автоматически Flask'ом при завершении запроса.
    Параметр exc — это исключение, если запрос завершился с ошибкой (не используется)."""

    db = g.pop("db", None)
    if db is not None:
        db.close()


def init_db():
    """Инициализирует БД: создаёт таблицы, если их нет, применяет миграции
    (добавляет колонку max_score, если её нет) и создаёт дефолтного админа."""

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