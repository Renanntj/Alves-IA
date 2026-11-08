from uuid import uuid4

import jwt
import pytest
from pydantic import SecretStr

from alves_ia.config import Settings
from alves_ia.security import Identity, decode_token, issue_token


def test_signed_token_preserves_identity() -> None:
    settings = Settings(jwt_secret=SecretStr("a" * 32))
    identity = Identity(user_id=uuid4(), clinic_id=uuid4())
    assert decode_token(issue_token(identity, settings), settings) == identity


def test_token_signed_with_another_key_is_rejected() -> None:
    settings = Settings(jwt_secret=SecretStr("a" * 32))
    other = Settings(jwt_secret=SecretStr("b" * 32))
    identity = Identity(user_id=uuid4(), clinic_id=uuid4())
    with pytest.raises(jwt.InvalidSignatureError):
        decode_token(issue_token(identity, other), settings)


def test_expired_token_is_rejected() -> None:
    settings = Settings(jwt_secret=SecretStr("a" * 32))
    identity = Identity(user_id=uuid4(), clinic_id=uuid4())
    claims = jwt.decode(issue_token(identity, settings), options={"verify_signature": False})
    claims["exp"] = 1
    token = jwt.encode(claims, settings.jwt_secret.get_secret_value(), algorithm="HS256")
    with pytest.raises(jwt.ExpiredSignatureError):
        decode_token(token, settings)


def test_missing_signing_key_is_rejected() -> None:
    with pytest.raises(RuntimeError, match="JWT_SECRET"):
        issue_token(
            Identity(user_id=uuid4(), clinic_id=uuid4()), Settings(jwt_secret=SecretStr(""))
        )
