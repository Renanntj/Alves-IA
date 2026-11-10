from __future__ import annotations

import base64
import secrets

import httpx
import pytest
from pydantic import SecretStr
from pydantic_settings import SettingsConfigDict

from alves_ia.config import Settings
from alves_ia.sheets.crypto import decrypt_refresh_token, encrypt_refresh_token
from alves_ia.sheets.google_api import GoogleSheetsAPIError, read_values, refresh_access_token
from alves_ia.sheets.search import (
    search_values,
    validate_range_name,
    validate_sheet_id,
)


class SettingsForSheets(Settings):
    model_config = SettingsConfigDict(env_file=None)

    google_oauth_client_id: str = "client-id"
    google_oauth_client_secret: SecretStr = SecretStr("client-secret")


def make_key() -> str:
    return base64.urlsafe_b64encode(secrets.token_bytes(32)).decode("ascii")


def test_refresh_token_is_encrypted_and_round_trips() -> None:
    key = make_key()
    refresh_token = "google-refresh-token-private"

    ciphertext = encrypt_refresh_token(refresh_token, key)

    assert isinstance(ciphertext, bytes)
    assert refresh_token.encode() not in ciphertext
    assert decrypt_refresh_token(ciphertext, key) == refresh_token


def test_refresh_token_cannot_be_decrypted_with_another_key() -> None:
    ciphertext = encrypt_refresh_token("private", make_key())

    with pytest.raises(ValueError, match="decrypt"):
        decrypt_refresh_token(ciphertext, make_key())


@pytest.mark.parametrize("key", ["", "not-base64", base64.urlsafe_b64encode(b"short").decode()])
def test_encryption_rejects_invalid_key(key: str) -> None:
    with pytest.raises(ValueError, match="key"):
        encrypt_refresh_token("private", key)


@pytest.mark.parametrize(
    ("spreadsheet_id", "valid"),
    [("1AbCd_1234567890-xyz", True), ("short", False), ("https://evil.test/id", False)],
)
def test_spreadsheet_id_is_an_id_not_a_url(spreadsheet_id: str, valid: bool) -> None:
    if valid:
        assert validate_sheet_id(spreadsheet_id) == spreadsheet_id
    else:
        with pytest.raises(ValueError, match="spreadsheet_id"):
            validate_sheet_id(spreadsheet_id)


@pytest.mark.parametrize(
    "range_name",
    [
        "Services!A1:Z501",
        "'Patient Services'!A1:M50",
        "'D''Angelo'!A1:B20",
    ],
)
def test_sheet_range_accepts_bounded_a1_ranges(range_name: str) -> None:
    assert validate_range_name(range_name) == range_name


@pytest.mark.parametrize(
    "range_name",
    [
        "A1:Z501",
        "Services!B1:Z20",
        "Services!A2:Z20",
        "Services!A1:AA20",
        "Services!A1:Z999",
        "Services!A:A",
        "https://evil.test/Services!A1:Z2",
    ],
)
def test_sheet_range_rejects_unbounded_or_unsupported_a1(range_name: str) -> None:
    with pytest.raises(ValueError, match="range"):
        validate_range_name(range_name)


def test_sheet_search_returns_only_matching_allowlisted_columns() -> None:
    values: list[list[object]] = [
        ["Serviço", "Descrição", "Observações internas"],
        ["Botox", "Aplicação facial", "não compartilhar instrução"],
        ["Limpeza", "Higienização de pele", "outro dado"],
    ]

    results = search_values(values, ["Serviço", "Descrição"], "aplicação facial")

    assert results == [{"linha": "2", "Serviço": "Botox", "Descrição": "Aplicação facial"}]


def test_sheet_search_caps_rows_and_cell_values() -> None:
    values: list[list[object]] = [["Nome", "Plano"]] + [
        [f"Cliente {index}", "Plano " + ("x" * 500)] for index in range(8)
    ]

    results = search_values(values, ["Nome", "Plano"], "plano")

    assert len(results) == 5
    assert all(len(row["Plano"]) == 300 for row in results)


def test_sheet_search_rejects_missing_headers_and_invalid_queries() -> None:
    with pytest.raises(ValueError, match="columns"):
        search_values([["Nome"]], ["Plano"], "plano")
    with pytest.raises(ValueError, match="query"):
        search_values([["Nome"]], ["Nome"], "x")


def test_refresh_access_token_uses_confidential_client_credentials() -> None:
    def respond(request: httpx.Request) -> httpx.Response:
        assert request.url == "https://oauth2.googleapis.com/token"
        assert request.method == "POST"
        assert request.headers["content-type"].startswith("application/x-www-form-urlencoded")
        assert "refresh_token=secret-refresh" in request.content.decode()
        return httpx.Response(200, json={"access_token": "temporary", "expires_in": 3600})

    assert (
        refresh_access_token("secret-refresh", SettingsForSheets(), httpx.MockTransport(respond))
        == "temporary"
    )


def test_read_values_uses_only_requested_bounded_range() -> None:
    def respond(request: httpx.Request) -> httpx.Response:
        assert request.url.host == "sheets.googleapis.com"
        assert request.url.raw_path.split(b"?", 1)[0].endswith(b"/values/Services%21A1%3AZ20")
        assert request.url.params["majorDimension"] == "ROWS"
        assert request.headers["authorization"] == "Bearer temporary"
        return httpx.Response(200, json={"values": [["Name"], ["Service"]]}, request=request)

    values = read_values(
        "1AbCd_1234567890-xyz",
        "Services!A1:Z20",
        "temporary",
        httpx.MockTransport(respond),
    )

    assert values == [["Name"], ["Service"]]


def test_read_values_rejects_redirects_without_forwarding_token() -> None:
    def respond(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            302,
            headers={"Location": "https://attacker.invalid/collect"},
            request=request,
        )

    with pytest.raises(GoogleSheetsAPIError, match="sheets_read_failed"):
        read_values(
            "1AbCd_1234567890-xyz",
            "Services!A1:Z20",
            "temporary",
            httpx.MockTransport(respond),
        )
