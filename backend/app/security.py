"""Senhas, tokens de sessão e criptografia dos segredos guardados no banco."""

from __future__ import annotations

import base64
import hashlib
import hmac
import secrets

from cryptography.fernet import Fernet, InvalidToken

_SCRYPT_N = 2**14
_SCRYPT_R = 8
_SCRYPT_P = 1

MIN_PASSWORD_LENGTH = 10


def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    digest = hashlib.scrypt(password.encode("utf-8"), salt=salt, n=_SCRYPT_N, r=_SCRYPT_R, p=_SCRYPT_P, dklen=32)
    return "scrypt${}${}${}${}${}".format(
        _SCRYPT_N,
        _SCRYPT_R,
        _SCRYPT_P,
        base64.b64encode(salt).decode(),
        base64.b64encode(digest).decode(),
    )


def verify_password(password: str, stored: str) -> bool:
    try:
        scheme, n, r, p, salt_b64, digest_b64 = stored.split("$")
        if scheme != "scrypt":
            return False
        salt = base64.b64decode(salt_b64)
        expected = base64.b64decode(digest_b64)
        digest = hashlib.scrypt(
            password.encode("utf-8"), salt=salt, n=int(n), r=int(r), p=int(p), dklen=len(expected)
        )
    except (ValueError, TypeError):
        return False
    return hmac.compare_digest(digest, expected)


def password_problem(password: str) -> str | None:
    if len(password) < MIN_PASSWORD_LENGTH:
        return f"A senha precisa ter pelo menos {MIN_PASSWORD_LENGTH} caracteres."
    if password.isdigit() or password.isalpha():
        return "Use letras e números (ou símbolos) na senha."
    return None


def new_token() -> str:
    return secrets.token_urlsafe(32)


def token_hash(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


class SecretBox:
    """Criptografa segredos com uma chave derivada de MB_SECRET_KEY (Fernet)."""

    def __init__(self, secret_key: str):
        raw = hashlib.sha256(("meta-bot:" + secret_key).encode("utf-8")).digest()
        self._fernet = Fernet(base64.urlsafe_b64encode(raw))

    def encrypt(self, value: str) -> str:
        return self._fernet.encrypt(value.encode("utf-8")).decode("ascii")

    def decrypt(self, token: str) -> str | None:
        if not token:
            return None
        try:
            return self._fernet.decrypt(token.encode("ascii")).decode("utf-8")
        except (InvalidToken, ValueError):
            return None


def mask(value: str | None) -> str:
    if not value:
        return ""
    if len(value) <= 8:
        return "••••"
    return f"{value[:4]}••••{value[-4:]}"


_box: SecretBox | None = None


def set_secret_box(box: SecretBox) -> None:
    global _box
    _box = box


def box() -> SecretBox:
    if _box is None:
        raise RuntimeError("SecretBox não inicializado")
    return _box
