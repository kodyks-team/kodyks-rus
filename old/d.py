import secrets
import string

def generate_secret_key(length=64):
    """Генерирует криптографически стойкий секретный ключ."""
    # secrets.token_hex возвращает строку в hex-формате (0-9, a-f)
    return secrets.token_hex(length // 2)

def generate_admin_password(length=64):
    """Генерирует надежный пароль для админа (буквы + цифры)."""
    alphabet = string.ascii_letters + string.digits
    return ''.join(secrets.choice(alphabet) for _ in range(length))

if __name__ == "__main__":
    secret_key = generate_secret_key()
    admin_password = generate_admin_password()

    print("=" * 60)
    print("ВАШИ НОВЫЕ СЕКРЕТНЫЕ ДАННЫЕ")
    print("Вставьте эти строки в wsgi.py на PythonAnywhere (и локально тоже):")
    print("=" * 60)
    print()
    print(f'os.environ["SECRET_KEY"] = "{secret_key}"')
    print(f'os.environ["ADMIN_PASSWORD"] = "{admin_password}"')
    print()
    print("=" * 60)
    print("⚠️  НЕ КОММИТЬТЕ ЭТИ ЗНАЧЕНИЯ В GITHUB!")
    print("⚠️  СОХРАНИТЕ ИХ В НАДЕЖНОМ МЕСТЕ (например, в менеджере паролей)")
    print("=" * 60)