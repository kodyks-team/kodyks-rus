import os
import unicodedata

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(BASE_DIR, "game.db")



def normalize_username(value):
    """Нормализует имя пользователя для стабильного сравнения в Unicode."""
    if not isinstance(value, str):
        return ""
    return unicodedata.normalize("NFKC", value.strip()).casefold()

# --- Брендинг и названия ---
APP_NAME = "Код Языка"
APP_BRAND_HTML = "Код<span>·</span>Языка"
APP_FOOTER = "Код Языка · Разработано kodyks · tg: @kodyks"

# --- Названия модулей ---
MODULE_SPELLING_NAME = "Словарные слова"
MODULE_SPELLING_DESC = (
    "Тебе показывают слово. Иногда оно написано верно, иногда — с ошибкой. "
    "Скажи, правильно ли оно, а если нет — введи верный вариант с первой попытки."
)

# --- Настройки игры ---
NO_REPEAT_WINDOW = 15
ROUNDS_OPTIONS = (10, 15, 20, 25, 30, 35, 40, 50)

# --- Настройки темы ---
THEME_COOKIE = "theme"
THEME_COOKIE_MAX_AGE = 60 * 60 * 24 * 365  # 1 год

# --- Настройки безопасности ---
SESSION_LIFETIME_SECONDS = 60 * 60 * 24 * 7  # 7 дней
ADMIN_SESSION_LIFETIME_SECONDS = 60 * 60 * 8  # 8 часов
PASSWORD_MIN_LENGTH = 12
PASSWORD_MAX_LENGTH = 1024
LOGIN_RATE_WINDOW_SECONDS = 15 * 60
LOGIN_RATE_LIMIT_PAIR = 8
LOGIN_RATE_LIMIT_IP = 40
REGISTER_RATE_WINDOW_SECONDS = 60 * 60
REGISTER_RATE_LIMIT_IP = 5
