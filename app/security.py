"""Password hashes and request CSRF. Cookies are signed by SessionMiddleware."""
import base64
import hashlib
import hmac
import secrets
from fastapi import HTTPException, Request

ROUNDS = 600_000


def password_hash(password: str) -> str:
    if len(password) < 10 or len(password) > 256:
        raise ValueError('Пароль должен содержать от 10 до 256 символов')
    salt = secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac('sha256', password.encode('utf-8'), salt, ROUNDS)
    return 'pbkdf2_sha256${}${}${}'.format(
        ROUNDS,
        base64.urlsafe_b64encode(salt).decode('ascii'),
        base64.urlsafe_b64encode(digest).decode('ascii'),
    )


def check_password(password: str, encoded: str) -> bool:
    if len(password) > 256:
        return False
    try:
        algorithm, iterations, salt, digest = encoded.split('$')
        if algorithm != 'pbkdf2_sha256' or not (100_000 <= int(iterations) <= 1_000_000):
            return False
        output = hashlib.pbkdf2_hmac('sha256', password.encode('utf-8'), base64.urlsafe_b64decode(salt), int(iterations))
        return hmac.compare_digest(output, base64.urlsafe_b64decode(digest))
    except (ValueError, TypeError):
        return False


def get_csrf_token(request: Request) -> str:
    if 'csrf' not in request.session:
        request.session['csrf'] = secrets.token_urlsafe(32)
    return request.session['csrf']


def require_csrf(request: Request, token: str):
    original = request.session.get('csrf', '')
    if not original or not hmac.compare_digest(original.encode('utf-8'), (token or '').encode('utf-8')):
        raise HTTPException(status_code=403, detail='Неверный защитный токен формы. Обновите страницу.')
