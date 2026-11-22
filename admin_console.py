"""Ограниченная командная консоль для административной панели."""

import shlex
import sqlite3

from config import normalize_username


HELP_TEXT = """ДОСТУПНЫЕ КОМАНДЫ

help                         Справка по командам
status                       Сводка по сайту и базе данных
users [количество]           Список пользователей (по умолчанию 10, максимум 50)
user <id|имя>                Карточка пользователя
games [количество]           Последние игры (по умолчанию 10, максимум 50)
db-check                     Проверка целостности базы данных
set-admin <id> on|off confirm Выдать или снять права администратора
reset-stats <id> confirm     Сбросить статистику пользователя
clear-game <id|all> confirm  Удалить активную игру одного или всех пользователей
delete-user <id> confirm     Удалить пользователя и связанные данные

Примеры:
  users 20
  user 12
  set-admin 12 on confirm
  reset-stats 12 confirm

Изменяющие команды требуют явного подтверждения.
Произвольный Python, shell и SQL не выполняются."""


ROLE_LABELS = {
    "owner": "владелец",
    "admin": "администратор",
    "user": "пользователь",
}


class ConsoleCommandError(ValueError):
    """Ошибка команды, которую можно безопасно показать администратору."""


def _user_id(value):
    """Проверяет и возвращает положительный идентификатор пользователя."""
    try:
        result = int(value)
    except (TypeError, ValueError):
        raise ConsoleCommandError(
            "ID пользователя должен быть целым числом."
        )

    if result < 1:
        raise ConsoleCommandError(
            "ID пользователя должен быть больше нуля."
        )

    return result


def _limit(value=None):
    """Ограничивает размер выдачи консоли."""
    if value is None:
        return 10

    try:
        result = int(value)
    except (TypeError, ValueError):
        raise ConsoleCommandError(
            "Количество должно быть целым числом от 1 до 50."
        )

    if not 1 <= result <= 50:
        raise ConsoleCommandError(
            "Количество должно быть от 1 до 50."
        )

    return result


def _get_user(db, value):
    """Ищет пользователя по ID или имени без динамического SQL."""
    if value.isdigit():
        return db.execute(
            """
            SELECT
                id, username, is_admin, role,
                best_score, correct_total, wrong_total, created_at
            FROM users
            WHERE id=?
            """,
            (_user_id(value),),
        ).fetchone()

    return db.execute(
        """
        SELECT
            id, username, is_admin, role,
            best_score, correct_total, wrong_total, created_at
        FROM users
        WHERE username_key=?
        """,
        (normalize_username(value),),
    ).fetchone()


def execute_admin_command(db, raw_command, actor_id):
    """Выполняет одну команду из безопасного белого списка.

    Функция не делает commit: транзакцией и записью аудита управляет маршрут.
    Возвращает пару (текст результата, признак ошибки).
    """
    if not isinstance(raw_command, str) or len(raw_command) > 200:
        return "ОШИБКА: команда должна содержать не более 200 символов.", True

    if any(ord(char) < 32 or ord(char) == 127 for char in raw_command):
        return "ОШИБКА: управляющие символы в команде запрещены.", True

    try:
        parts = shlex.split(raw_command.strip())
    except ValueError:
        return "ОШИБКА: проверьте кавычки в команде.", True

    if not parts:
        return "Введите команду. Для справки выполните: help", True

    command, *args = parts
    command = command.lower()

    try:
        if command == "help":
            if args:
                raise ConsoleCommandError(
                    "Команда help не принимает аргументы."
                )

            return HELP_TEXT, False

        if command == "status":
            if args:
                raise ConsoleCommandError(
                    "Команда status не принимает аргументы."
                )

            users_count = db.execute(
                "SELECT COUNT(*) FROM users"
            ).fetchone()[0]

            admins_count = db.execute(
                "SELECT COUNT(*) FROM users WHERE is_admin=1"
            ).fetchone()[0]

            games_count = db.execute(
                "SELECT COUNT(*) FROM games"
            ).fetchone()[0]

            active_count = db.execute(
                "SELECT COUNT(*) FROM active_games"
            ).fetchone()[0]

            last_game = db.execute(
                "SELECT played_at FROM games ORDER BY id DESC LIMIT 1"
            ).fetchone()

            return (
                "СОСТОЯНИЕ САЙТА\n"
                f"Пользователей:       {users_count}\n"
                f"Администраторов:     {admins_count}\n"
                f"Завершённых игр:      {games_count}\n"
                f"Активных игр:         {active_count}\n"
                f"Последняя игра:       "
                f"{last_game['played_at'] if last_game else 'нет данных'}"
            ), False

        if command == "users":
            if len(args) > 1:
                raise ConsoleCommandError(
                    "Формат: users [количество]"
                )

            limit = _limit(args[0] if args else None)

            rows = db.execute(
                """
                SELECT
                    u.id,
                    u.username,
                    u.role,
                    u.best_score,
                    COUNT(g.id) AS games_count
                FROM users AS u
                LEFT JOIN games AS g ON g.user_id=u.id
                GROUP BY u.id
                ORDER BY
                    CASE u.role
                        WHEN 'owner' THEN 0
                        WHEN 'admin' THEN 1
                        ELSE 2
                    END,
                    u.id
                LIMIT ?
                """,
                (limit,),
            ).fetchall()

            if not rows:
                return "Пользователей пока нет.", False

            lines = [
                "ID | ИМЯ | РОЛЬ | РЕКОРД | ИГР",
                "-" * 72,
            ]

            lines.extend(
                f"{row['id']} | {row['username']} | "
                f"{ROLE_LABELS.get(row['role'], 'неизвестно')} | "
                f"{row['best_score']} | {row['games_count']}"
                for row in rows
            )

            return "\n".join(lines), False

        if command == "user":
            if len(args) != 1:
                raise ConsoleCommandError(
                    "Формат: user <id|имя>"
                )

            user = _get_user(db, args[0])

            if user is None:
                raise ConsoleCommandError(
                    "Пользователь не найден."
                )

            games_count = db.execute(
                "SELECT COUNT(*) FROM games WHERE user_id=?",
                (user["id"],),
            ).fetchone()[0]

            active = db.execute(
                "SELECT updated_at FROM active_games WHERE user_id=?",
                (user["id"],),
            ).fetchone()

            return (
                "КАРТОЧКА ПОЛЬЗОВАТЕЛЯ\n"
                f"ID:                  {user['id']}\n"
                f"Имя:                 {user['username']}\n"
                f"Роль:                "
                f"{ROLE_LABELS.get(user['role'], 'неизвестно')}\n"
                f"Рекорд:              {user['best_score']}\n"
                f"Верных ответов:      {user['correct_total']}\n"
                f"Ошибок:              {user['wrong_total']}\n"
                f"Завершённых игр:     {games_count}\n"
                f"Активная игра:       "
                f"{active['updated_at'] if active else 'нет'}\n"
                f"Создан:              {user['created_at']}"
            ), False

        if command == "games":
            if len(args) > 1:
                raise ConsoleCommandError(
                    "Формат: games [количество]"
                )

            limit = _limit(args[0] if args else None)

            rows = db.execute(
                """
                SELECT
                    g.id,
                    u.username,
                    g.score,
                    g.max_score,
                    g.rounds,
                    g.correct,
                    g.wrong,
                    g.played_at
                FROM games AS g
                JOIN users AS u ON u.id=g.user_id
                ORDER BY g.id DESC
                LIMIT ?
                """,
                (limit,),
            ).fetchall()

            if not rows:
                return "Завершённых игр пока нет.", False

            lines = [
                "ID | ИГРОК | СЧЁТ | РАУНДЫ | ВЕРНО/ОШИБКИ | ДАТА",
                "-" * 76,
            ]

            lines.extend(
                f"{row['id']} | {row['username']} | "
                f"{row['score']}/{row['max_score']} | "
                f"{row['rounds']} | "
                f"{row['correct']}/{row['wrong']} | "
                f"{row['played_at']}"
                for row in rows
            )

            return "\n".join(lines), False

        if command == "db-check":
            if args:
                raise ConsoleCommandError(
                    "Команда db-check не принимает аргументы."
                )

            integrity = db.execute(
                "PRAGMA integrity_check"
            ).fetchone()[0]

            foreign_keys = db.execute(
                "PRAGMA foreign_key_check"
            ).fetchall()

            if integrity == "ok" and not foreign_keys:
                return (
                    "Проверка завершена: база данных цела, "
                    "нарушений внешних ключей нет."
                ), False

            issues = [f"Проверка целостности: {integrity}"]

            issues.extend(
                f"Нарушение внешнего ключа: таблица {row[0]}, rowid {row[1]}"
                for row in foreign_keys[:10]
            )

            return "\n".join(issues), True

        if command == "set-admin":
            if (
                len(args) != 3
                or args[1].lower() not in ("on", "off")
                or args[2].lower() != "confirm"
            ):
                raise ConsoleCommandError(
                    "Формат: set-admin <id> on|off confirm"
                )

            target_id = _user_id(args[0])

            if target_id == actor_id:
                raise ConsoleCommandError(
                    "Нельзя менять собственную роль из консоли."
                )

            target = db.execute(
                """
                SELECT id, username, is_admin, role
                FROM users
                WHERE id=?
                """,
                (target_id,),
            ).fetchone()

            if target is None:
                raise ConsoleCommandError(
                    "Пользователь не найден."
                )

            if target["role"] == "owner":
                raise ConsoleCommandError(
                    "Нельзя менять роль владельца."
                )

            desired_role = (
                "admin" if args[1].lower() == "on" else "user"
            )
            desired_admin = int(desired_role == "admin")

            if target["role"] == desired_role:
                return (
                    f"У пользователя {target['username']} "
                    f"уже установлена роль: {ROLE_LABELS[desired_role]}."
                ), False

            if not desired_admin and target["is_admin"]:
                admins_count = db.execute(
                    "SELECT COUNT(*) FROM users WHERE is_admin=1"
                ).fetchone()[0]

                if admins_count <= 1:
                    raise ConsoleCommandError(
                        "Нельзя снять права у последнего администратора."
                    )

            db.execute(
                """
                UPDATE users
                SET role=?, is_admin=?
                WHERE id=?
                """,
                (desired_role, desired_admin, target_id),
            )

            # Изменение роли отзывает все старые сессии пользователя.
            db.execute(
                "DELETE FROM user_sessions WHERE user_id=?",
                (target_id,),
            )

            return (
                f"Для {target['username']} установлена роль: "
                f"{ROLE_LABELS[desired_role]}."
            ), False

        if command == "reset-stats":
            if len(args) != 2 or args[1].lower() != "confirm":
                raise ConsoleCommandError(
                    "Формат: reset-stats <id> confirm"
                )

            target_id = _user_id(args[0])

            target = db.execute(
                "SELECT id, username FROM users WHERE id=?",
                (target_id,),
            ).fetchone()

            if target is None:
                raise ConsoleCommandError(
                    "Пользователь не найден."
                )

            db.execute(
                """
                UPDATE users
                SET best_score=0, correct_total=0, wrong_total=0
                WHERE id=?
                """,
                (target_id,),
            )

            db.execute(
                "DELETE FROM games WHERE user_id=?",
                (target_id,),
            )
            db.execute(
                "DELETE FROM active_games WHERE user_id=?",
                (target_id,),
            )

            return (
                f"Статистика пользователя {target['username']} сброшена, "
                "игры удалены."
            ), False

        if command == "clear-game":
            if len(args) != 2 or args[1].lower() != "confirm":
                raise ConsoleCommandError(
                    "Формат: clear-game <id|all> confirm"
                )

            if args[0].lower() == "all":
                cursor = db.execute(
                    "DELETE FROM active_games"
                )

                return (
                    f"Удалено активных игр: {cursor.rowcount}."
                ), False

            target_id = _user_id(args[0])

            target = db.execute(
                "SELECT username FROM users WHERE id=?",
                (target_id,),
            ).fetchone()

            if target is None:
                raise ConsoleCommandError(
                    "Пользователь не найден."
                )

            cursor = db.execute(
                "DELETE FROM active_games WHERE user_id=?",
                (target_id,),
            )

            if cursor.rowcount:
                return (
                    f"Активная игра пользователя {target['username']} удалена."
                ), False

            return (
                f"У пользователя {target['username']} нет активной игры."
            ), False

        if command == "delete-user":
            if len(args) != 2 or args[1].lower() != "confirm":
                raise ConsoleCommandError(
                    "Формат: delete-user <id> confirm"
                )

            target_id = _user_id(args[0])

            if target_id == actor_id:
                raise ConsoleCommandError(
                    "Нельзя удалить собственный аккаунт."
                )

            target = db.execute(
                """
                SELECT id, username, is_admin, role
                FROM users
                WHERE id=?
                """,
                (target_id,),
            ).fetchone()

            if target is None:
                raise ConsoleCommandError(
                    "Пользователь не найден."
                )

            if target["role"] == "owner":
                raise ConsoleCommandError(
                    "Нельзя удалить аккаунт владельца."
                )

            if target["is_admin"]:
                admins_count = db.execute(
                    "SELECT COUNT(*) FROM users WHERE is_admin=1"
                ).fetchone()[0]

                if admins_count <= 1:
                    raise ConsoleCommandError(
                        "Нельзя удалить последнего администратора."
                    )

            db.execute(
                "DELETE FROM users WHERE id=?",
                (target_id,),
            )

            return (
                f"Пользователь {target['username']} "
                "и связанные данные удалены."
            ), False

        raise ConsoleCommandError(
            f"Неизвестная команда: {command}. Выполните help для списка команд."
        )

    except ConsoleCommandError as exc:
        return f"ОШИБКА: {exc}", True

    except sqlite3.Error:
        raise