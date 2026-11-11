from __future__ import annotations

import base64
import hashlib
import secrets
from datetime import UTC, datetime, timedelta
from urllib.parse import urlencode
from uuid import UUID

import jwt
from sqlalchemy import delete
from sqlalchemy.orm import Session

from alves_ia.config import Settings, get_settings
from alves_ia.database import set_clinic
from alves_ia.models import Membership, User
from alves_ia.security import Identity, signing_key

from .crypto import decrypt_refresh_token, encrypt_refresh_token
from .google_api import (
    SHEETS_READONLY_SCOPE,
    exchange_authorization_code,
    read_values,
    refresh_access_token,
    revoke_token,
)
from .models import GoogleSheetsConnection, GoogleSheetsOAuthState
from .search import MAX_ALLOWED_COLUMNS, search_values, validate_range_name, validate_sheet_id

_AUTH_ENDPOINT = "https://accounts.google.com/o/oauth2/v2/auth"
_STATE_ISSUER = "alves-ia-sheets"
_STATE_AUDIENCE = "alves-ia-sheets-callback"


def _secret(value: object) -> str:
    return value.get_secret_value() if hasattr(value, "get_secret_value") else str(value)


def _require_manager(session: Session, identity: Identity) -> None:
    set_clinic(session, identity.clinic_id)
    membership = session.get(Membership, (identity.clinic_id, identity.user_id))
    user = session.get(User, identity.user_id)
    if not membership or not membership.active or membership.role not in {"admin", "manager"}:
        raise PermissionError("manager_required")
    if not user or not user.active:
        raise PermissionError("active_user_required")


def _encryption_key(settings: Settings) -> str:
    key = _secret(settings.google_sheets_encryption_key)
    if not key:
        raise RuntimeError("Google Sheets encryption key is not configured")
    return key


def build_authorization_url(
    identity: Identity, session: Session, settings: Settings | None = None
) -> str:
    settings = settings or get_settings()
    _require_manager(session, identity)
    if not settings.google_oauth_client_id or not settings.google_oauth_redirect_uri:
        raise RuntimeError("Google OAuth is not configured")
    nonce = secrets.token_urlsafe(32)
    verifier = secrets.token_urlsafe(64)
    challenge = (
        base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    )
    now = datetime.now(UTC)
    state = jwt.encode(
        {
            "clinic_id": str(identity.clinic_id),
            "user_id": str(identity.user_id),
            "nonce": nonce,
            "iat": now,
            "exp": now + timedelta(minutes=10),
            "iss": _STATE_ISSUER,
            "aud": _STATE_AUDIENCE,
        },
        signing_key(settings),
        algorithm="HS256",
    )
    session.add(
        GoogleSheetsOAuthState(
            clinic_id=identity.clinic_id,
            nonce_hash=hashlib.sha256(nonce.encode()).hexdigest(),
            user_id=identity.user_id,
            code_verifier_ciphertext=encrypt_refresh_token(verifier, _encryption_key(settings)),
            expires_at=now + timedelta(minutes=10),
        )
    )
    session.commit()
    return (
        _AUTH_ENDPOINT
        + "?"
        + urlencode(
            {
                "client_id": settings.google_oauth_client_id,
                "redirect_uri": settings.google_oauth_redirect_uri,
                "response_type": "code",
                "response_mode": "form_post",
                "scope": SHEETS_READONLY_SCOPE,
                "access_type": "offline",
                "prompt": "consent",
                "state": state,
                "code_challenge": challenge,
                "code_challenge_method": "S256",
            }
        )
    )


def consume_authorization_state(
    state: str, session: Session, settings: Settings | None = None
) -> tuple[UUID, UUID, str]:
    settings = settings or get_settings()
    try:
        claims = jwt.decode(
            state,
            signing_key(settings),
            algorithms=["HS256"],
            issuer=_STATE_ISSUER,
            audience=_STATE_AUDIENCE,
            options={"require": ["clinic_id", "user_id", "nonce", "iat", "exp", "iss", "aud"]},
        )
        clinic_id, user_id = UUID(claims["clinic_id"]), UUID(claims["user_id"])
        nonce = claims["nonce"]
        nonce_hash = hashlib.sha256(nonce.encode()).hexdigest()
    except (jwt.InvalidTokenError, ValueError, KeyError, TypeError) as exc:
        raise PermissionError("invalid_oauth_state") from exc

    set_clinic(session, clinic_id)
    row = session.execute(
        delete(GoogleSheetsOAuthState)
        .where(
            GoogleSheetsOAuthState.clinic_id == clinic_id,
            GoogleSheetsOAuthState.nonce_hash == nonce_hash,
            GoogleSheetsOAuthState.user_id == user_id,
        )
        .returning(
            GoogleSheetsOAuthState.code_verifier_ciphertext,
            GoogleSheetsOAuthState.expires_at,
        )
    ).one_or_none()
    if row is None:
        session.rollback()
        raise PermissionError("invalid_oauth_state")
    membership = session.get(Membership, (clinic_id, user_id))
    user = session.get(User, user_id)
    valid = (
        row.expires_at > datetime.now(UTC)
        and membership is not None
        and membership.active
        and membership.role in {"admin", "manager"}
        and user is not None
        and user.active
    )
    session.commit()  # Consume one-use state before external token exchange.
    if not valid:
        raise PermissionError("invalid_oauth_state")
    verifier = decrypt_refresh_token(row.code_verifier_ciphertext, _encryption_key(settings))
    return clinic_id, user_id, verifier


def complete_authorization(
    code: str, state: str, session: Session, settings: Settings | None = None
) -> None:
    settings = settings or get_settings()
    clinic_id, user_id, verifier = consume_authorization_state(state, session, settings)
    tokens = exchange_authorization_code(code, verifier, settings)
    set_clinic(session, clinic_id)
    _require_manager(session, Identity(user_id=user_id, clinic_id=clinic_id))
    connection = session.get(GoogleSheetsConnection, clinic_id)
    if connection is None:
        connection = GoogleSheetsConnection(
            clinic_id=clinic_id,
            refresh_token_ciphertext=encrypt_refresh_token(
                tokens["refresh_token"], _encryption_key(settings)
            ),
        )
        session.add(connection)
    else:
        connection.refresh_token_ciphertext = encrypt_refresh_token(
            tokens["refresh_token"], _encryption_key(settings)
        )
        connection.connected_at = datetime.now(UTC)
    session.commit()


def configure_sheet(
    session: Session,
    identity: Identity,
    spreadsheet_id: str,
    range_name: str,
    allowed_columns: list[str],
    settings: Settings | None = None,
) -> None:
    settings = settings or get_settings()
    _require_manager(session, identity)
    validate_sheet_id(spreadsheet_id)
    validate_range_name(range_name)
    if (
        not 1 <= len(allowed_columns) <= MAX_ALLOWED_COLUMNS
        or len(set(allowed_columns)) != len(allowed_columns)
        or any(
            not isinstance(column, str) or not column.strip() or len(column) > 100
            for column in allowed_columns
        )
    ):
        raise ValueError("invalid_allowed_columns")
    connection = session.get(GoogleSheetsConnection, identity.clinic_id)
    if connection is None:
        raise LookupError("google_sheets_not_connected")
    refresh_token = decrypt_refresh_token(
        connection.refresh_token_ciphertext, _encryption_key(settings)
    )
    access_token = refresh_access_token(refresh_token, settings)
    values = read_values(spreadsheet_id, range_name, access_token)
    headers = [str(value).strip() for value in values[0]] if values else []
    if (
        not headers
        or len(headers) != len(set(headers))
        or any(not isinstance(column, str) or column not in headers for column in allowed_columns)
    ):
        raise ValueError("sheet_headers_do_not_match")
    connection.spreadsheet_id = spreadsheet_id
    connection.range_name = range_name
    connection.allowed_columns = allowed_columns
    session.commit()


def search_sheet(
    session: Session, clinic_id: UUID, query: str, settings: Settings | None = None
) -> list[dict[str, str]]:
    settings = settings or get_settings()
    set_clinic(session, clinic_id)
    connection = session.get(GoogleSheetsConnection, clinic_id)
    if (
        connection is None
        or not connection.spreadsheet_id
        or not connection.range_name
        or not connection.allowed_columns
    ):
        raise LookupError("google_sheets_not_configured")
    refresh_token = decrypt_refresh_token(
        connection.refresh_token_ciphertext, _encryption_key(settings)
    )
    access_token = refresh_access_token(refresh_token, settings)
    values = read_values(connection.spreadsheet_id, connection.range_name, access_token)
    return search_values(values, connection.allowed_columns, query)


def disconnect_sheet(
    session: Session, identity: Identity, settings: Settings | None = None
) -> None:
    settings = settings or get_settings()
    _require_manager(session, identity)
    connection = session.get(GoogleSheetsConnection, identity.clinic_id)
    if connection:
        refresh_token = decrypt_refresh_token(
            connection.refresh_token_ciphertext, _encryption_key(settings)
        )
        session.delete(connection)
        session.commit()
        revoke_token(refresh_token)
