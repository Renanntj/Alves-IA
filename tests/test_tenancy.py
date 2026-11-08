"""Run against a dedicated migrated PostgreSQL database, never production."""

import os
from uuid import uuid4

import pytest
from fastapi import HTTPException
from sqlalchemy import create_engine, delete, select, text
from sqlalchemy.orm import Session

from alves_ia.auth import require_membership
from alves_ia.database import set_clinic
from alves_ia.models import Clinic, Membership, User
from alves_ia.security import Identity, password_hasher


def test_rls_and_membership_isolate_clinics() -> None:
    owner_url = os.environ.get("TEST_OWNER_DATABASE_URL")
    app_url = os.environ.get("TEST_DATABASE_URL")
    if not owner_url or not app_url:
        pytest.skip("Configure dedicated PostgreSQL TEST_OWNER_DATABASE_URL and TEST_DATABASE_URL")
    owner = create_engine(owner_url)
    app = create_engine(app_url, pool_size=1, max_overflow=0)
    a, b, user_id = uuid4(), uuid4(), uuid4()
    try:
        with Session(owner) as session, session.begin():
            session.add_all([Clinic(id=a, name="A"), Clinic(id=b, name="B")])
            session.add(
                User(
                    id=user_id,
                    email=f"{user_id}@test.invalid",
                    password_hash=password_hasher.hash("testing-password"),
                )
            )
            session.flush()
            session.add(Membership(clinic_id=a, user_id=user_id, role="admin"))
        with Session(app) as session, session.begin():
            assert not session.scalar(
                text("SELECT rolsuper OR rolbypassrls FROM pg_roles WHERE rolname = current_user")
            )
            assert session.scalars(select(Clinic)).all() == []
            set_clinic(session, a)
            assert [row.id for row in session.scalars(select(Clinic))] == [a]
            assert session.get(Clinic, b) is None
            require_membership(session, Identity(user_id=user_id, clinic_id=a))
        with Session(app) as session, session.begin():
            assert session.scalars(select(Clinic)).all() == []
            with pytest.raises(HTTPException) as error:
                require_membership(session, Identity(user_id=user_id, clinic_id=b))
            assert error.value.status_code == 401
    finally:
        with owner.begin() as connection:
            connection.execute(delete(Membership).where(Membership.user_id == user_id))
            connection.execute(delete(User).where(User.id == user_id))
            connection.execute(delete(Clinic).where(Clinic.id.in_([a, b])))
        app.dispose()
        owner.dispose()
