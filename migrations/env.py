import os

from alembic import context
from sqlalchemy import create_engine

from alves_ia.models import Base
from alves_ia.whatsapp import models as whatsapp_models  # noqa: F401

url = os.environ["MIGRATION_DATABASE_URL"]
if context.is_offline_mode():
    context.configure(url=url, target_metadata=Base.metadata, literal_binds=True)
    with context.begin_transaction():
        context.run_migrations()
else:
    engine = create_engine(url, hide_parameters=True)
    with engine.connect() as connection:
        context.configure(connection=connection, target_metadata=Base.metadata)
        with context.begin_transaction():
            context.run_migrations()
    engine.dispose()
