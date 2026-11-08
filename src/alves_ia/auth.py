from typing import Annotated
from uuid import UUID

import jwt
from fastapi import APIRouter, Depends, HTTPException
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel, Field, ValidationError
from sqlalchemy import select
from sqlalchemy.orm import Session

from .config import get_settings
from .database import get_session, set_clinic
from .models import Clinic, Membership, User
from .security import DUMMY_HASH, Identity, decode_token, issue_token, password_hasher

router = APIRouter(prefix="/v1", tags=["identity"])
bearer = HTTPBearer(auto_error=False)
Database = Annotated[Session, Depends(get_session)]


class Login(BaseModel):
    email: str = Field(min_length=3, max_length=254)
    password: str = Field(min_length=1, max_length=1024)
    clinic_id: UUID


def unauthorized() -> HTTPException:
    return HTTPException(401, "Credenciais inválidas", headers={"WWW-Authenticate": "Bearer"})


def require_membership(session: Session, identity: Identity) -> Membership:
    set_clinic(session, identity.clinic_id)
    membership = session.get(Membership, (identity.clinic_id, identity.user_id))
    user = session.get(User, identity.user_id)
    clinic = session.get(Clinic, identity.clinic_id)
    if not membership or not membership.active or not user or not user.active:
        raise unauthorized()
    if not clinic or not clinic.active:
        raise unauthorized()
    return membership


@router.post("/auth/token")
def login(body: Login, session: Database) -> dict[str, str | int]:
    user = session.scalar(select(User).where(User.email == body.email.strip().lower()))
    valid = password_hasher.verify(body.password, user.password_hash if user else DUMMY_HASH)
    if not valid or user is None or not user.active:
        raise unauthorized()
    identity = Identity(user_id=user.id, clinic_id=body.clinic_id)
    require_membership(session, identity)
    return {
        "access_token": issue_token(identity, get_settings()),
        "token_type": "bearer",
        "expires_in": 900,
    }


async def token_identity(
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer)],
) -> Identity:
    if credentials is None:
        raise unauthorized()
    try:
        identity = decode_token(credentials.credentials, get_settings())
    except (jwt.InvalidTokenError, ValidationError, ValueError) as exc:
        raise unauthorized() from exc
    return identity


def current_identity(
    identity: Annotated[Identity, Depends(token_identity)],
    session: Database,
) -> Identity:
    require_membership(session, identity)
    return identity


@router.get("/me")
def me(identity: Annotated[Identity, Depends(current_identity)]) -> Identity:
    return identity


@router.get("/clinic")
def clinic(
    identity: Annotated[Identity, Depends(current_identity)],
    session: Database,
) -> dict[str, str]:
    row = session.get(Clinic, identity.clinic_id)
    if row is None:
        raise HTTPException(404, "Clínica não encontrada")
    return {"id": str(row.id), "name": row.name}
