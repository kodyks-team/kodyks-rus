"""Интеграционные проверки защиты входа, cookie, CSRF и админ-маршрутов.

Запуск: python -m unittest discover -s tests -v
"""

import json
import os
import re
import secrets
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

os.environ["APP_ENV"] = "development"
_IMPORT_DB_DIR = tempfile.TemporaryDirectory()
os.environ["DATABASE_PATH"] = str(Path(_IMPORT_DB_DIR.name) / "import-only.db")
for _name in ("HTTPS", "COOKIE_SECURE", "ADMIN_PASSWORD", "ADMIN_USERNAME"):
    os.environ.pop(_name, None)

from werkzeug.security import generate_password_hash
import auth as auth_module
from app import create_app
from db import get_db
from words import new_game_state


CSRF_PATTERN = re.compile(
    r'<input[^>]+name="_csrf"[^>]+value="([^"]+)"', re.IGNORECASE
)


class SecurityIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.database = str(Path(self.temp_dir.name) / "test.db")
        self.app = create_app({
            "TESTING": True,
            "SECRET_KEY": "test-secret-key-that-is-at-least-32-bytes-long",
            "DATABASE": self.database,
            "LOGIN_RATE_LIMIT_PAIR": 3,
            "LOGIN_RATE_LIMIT_IP": 100,
            "TRUSTED_HOSTS": ["localhost", "127.0.0.1"],
        })
        self.client = self.app.test_client()
        with self.app.app_context():
            db = get_db()
            db.execute(
                "INSERT INTO users (username, username_key, password_hash, is_admin) "
                "VALUES (?, ?, ?, 1)",
                ("rootadmin", "rootadmin", generate_password_hash("Admin-Password-123!")),
            )
            db.execute(
                "INSERT INTO users (username, username_key, password_hash) VALUES (?, ?, ?)",
                ("tester", "tester", generate_password_hash("User-Password-123!")),
            )
            self.tester_id = db.execute(
                "SELECT id FROM users WHERE username_key='tester'"
            ).fetchone()[0]
            db.commit()

    def tearDown(self):
        self.temp_dir.cleanup()

    def _csrf(self, client, path="/login"):
        response = client.get(path)
        self.assertEqual(response.status_code, 200)
        match = CSRF_PATTERN.search(response.get_data(as_text=True))
        self.assertIsNotNone(match, "CSRF hidden field must be present")
        return match.group(1)

    def _login(self, client, username="rootadmin", password="Admin-Password-123!", query=""):
        token = self._csrf(client)
        return client.post(
            f"/login{query}",
            data={"_csrf": token, "username": username, "password": password},
            follow_redirects=False,
        )

    def test_password_hash_is_not_exposed_in_request_user_context(self):
        self._login(self.client, "tester", "User-Password-123!")
        captured = {}

        @self.app.before_request
        def capture_user_fields():
            from flask import g
            captured["fields"] = set(g.user.keys()) if g.user else set()

        self.client.get("/")
        self.assertNotIn("password_hash", captured.get("fields", set()))
        self.assertNotIn("username_key", captured.get("fields", set()))

    def test_login_sets_hardened_server_bound_cookie(self):
        response = self._login(self.client)
        self.assertEqual(response.status_code, 302)
        cookie_header = response.headers.get("Set-Cookie", "")
        self.assertIn("kodyazyka_session=", cookie_header)
        self.assertIn("HttpOnly", cookie_header)
        self.assertIn("SameSite=Lax", cookie_header)
        self.assertIn("Path=/", cookie_header)
        self.assertNotIn("Domain=", cookie_header)
        with self.client:
            self.client.get("/admin")
            self.assertTrue(self.client.get_cookie("kodyazyka_session").value)


    def test_login_rotates_anonymous_session_cookie(self):
        self._csrf(self.client)
        anonymous_cookie = self.client.get_cookie("kodyazyka_session")
        self.assertIsNotNone(anonymous_cookie)
        anonymous_value = anonymous_cookie.value

        response = self._login(self.client)
        self.assertEqual(response.status_code, 302)
        authenticated_cookie = self.client.get_cookie("kodyazyka_session")
        self.assertIsNotNone(authenticated_cookie)
        self.assertNotEqual(anonymous_value, authenticated_cookie.value)

        replay_client = self.app.test_client()
        replay_client.set_cookie("kodyazyka_session", anonymous_value, domain="localhost")
        self.assertEqual(replay_client.get("/admin").status_code, 403)

    def test_admin_session_has_shorter_server_expiry(self):
        admin_client = self.app.test_client()
        user_client = self.app.test_client()
        self._login(admin_client)
        self._login(user_client, "tester", "User-Password-123!")
        with self.app.app_context():
            db = get_db()
            expiry_by_user = {
                row["username"]: row["expires_at"] - row["created_at"]
                for row in db.execute(
                    """SELECT u.username, s.created_at, s.expires_at
                       FROM user_sessions AS s JOIN users AS u ON u.id=s.user_id"""
                ).fetchall()
            }
        self.assertEqual(expiry_by_user["rootadmin"], 8 * 60 * 60)
        self.assertEqual(expiry_by_user["tester"], 7 * 24 * 60 * 60)

    def test_tampered_cookie_does_not_authenticate(self):
        self._login(self.client)
        cookie = self.client.get_cookie("kodyazyka_session")
        self.assertIsNotNone(cookie)
        forged = ("A" if cookie.value[0] != "A" else "B") + cookie.value[1:]
        attacker = self.app.test_client()
        attacker.set_cookie("kodyazyka_session", forged, domain="localhost")
        response = attacker.get("/admin")
        self.assertEqual(response.status_code, 403)

    def test_stolen_cookie_is_revoked_after_logout(self):
        self._login(self.client)
        admin_page = self.client.get("/admin")
        self.assertEqual(admin_page.status_code, 200)
        token = CSRF_PATTERN.search(admin_page.get_data(as_text=True)).group(1)
        stolen_value = self.client.get_cookie("kodyazyka_session").value

        replay_client = self.app.test_client()
        replay_client.set_cookie("kodyazyka_session", stolen_value, domain="localhost")
        self.assertEqual(replay_client.get("/admin").status_code, 200)

        self.client.post("/logout", data={"_csrf": token})
        self.assertEqual(replay_client.get("/admin").status_code, 403)

    def test_csrf_and_cross_origin_requests_are_rejected(self):
        self._login(self.client)
        self.assertEqual(self.client.post("/admin/console", data={"command": "status"}).status_code, 400)
        token = self._csrf(self.client, "/admin")
        response = self.client.post(
            "/admin/console",
            data={"_csrf": token, "command": "status"},
            headers={"Origin": "https://evil.example"},
        )
        self.assertEqual(response.status_code, 400)
        response = self.client.post(
            "/admin/console",
            data={"_csrf": token, "command": "status"},
            headers={"Sec-Fetch-Site": "cross-site"},
        )
        self.assertEqual(response.status_code, 400)

    def test_non_admin_cannot_open_admin_panel(self):
        client = self.app.test_client()
        self._login(client, "tester", "User-Password-123!")
        self.assertEqual(client.get("/admin").status_code, 403)

    def test_login_next_does_not_allow_external_redirect(self):
        for next_value in (
            "https%3A%2F%2Fevil.example%2Fsteal",
            "%2F%252f%252fevil.example",
            "%2F%255c%255cevil.example",
        ):
            response = self._login(self.client, query=f"?next={next_value}")
            self.assertEqual(response.status_code, 302)
            self.assertEqual(response.headers["Location"], "/")
            # Успешная аутентификация оставляет клиент вошедшим, создаём новый клиент.
            self.client = self.app.test_client()

    def test_login_rate_limit_blocks_password_bruteforce(self):
        token = self._csrf(self.client)
        for _ in range(3):
            response = self.client.post(
                "/login",
                data={"_csrf": token, "username": "tester", "password": "wrong-password"},
            )
            self.assertEqual(response.status_code, 401)
        response = self.client.post(
            "/login",
            data={"_csrf": token, "username": "tester", "password": "wrong-password"},
        )
        self.assertEqual(response.status_code, 429)

    def test_username_unicode_normalization_prevents_duplicate_account(self):
        for username in ("Tester", "Ｔｅｓｔｅｒ"):
            with self.subTest(username=username):
                client = self.app.test_client()
                token = self._csrf(client, "/register")
                response = client.post(
                    "/register",
                    data={
                        "_csrf": token,
                        "username": username,
                        "password": "Another-Password-123!",
                        "password2": "Another-Password-123!",
                    },
                )
                self.assertEqual(response.status_code, 400)

    def test_security_headers_are_present(self):
        response = self.client.get("/")
        self.assertEqual(response.headers.get("X-Content-Type-Options"), "nosniff")
        self.assertEqual(response.headers.get("X-Frame-Options"), "DENY")
        self.assertIn("frame-ancestors 'none'", response.headers.get("Content-Security-Policy", ""))
        self.assertIn("no-store", response.headers.get("Cache-Control", ""))


    def test_password_reset_race_cannot_recreate_revoked_session(self):
        client = self.app.test_client()
        token = self._csrf(client)
        original_check = auth_module.check_password_hash

        def reset_password_during_hash_check(stored_hash, supplied_password):
            valid = original_check(stored_hash, supplied_password)
            db = get_db()
            db.execute(
                "UPDATE users SET password_hash=? WHERE username_key='tester'",
                (generate_password_hash("New-Password-After-Reset-987!"),),
            )
            db.execute(
                "DELETE FROM user_sessions WHERE user_id=?", (self.tester_id,)
            )
            db.commit()
            return valid

        with patch.object(
            auth_module,
            "check_password_hash",
            side_effect=reset_password_during_hash_check,
        ):
            response = client.post(
                "/login",
                data={
                    "_csrf": token,
                    "username": "tester",
                    "password": "User-Password-123!",
                },
            )
        self.assertEqual(response.status_code, 401)
        self.assertEqual(client.get("/play").status_code, 302)
        with self.app.app_context():
            sessions = get_db().execute(
                "SELECT COUNT(*) FROM user_sessions WHERE user_id=?", (self.tester_id,)
            ).fetchone()[0]
        self.assertEqual(sessions, 0)

    def test_password_change_revokes_existing_sessions(self):
        admin_client = self.app.test_client()
        self._login(admin_client)
        user_client = self.app.test_client()
        self._login(user_client, "tester", "User-Password-123!")
        self.assertEqual(user_client.get("/play").status_code, 200)

        admin_page = admin_client.get("/admin")
        token = CSRF_PATTERN.search(admin_page.get_data(as_text=True)).group(1)
        response = admin_client.post(
            "/admin/user/2/password",
            data={"_csrf": token, "new_password": "Changed-Password-456!"},
        )
        self.assertEqual(response.status_code, 302)
        self.assertEqual(user_client.get("/play").status_code, 302)

    def test_admin_console_does_not_execute_shell_or_sql(self):
        self._login(self.client)
        admin_page = self.client.get("/admin")
        token = CSRF_PATTERN.search(admin_page.get_data(as_text=True)).group(1)
        response = self.client.post(
            "/admin/console",
            data={"_csrf": token, "command": "__import__(\"os\").system(\"id\")"},
        )
        self.assertEqual(response.status_code, 302)
        with self.app.app_context():
            db = get_db()
            count = db.execute("SELECT COUNT(*) FROM users WHERE username_key='tester'").fetchone()[0]
            self.assertEqual(count, 1)
            output = db.execute("SELECT output FROM admin_logs ORDER BY id DESC LIMIT 1").fetchone()[0]
            self.assertIn("Неизвестная команда", output)

    def test_demotion_revokes_old_admin_cookie(self):
        self._login(self.client)
        admin_page = self.client.get("/admin")
        token = CSRF_PATTERN.search(admin_page.get_data(as_text=True)).group(1)
        with self.app.app_context():
            db = get_db()
            db.execute(
                "INSERT INTO users (username, username_key, password_hash, is_admin) VALUES (?, ?, ?, 1)",
                ("secondadmin", "secondadmin", generate_password_hash("Second-Admin-Password-123!")),
            )
            target_id = db.execute("SELECT id FROM users WHERE username_key='secondadmin'").fetchone()[0]
            db.commit()
        other_admin = self.app.test_client()
        self._login(other_admin, "secondadmin", "Second-Admin-Password-123!")
        stolen_value = other_admin.get_cookie("kodyazyka_session").value

        response = self.client.post(
            f"/admin/user/{target_id}/toggle_admin", data={"_csrf": token}
        )
        self.assertEqual(response.status_code, 302)
        replay = self.app.test_client()
        replay.set_cookie("kodyazyka_session", stolen_value, domain="localhost")
        self.assertEqual(replay.get("/admin").status_code, 403)


    def test_theme_redirect_rejects_multi_encoded_external_path(self):
        page = self.client.get("/")
        token = CSRF_PATTERN.search(page.get_data(as_text=True)).group(1)
        response = self.client.post(
            "/theme/toggle",
            data={"_csrf": token, "next": "/%252f%252fevil.example"},
        )
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.headers["Location"], "/")

    def test_production_uses_host_cookie_and_requires_strong_secret(self):
        with patch.dict(os.environ, {
            "APP_ENV": "production",
            "SECRET_KEY": secrets.token_hex(32),
            "TRUSTED_HOSTS": "localhost",
            "ADMIN_PASSWORD": "Production-Admin-Password-987!",
            "ADMIN_USERNAME": "admin",
        }, clear=False):
            os.environ.pop("DATABASE_PATH", None)
            production_app = create_app({
                "TESTING": True,
                "DATABASE": str(Path(self.temp_dir.name) / "production.db"),
            })
        self.assertTrue(production_app.config["SESSION_COOKIE_SECURE"])
        self.assertEqual(
            production_app.config["SESSION_COOKIE_NAME"],
            "__Host-kodyazyka_session",
        )
        client = production_app.test_client()
        login_page = client.get("/login", base_url="https://localhost")
        token = CSRF_PATTERN.search(login_page.get_data(as_text=True)).group(1)
        response = client.post(
            "/login",
            base_url="https://localhost",
            data={
                "_csrf": token,
                "username": "admin",
                "password": "Production-Admin-Password-987!",
            },
        )
        self.assertEqual(response.status_code, 302)
        cookie = response.headers.get("Set-Cookie", "")
        self.assertIn("__Host-kodyazyka_session=", cookie)
        self.assertIn("Secure", cookie)
        self.assertIn("HttpOnly", cookie)
        self.assertIn("SameSite=Lax", cookie)
        self.assertIn("Path=/", cookie)
        self.assertNotIn("Domain=", cookie)

    def test_production_rejects_wildcard_trusted_hosts(self):
        with patch.dict(os.environ, {
            "APP_ENV": "production",
            "SECRET_KEY": secrets.token_hex(32),
            "TRUSTED_HOSTS": "*",
            "ADMIN_PASSWORD": "Production-Admin-Password-987!",
        }, clear=False):
            with self.assertRaisesRegex(RuntimeError, "только точные имена хостов"):
                create_app({"TESTING": False})

    def test_production_rejects_short_secret_key(self):
        with patch.dict(os.environ, {
            "APP_ENV": "production",
            "SECRET_KEY": "too-short-secret-key-32-characters",
            "TRUSTED_HOSTS": "localhost",
        }, clear=False):
            with self.assertRaisesRegex(RuntimeError, "64 байт"):
                create_app({"TESTING": False})

    def test_production_rejects_low_entropy_secret_key(self):
        predictable_keys = ("a" * 64, ("AbcdefGhijkL" * 5) + "Abcd")
        for weak_key in predictable_keys:
            with self.subTest(secret=weak_key[:12]):
                with patch.dict(os.environ, {
                    "APP_ENV": "production",
                    "SECRET_KEY": weak_key,
                    "TRUSTED_HOSTS": "localhost",
                }, clear=False):
                    with self.assertRaisesRegex(RuntimeError, "предсказуемый"):
                        create_app({"TESTING": False})

    def test_external_scripts_are_restricted_to_stats_and_use_sri(self):
        home = self.client.get("/")
        home_csp = home.headers.get("Content-Security-Policy", "")
        self.assertIn("script-src 'self'", home_csp)
        self.assertNotIn("cdn.jsdelivr.net", home_csp)
        self._login(self.client, "tester", "User-Password-123!")
        stats = self.client.get("/stats")
        stats_csp = stats.headers.get("Content-Security-Policy", "")
        html = stats.get_data(as_text=True)
        self.assertIn("cdn.jsdelivr.net", stats_csp)
        self.assertIn('integrity="sha256-0q+JdOlScWOHcunpUk21uab1jW7C1deBQARHtKMcaB4="', html)
        self.assertIn('crossorigin="anonymous"', html)
        self.assertNotIn("hammerjs", html)
        self.assertNotIn("chartjs-plugin-zoom", html)


    def test_production_rejects_insecure_cookie_overrides(self):
        for override in (
            {"SESSION_COOKIE_HTTPONLY": False},
            {"SESSION_COOKIE_SAMESITE": "None"},
            {"SESSION_COOKIE_PATH": "/other"},
            {"SESSION_COOKIE_DOMAIN": "localhost"},
        ):
            with self.subTest(override=override):
                with patch.dict(os.environ, {
                    "APP_ENV": "production",
                    "SECRET_KEY": secrets.token_hex(32),
                    "TRUSTED_HOSTS": "localhost",
                    "ADMIN_PASSWORD": "Production-Admin-Password-987!",
                    "ADMIN_USERNAME": "admin",
                }, clear=False):
                    with self.assertRaises(RuntimeError):
                        create_app({"TESTING": True, **override})

    def test_untrusted_host_is_rejected_when_configured(self):
        response = self.client.get("/", headers={"Host": "attacker.invalid"})
        self.assertEqual(response.status_code, 400)

    def test_game_get_does_not_advance_state_and_post_requires_csrf(self):
        self._login(self.client, "tester", "User-Password-123!")
        with self.app.app_context():
            db = get_db()
            state = new_game_state(None, 10)
            first = state["rounds"][0]
            first["shown_is_correct"] = True
            first["guessed"] = True
            first["actions_correct"] = 1
            state["score"] = 1
            db.execute(
                "INSERT INTO active_games (user_id, state) VALUES (?, ?)",
                (self.tester_id, json.dumps(state, ensure_ascii=False)),
            )
            db.commit()
            before = db.execute(
                "SELECT state FROM active_games WHERE user_id=?", (self.tester_id,)
            ).fetchone()[0]

        page = self.client.get("/game")
        self.assertEqual(page.status_code, 200)
        self.assertIn("Следующее слово", page.get_data(as_text=True))
        with self.app.app_context():
            after = get_db().execute(
                "SELECT state FROM active_games WHERE user_id=?", (self.tester_id,)
            ).fetchone()[0]
            self.assertEqual(before, after)

        self.assertEqual(self.client.post("/game/advance").status_code, 400)
        token = CSRF_PATTERN.search(page.get_data(as_text=True)).group(1)
        response = self.client.post("/game/advance", data={"_csrf": token})
        self.assertEqual(response.status_code, 302)
        with self.app.app_context():
            updated = json.loads(get_db().execute(
                "SELECT state FROM active_games WHERE user_id=?", (self.tester_id,)
            ).fetchone()[0])
            self.assertEqual(updated["current"], 1)


if __name__ == "__main__":
    unittest.main()
