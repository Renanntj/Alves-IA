"""Explicit operator-only provisioning; never exposed over HTTP."""

import argparse
import getpass
import os

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from .models import Clinic, Membership, User
from .security import password_hasher


def main() -> None:
    parser = argparse.ArgumentParser(description="Criar clínica e administrador Alves-IA")
    parser.add_argument("--clinic", required=True)
    parser.add_argument("--email", required=True)
    args = parser.parse_args()
    password = getpass.getpass("Senha do administrador (mínimo 12 caracteres): ")
    if len(password) < 12 or password != getpass.getpass("Confirme a senha: "):
        parser.error("Senha curta ou confirmação diferente")
    engine = create_engine(os.environ["MIGRATION_DATABASE_URL"], hide_parameters=True)
    try:
        with Session(engine) as session, session.begin():
            clinic = Clinic(name=args.clinic)
            user = User(
                email=args.email.strip().lower(), password_hash=password_hasher.hash(password)
            )
            session.add_all([clinic, user])
            session.flush()
            session.add(Membership(clinic_id=clinic.id, user_id=user.id, role="admin"))
            clinic_id = clinic.id
        print(f"Clínica criada: {clinic_id}")
    finally:
        engine.dispose()


if __name__ == "__main__":
    main()
