"""Register a verified Meta number through an operator-only command."""

import argparse
import os
import re
from uuid import UUID

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from alves_ia.models import Clinic

from .models import ChannelRoute


def main() -> None:
    parser = argparse.ArgumentParser(description="Vincular número WhatsApp à clínica")
    parser.add_argument("--clinic-id", type=UUID, required=True)
    parser.add_argument("--phone-id", required=True)
    parser.add_argument("--waba-id", required=True)
    args = parser.parse_args()
    if not all(re.fullmatch(r"\d{1,64}", value) for value in (args.phone_id, args.waba_id)):
        parser.error("IDs Meta devem ser numéricos")
    engine = create_engine(os.environ["MIGRATION_DATABASE_URL"], hide_parameters=True)
    try:
        with Session(engine) as session, session.begin():
            clinic = session.get(Clinic, args.clinic_id)
            if not clinic or not clinic.active:
                parser.error("Clínica inexistente ou desativada")
            if session.get(ChannelRoute, args.phone_id):
                parser.error("Número já vinculado; transferência exige procedimento administrativo")
            session.add(
                ChannelRoute(phone_id=args.phone_id, waba_id=args.waba_id, clinic_id=args.clinic_id)
            )
        print("Canal cadastrado")
    finally:
        engine.dispose()


if __name__ == "__main__":
    main()
