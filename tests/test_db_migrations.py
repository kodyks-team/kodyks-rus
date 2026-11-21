"""Проверки миграции старой SQLite-базы к актуальной схеме."""

import os
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from werkzeug.security import check_password_hash, generate_password_hash

from db import init_db


class DatabaseMigrationTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.path = str(Path(self.temp_dir.name) / "legacy.db")

    def tearDown(self):
        self.temp_dir.cleanup()

    def _create_legacy_database(self, *, admin=False):
        db = sqlite3.connect(self.path)
        db.executescript("""
            CREATE TABLE users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                username TEXT UNIQUE NOT NULL,
                password_hash TEXT NOT NULL,
                is_admin INTEGER NOT NULL DEFAULT 0,
                best_score INTEGER NOT NULL DEFAULT 0,
                correct_total INTEGER NOT NULL DEFAULT 0,
                wrong_total INTEGER NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            );
            CREATE TABLE games (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                score INTEGER NOT NULL,
                rounds INTEGER NOT NULL,
                correct INTEGER NOT NULL,
                wrong INTEGER NOT NULL,
                played_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            );
        """)
        db.execute(
            "INSERT INTO users (username, password_hash, is_admin) VALUES (?, ?, ?)",
            ("Admin" if admin else "Tester", generate_password_hash("admin" if admin else "test-pass"), int(admin)),
        )
        db.execute(
            "INSERT INTO games (user_id, score, rounds, correct, wrong) VALUES (1, 10, 10, 5, 5)"
        )
        db.commit()
        db.close()

    def test_migrates_old_schema_without_losing_games(self):
        self._create_legacy_database()
        with patch.dict(os.environ, {"APP_ENV": "development"}, clear=False):
            os.environ.pop("ADMIN_PASSWORD", None)
            init_db(self.path)
        db = sqlite3.connect(self.path)
        db.row_factory = sqlite3.Row
        user = db.execute("SELECT * FROM users WHERE id=1").fetchone()
        game = db.execute("SELECT * FROM games WHERE id=1").fetchone()
        table_names = {
            row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")
        }
        indexes = {
            row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='index'")
        }
        db.close()
        if os.name == "posix":
            self.assertEqual(os.stat(self.path).st_mode & 0o777, 0o600)
        self.assertEqual(user["username_key"], "tester")
        self.assertEqual(user["timezone"], "Europe/Moscow")
        self.assertEqual(game["max_score"], 20)
        self.assertEqual(game["errors"], "[]")
        self.assertIn("user_sessions", table_names)
        self.assertIn("auth_attempts", table_names)
        self.assertIn("idx_users_username_key", indexes)

    def test_replaces_legacy_admin_admin_password(self):
        self._create_legacy_database(admin=True)
        strong_password = "New-Admin-Password-987!"
        with patch.dict(os.environ, {"APP_ENV": "development", "ADMIN_PASSWORD": strong_password}, clear=False):
            init_db(self.path)
        db = sqlite3.connect(self.path)
        password_hash = db.execute("SELECT password_hash FROM users WHERE id=1").fetchone()[0]
        db.close()
        self.assertTrue(check_password_hash(password_hash, strong_password))
        self.assertFalse(check_password_hash(password_hash, "admin"))

    def test_refuses_to_keep_legacy_admin_admin_password_without_override(self):
        self._create_legacy_database(admin=True)
        with patch.dict(os.environ, {"APP_ENV": "development"}, clear=False):
            os.environ.pop("ADMIN_PASSWORD", None)
            with self.assertRaisesRegex(RuntimeError, "ADMIN_PASSWORD"):
                init_db(self.path)


    def test_nfkc_compatible_names_are_rejected_as_duplicates(self):
        self._create_legacy_database()
        db = sqlite3.connect(self.path)
        db.execute(
            "INSERT INTO users (username, password_hash) VALUES (?, ?)",
            ("Ｔｅｓｔｅｒ", generate_password_hash("another-pass")),
        )
        db.commit()
        db.close()
        with patch.dict(os.environ, {"APP_ENV": "development"}, clear=False):
            os.environ.pop("ADMIN_PASSWORD", None)
            with self.assertRaisesRegex(RuntimeError, "совпадающие после Unicode NFKC-нормализации"):
                init_db(self.path)


    @unittest.skipUnless(hasattr(os, "O_NOFOLLOW"), "O_NOFOLLOW доступен только на поддерживаемых ОС")
    def test_refuses_symbolic_link_database_path(self):
        target = Path(self.temp_dir.name) / "real.db"
        link = Path(self.temp_dir.name) / "linked.db"
        target.touch()
        link.symlink_to(target)
        with self.assertRaisesRegex(RuntimeError, "Небезопасный путь базы данных"):
            init_db(str(link))

    @unittest.skipUnless(os.name == "posix", "Проверка прав каталога требует POSIX")
    def test_refuses_group_or_world_writable_database_directory(self):
        directory = Path(self.temp_dir.name) / "public"
        directory.mkdir()
        try:
            for mode in (0o770, 0o777):
                with self.subTest(mode=oct(mode)):
                    directory.chmod(mode)
                    with self.assertRaisesRegex(RuntimeError, "Небезопасный путь базы данных"):
                        init_db(str(directory / "site.db"))
        finally:
            directory.chmod(0o700)

    def test_casefold_duplicate_names_stop_migration(self):
        self._create_legacy_database()
        db = sqlite3.connect(self.path)
        db.execute(
            "INSERT INTO users (username, password_hash) VALUES (?, ?)",
            ("TESTER", generate_password_hash("another-pass")),
        )
        db.commit()
        db.close()
        with patch.dict(os.environ, {"APP_ENV": "development"}, clear=False):
            os.environ.pop("ADMIN_PASSWORD", None)
            with self.assertRaisesRegex(RuntimeError, "совпадающие после Unicode NFKC-нормализации"):
                init_db(self.path)


if __name__ == "__main__":
    unittest.main()
