from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import jwt
from pwdlib import PasswordHash
from pydantic import BaseModel

from .config import Settings

password_hasher = PasswordHash.recommended()
DUMMY_HASH = password_hasher.hash("dummy-password-for-timing-equalization")


class Identity(BaseModel):
    user_id: UUID
    clinic_id: UUID


def signing_key(settings: Settings) -> str:
    key = settings.jwt_secret.get_secret_value()
    if len(key.encode()) < 32:
        raise RuntimeError("JWT_SECRET deve ter pelo menos 32 bytes aleatórios")
    return key


def issue_token(identity: Identity, settings: Settings) -> str:
    now = datetime.now(UTC)
    return jwt.encode(
        {
            "sub": str(identity.user_id),
            "clinic_id": str(identity.clinic_id),
            "iat": now,
            "exp": now + timedelta(minutes=15),
            "jti": str(uuid4()),
            "iss": settings.jwt_issuer,
            "aud": settings.jwt_audience,
        },
        signing_key(settings),
        algorithm="HS256",
    )


def decode_token(token: str, settings: Settings) -> Identity:
    claims = jwt.decode(
        token,
        signing_key(settings),
        algorithms=["HS256"],
        issuer=settings.jwt_issuer,
        audience=settings.jwt_audience,
        options={"require": ["sub", "clinic_id", "iat", "exp", "jti", "iss", "aud"]},
    )
    return Identity(user_id=claims["sub"], clinic_id=claims["clinic_id"])
