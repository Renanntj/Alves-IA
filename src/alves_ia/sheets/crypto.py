from __future__ import annotations

import base64
import binascii
import secrets

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

_NONCE_LENGTH = 12
_TAG_LENGTH = 16
_AAD = b"alves-ia/google-sheets-refresh-token/v1"


def _key_bytes(encoded_key: str) -> bytes:
    try:
        key = base64.b64decode(encoded_key, altchars=b"-_", validate=True)
    except (ValueError, binascii.Error) as exc:
        raise ValueError("Google Sheets encryption key must be valid base64") from exc
    if len(key) != 32:
        raise ValueError("Google Sheets encryption key must decode to 32 bytes")
    return key


def encrypt_refresh_token(refresh_token: str, encoded_key: str) -> bytes:
    if not refresh_token:
        raise ValueError("Refresh token cannot be empty")
    nonce = secrets.token_bytes(_NONCE_LENGTH)
    ciphertext = AESGCM(_key_bytes(encoded_key)).encrypt(nonce, refresh_token.encode("utf-8"), _AAD)
    return nonce + ciphertext


def decrypt_refresh_token(ciphertext: bytes, encoded_key: str) -> str:
    if len(ciphertext) < _NONCE_LENGTH + _TAG_LENGTH:
        raise ValueError("Encrypted refresh token is malformed")
    nonce, encrypted = ciphertext[:_NONCE_LENGTH], ciphertext[_NONCE_LENGTH:]
    try:
        value = AESGCM(_key_bytes(encoded_key)).decrypt(nonce, encrypted, _AAD)
    except InvalidTag as exc:
        raise ValueError("Unable to decrypt stored Google Sheets token") from exc
    return value.decode("utf-8")
