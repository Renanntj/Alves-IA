"""HTTP authentication against the isolated PostgreSQL test stack."""

import os
from collections.abc import Iterator
from uuid import uuid4

import httpx
import pytest
from sqlalchemy import create_engine, delete, update
from sqlalchemy.orm import Session

from alves_ia.config import get_settings
from alves_ia.database import get_session
from alves_ia.main import app
from alves_ia.models import Clinic, Membership, User
from alves_ia.security import password_hasher


@pytest.mark.asyncio
async def test_login_tenant_authorization_and_revocation(monkeypatch: pytest.MonkeyPatch) -> None:
    owner_url = os.environ.get("TEST_OWNER_DATABASE_URL")
    app_url = os.environ.get("TEST_DATABASE_URL")
    if not owner_url or not app_url:
        pytest.skip("Dedicated PostgreSQL test URLs required")
    owner = create_engine(owner_url)
    database = create_engine(app_url)
    clinic_a, clinic_b, user_id = uuid4(), uuid4(), uuid4()
    email, password = f"{user_id}@test.invalid", "test-password-123"
    monkeypatch.setenv("JWT_SECRET", "integration-test-signing-key-123456789")
    get_settings.cache_clear()

    def session_override() -> Iterator[Session]:
        with Session(database) as session, session.begin():
            yield session

    app.dependency_overrides[get_session] = session_override
    try:
        with Session(owner) as session, session.begin():
            session.add_all([Clinic(id=clinic_a, name="A"), Clinic(id=clinic_b, name="B")])
            session.add(User(id=user_id, email=email, password_hash=password_hasher.hash(password)))
            session.flush()
            session.add(Membership(clinic_id=clinic_a, user_id=user_id, role="admin"))
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            payload = {"email": email, "password": password, "clinic_id": str(clinic_a)}
            wrong_password = await client.post(
                "/v1/auth/token", json={**payload, "password": "wrong"}
            )
            assert wrong_password.status_code == 401
            other_tenant = await client.post(
                "/v1/auth/token", json={**payload, "clinic_id": str(clinic_b)}
            )
            assert other_tenant.status_code == 401
            login = await client.post("/v1/auth/token", json=payload)
            assert login.status_code == 200
            headers = {"Authorization": f"Bearer {login.json()['access_token']}"}
            me = await client.get("/v1/me", headers=headers)
            assert me.json() == {"user_id": str(user_id), "clinic_id": str(clinic_a)}
            clinic = await client.get("/v1/clinic", headers=headers)
            assert clinic.json() == {"id": str(clinic_a), "name": "A"}
            with owner.begin() as connection:
                connection.execute(
                    update(Membership).where(Membership.user_id == user_id).values(active=False)
                )
            revoked = await client.get("/v1/me", headers=headers)
            assert revoked.status_code == 401
    finally:
        app.dependency_overrides.pop(get_session, None)
        get_settings.cache_clear()
        with owner.begin() as connection:
            connection.execute(delete(Membership).where(Membership.user_id == user_id))
            connection.execute(delete(User).where(User.id == user_id))
            connection.execute(delete(Clinic).where(Clinic.id.in_([clinic_a, clinic_b])))
        database.dispose()
        owner.dispose()
