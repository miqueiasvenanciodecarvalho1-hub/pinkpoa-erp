"""Senhas (PBKDF2-SHA256) e tokens de acesso assinados (JWT HS256), só com a biblioteca padrão."""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import secrets
import time
from typing import Any

from finance_engine.core.config import get_settings

_ITERATIONS = 240_000


def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, _ITERATIONS)
    return f"pbkdf2_sha256${_ITERATIONS}${salt.hex()}${digest.hex()}"


def verify_password(password: str, stored: str) -> bool:
    try:
        _, iterations, salt, digest = stored.split("$")
    except ValueError:
        return False
    candidate = hashlib.pbkdf2_hmac(
        "sha256", password.encode(), bytes.fromhex(salt), int(iterations)
    )
    return hmac.compare_digest(candidate.hex(), digest)


def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def _unb64(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


class InvalidToken(Exception):
    pass


def create_token(claims: dict[str, Any], ttl_minutes: int | None = None) -> str:
    settings = get_settings()
    now = int(time.time())
    payload = {**claims, "iat": now, "exp": now + 60 * (ttl_minutes or settings.token_ttl_minutes)}
    head = _b64(json.dumps({"alg": "HS256", "typ": "JWT"}).encode())
    body = _b64(json.dumps(payload, separators=(",", ":")).encode())
    sig = hmac.new(settings.secret_key.encode(), f"{head}.{body}".encode(), hashlib.sha256).digest()
    return f"{head}.{body}.{_b64(sig)}"


def decode_token(token: str) -> dict[str, Any]:
    try:
        head, body, sig = token.split(".")
    except ValueError as exc:
        raise InvalidToken("token malformado") from exc
    expected = hmac.new(
        get_settings().secret_key.encode(), f"{head}.{body}".encode(), hashlib.sha256
    ).digest()
    if not hmac.compare_digest(_b64(expected), sig):
        raise InvalidToken("assinatura inválida")
    payload: dict[str, Any] = json.loads(_unb64(body))
    if payload.get("exp", 0) < time.time():
        raise InvalidToken("token expirado")
    return payload
