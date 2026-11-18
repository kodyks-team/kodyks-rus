import os

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(BASE_DIR, "game.db")

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