from collections.abc import Iterator
from functools import lru_cache
from uuid import UUID

from sqlalchemy import Engine, create_engine, text
from sqlalchemy.orm import Session

from .config import get_settings


@lru_cache
def get_engine() -> Engine:
    url = get_settings().database_url.get_secret_value()
    if not url.startswith("postgresql+psycopg://"):
        raise RuntimeError("Configure DATABASE_URL com PostgreSQL/psycopg")
    engine = create_engine(url, pool_pre_ping=True, hide_parameters=True)
    with engine.connect() as connection:
        unsafe = connection.scalar(
            text("SELECT rolsuper OR rolbypassrls FROM pg_roles WHERE rolname = current_user")
        )
    if unsafe:
        engine.dispose()
        raise RuntimeError("A API não pode usar papel superuser ou BYPASSRLS")
    return engine


def get_session() -> Iterator[Session]:
    with Session(get_engine()) as session, session.begin():
        yield session


def set_clinic(session: Session, clinic_id: UUID) -> None:
    # Transaction-local: the pool must never retain another tenant's context.
    session.execute(
        text("SELECT set_config('app.clinic_id', :clinic_id, true)"),
        {"clinic_id": str(clinic_id)},
    )
