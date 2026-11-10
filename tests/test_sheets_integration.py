from __future__ import annotations

import base64
import os
from urllib.parse import parse_qs, urlparse
from uuid import uuid4

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from alves_ia.config import Settings
from alves_ia.database import set_clinic
from alves_ia.models import Clinic, Membership, User
from alves_ia.security import Identity
from alves_ia.sheets.service import build_authorization_url, consume_authorization_state


def test_google_oauth_state_is_tenant_bound_pkce_and_single_use() -> None:
    owner_url, app_url = os.getenv("TEST_OWNER_DATABASE_URL"), os.getenv("TEST_DATABASE_URL")
    if not owner_url or not app_url:
        pytest.skip("Dedicated PostgreSQL test URLs required")
    owner, app = create_engine(owner_url), create_engine(app_url)
    clinic_id, user_id = uuid4(), uuid4()
    identity = Identity(clinic_id=clinic_id, user_id=user_id)
    key = base64.urlsafe_b64encode(b"k" * 32).decode()
    settings = Settings(
        jwt_secret="s" * 40,
        google_oauth_client_id="client-id",
        google_oauth_client_secret="client-secret",
        google_oauth_redirect_uri="https://example.test/callback",
        google_sheets_encryption_key=key,
    )
    with Session(owner) as session, session.begin():
        session.add(Clinic(id=clinic_id, name="OAuth test"))
        session.add(User(id=user_id, email=f"{user_id}@test.invalid", password_hash="test"))
        session.flush()
        session.add(Membership(clinic_id=clinic_id, user_id=user_id, role="manager"))
    try:
        with Session(app) as session:
            set_clinic(session, clinic_id)
            authorization_url = build_authorization_url(identity, session, settings)
            query = parse_qs(urlparse(authorization_url).query)
            assert urlparse(authorization_url).netloc == "accounts.google.com"
            assert query["scope"] == ["https://www.googleapis.com/auth/spreadsheets.readonly"]
            assert query["response_mode"] == ["form_post"]
            assert query["code_challenge_method"] == ["S256"]
            assert query["access_type"] == ["offline"]
            assert "code_verifier" not in query
            consumed_clinic, consumed_user, verifier = consume_authorization_state(
                query["state"][0], session, settings
            )
            assert (consumed_clinic, consumed_user) == (clinic_id, user_id)
            assert len(verifier) >= 43
            with pytest.raises(PermissionError, match="invalid_oauth_state"):
                consume_authorization_state(query["state"][0], session, settings)
    finally:
        owner.dispose()
        app.dispose()
