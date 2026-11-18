import os

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(BASE_DIR, "game.db")

# Не повторять слово, если оно встречалось в последних N раундах
NO_REPEAT_WINDOW = 15

# Допустимые варианты количества слов за игру
ROUNDS_OPTIONS = (10, 15, 20, 25, 30, 40, 50)

# Тема
THEME_COOKIE = "theme"
THEME_COOKIE_MAX_AGE = 60 * 60 * 24 * 365  # 1 год