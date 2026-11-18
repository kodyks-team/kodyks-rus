import secrets
import string


def generate_password(length: int = 12, use_digits: bool = True,
                      use_symbols: bool = True, use_uppercase: bool = True) -> str:
    """Генерирует пароль заданной длины."""
    if length < 4:
        raise ValueError("Длина пароля должна быть не менее 4 символов")

    # Базовый набор — строчные буквы
    alphabet = string.ascii_lowercase
    # Обязательные символы (по одному из каждой категории)
    required = [secrets.choice(string.ascii_lowercase)]

    if use_uppercase:
        alphabet += string.ascii_uppercase
        required.append(secrets.choice(string.ascii_uppercase))
    if use_digits:
        alphabet += string.digits
        required.append(secrets.choice(string.digits))
    if use_symbols:
        symbols = "!@#$%^&*()-_=+[]{};:,.<>?/"
        alphabet += symbols
        required.append(secrets.choice(symbols))

    if len(required) > length:
        raise ValueError("Длина слишком мала для выбранных категорий символов")

    # Остальные символы
    rest = [secrets.choice(alphabet) for _ in range(length - len(required))]
    password = required + rest

    # Перемешиваем, чтобы обязательные символы не стояли в начале
    secrets.SystemRandom().shuffle(password)
    return "".join(password)


if __name__ == "__main__":
    length = int(input("Введите длину пароля: "))
    print("Пароль:", generate_password(length))