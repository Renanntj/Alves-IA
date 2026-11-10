from __future__ import annotations

import json
from typing import Any
from urllib.parse import quote

import httpx

from alves_ia.config import Settings

from .search import validate_range_name, validate_sheet_id

SHEETS_READONLY_SCOPE = "https://www.googleapis.com/auth/spreadsheets.readonly"
_TOKEN_ENDPOINT = "https://oauth2.googleapis.com/token"
_REVOKE_ENDPOINT = "https://oauth2.googleapis.com/revoke"
_VALUES_ENDPOINT = "https://sheets.googleapis.com/v4/spreadsheets"
_MAX_VALUES_RESPONSE_BYTES = 1024 * 1024


class GoogleSheetsAPIError(RuntimeError):
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


def exchange_authorization_code(
    code: str,
    code_verifier: str,
    settings: Settings,
    transport: httpx.BaseTransport | None = None,
) -> dict[str, str]:
    if not code or len(code) > 2048:
        raise GoogleSheetsAPIError("invalid_authorization_code")
    try:
        with httpx.Client(
            timeout=15,
            follow_redirects=False,
            trust_env=False,
            transport=transport,
        ) as client:
            response = client.post(
                _TOKEN_ENDPOINT,
                data={
                    "code": code,
                    "code_verifier": code_verifier,
                    "client_id": settings.google_oauth_client_id,
                    "client_secret": settings.google_oauth_client_secret.get_secret_value(),
                    "redirect_uri": settings.google_oauth_redirect_uri,
                    "grant_type": "authorization_code",
                },
            )
            response.raise_for_status()
            data: Any = response.json()
    except (httpx.HTTPError, ValueError, TypeError) as exc:
        raise GoogleSheetsAPIError("google_oauth_exchange_failed") from exc
    if not isinstance(data, dict):
        raise GoogleSheetsAPIError("google_oauth_exchange_failed")
    access_token, refresh_token = data.get("access_token"), data.get("refresh_token")
    scopes = data.get("scope", "")
    if (
        not isinstance(access_token, str)
        or not access_token
        or not isinstance(refresh_token, str)
        or not refresh_token
        or not isinstance(scopes, str)
        or SHEETS_READONLY_SCOPE not in scopes.split()
    ):
        raise GoogleSheetsAPIError("google_oauth_invalid_grant")
    return {"access_token": access_token, "refresh_token": refresh_token}


def refresh_access_token(
    refresh_token: str,
    settings: Settings,
    transport: httpx.BaseTransport | None = None,
) -> str:
    if not refresh_token:
        raise GoogleSheetsAPIError("google_reauthorization_required")
    try:
        with httpx.Client(
            timeout=15,
            follow_redirects=False,
            trust_env=False,
            transport=transport,
        ) as client:
            response = client.post(
                _TOKEN_ENDPOINT,
                data={
                    "client_id": settings.google_oauth_client_id,
                    "client_secret": settings.google_oauth_client_secret.get_secret_value(),
                    "refresh_token": refresh_token,
                    "grant_type": "refresh_token",
                },
            )
            response.raise_for_status()
            data: Any = response.json()
    except (httpx.HTTPError, ValueError, TypeError) as exc:
        raise GoogleSheetsAPIError("google_reauthorization_required") from exc
    access_token = data.get("access_token") if isinstance(data, dict) else None
    if not isinstance(access_token, str) or not access_token:
        raise GoogleSheetsAPIError("google_reauthorization_required")
    return access_token


def read_values(
    spreadsheet_id: str,
    range_name: str,
    access_token: str,
    transport: httpx.BaseTransport | None = None,
) -> list[list[object]]:
    validate_sheet_id(spreadsheet_id)
    validate_range_name(range_name)
    if not access_token:
        raise GoogleSheetsAPIError("google_reauthorization_required")
    endpoint = f"{_VALUES_ENDPOINT}/{spreadsheet_id}/values/{quote(range_name, safe='')}"
    try:
        with httpx.Client(
            timeout=20,
            follow_redirects=False,
            trust_env=False,
            transport=transport,
        ) as client:
            with client.stream(
                "GET",
                endpoint,
                params={"majorDimension": "ROWS", "valueRenderOption": "FORMATTED_VALUE"},
                headers={"Authorization": f"Bearer {access_token}"},
            ) as response:
                response.raise_for_status()
                content_length = response.headers.get("content-length")
                if content_length and (
                    not content_length.isdigit() or int(content_length) > _MAX_VALUES_RESPONSE_BYTES
                ):
                    raise GoogleSheetsAPIError("sheets_response_too_large")
                body = bytearray()
                for chunk in response.iter_bytes():
                    body.extend(chunk)
                    if len(body) > _MAX_VALUES_RESPONSE_BYTES:
                        raise GoogleSheetsAPIError("sheets_response_too_large")
        data: Any = json.loads(body)
    except GoogleSheetsAPIError:
        raise
    except (httpx.HTTPError, ValueError, TypeError) as exc:
        raise GoogleSheetsAPIError("sheets_read_failed") from exc
    values = data.get("values", []) if isinstance(data, dict) else None
    if not isinstance(values, list) or any(not isinstance(row, list) for row in values):
        raise GoogleSheetsAPIError("sheets_invalid_response")
    return values


def revoke_token(
    token: str,
    transport: httpx.BaseTransport | None = None,
) -> bool:
    if not token:
        return True
    try:
        with httpx.Client(
            timeout=10,
            follow_redirects=False,
            trust_env=False,
            transport=transport,
        ) as client:
            response = client.post(_REVOKE_ENDPOINT, data={"token": token})
        return response.is_success
    except httpx.HTTPError:
        return False
