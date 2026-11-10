from __future__ import annotations

from typing import Annotated
from urllib.parse import parse_qs

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from alves_ia.auth import Database, current_identity
from alves_ia.models import Membership
from alves_ia.security import Identity

from .google_api import GoogleSheetsAPIError
from .models import GoogleSheetsConnection
from .service import build_authorization_url, configure_sheet, disconnect_sheet

router = APIRouter(prefix="/v1/integrations/google-sheets", tags=["Google Sheets"])


class SheetConfiguration(BaseModel):
    spreadsheet_id: str = Field(min_length=20, max_length=256)
    range_name: str = Field(min_length=3, max_length=256)
    allowed_columns: list[str] = Field(min_length=1, max_length=12)


def _manager(session: Session, identity: Identity) -> None:
    membership = session.get(Membership, (identity.clinic_id, identity.user_id))
    if not membership or not membership.active or membership.role not in {"admin", "manager"}:
        raise HTTPException(403, "Apenas administradores e gestores podem configurar a planilha")


@router.post("/connect")
def connect(
    identity: Annotated[Identity, Depends(current_identity)], session: Database
) -> dict[str, str]:
    try:
        return {"authorization_url": build_authorization_url(identity, session)}
    except PermissionError as exc:
        raise HTTPException(403, "Permissão insuficiente") from exc
    except (RuntimeError, ValueError) as exc:
        raise HTTPException(503, "OAuth do Google não está configurado") from exc


@router.post("/callback", include_in_schema=False)
async def callback(request: Request, session: Database) -> Response:
    # Google form_post response keeps the authorization code out of the URL/query logs.
    body = await request.body()
    if len(body) > 8192:
        raise HTTPException(413, "Callback muito grande")
    try:
        form = parse_qs(body.decode("ascii"), keep_blank_values=True, strict_parsing=True)
    except (UnicodeDecodeError, ValueError) as exc:
        raise HTTPException(400, "Callback OAuth inválido") from exc
    if any(len(values) != 1 for values in form.values()):
        raise HTTPException(400, "Callback OAuth inválido")
    error, code, state = (form.get(key, [""])[0] for key in ("error", "code", "state"))
    if error:
        from .service import consume_authorization_state

        try:
            consume_authorization_state(state, session)
        except PermissionError as exc:
            raise HTTPException(400, "Estado OAuth inválido ou expirado") from exc
        return Response(status_code=303, headers={"Location": "/docs"})
    if not code or not state:
        raise HTTPException(400, "Callback OAuth incompleto")
    try:
        from .service import complete_authorization

        complete_authorization(code, state, session)
    except PermissionError as exc:
        raise HTTPException(400, "Estado OAuth inválido ou expirado") from exc
    except GoogleSheetsAPIError as exc:
        raise HTTPException(502, "Não foi possível conectar ao Google Sheets") from exc
    except (RuntimeError, ValueError) as exc:
        raise HTTPException(503, "Configuração OAuth inválida") from exc
    return Response(status_code=303, headers={"Location": "/docs"})


@router.get("/status")
def status(
    identity: Annotated[Identity, Depends(current_identity)], session: Database
) -> dict[str, object]:
    _manager(session, identity)
    connection = session.get(GoogleSheetsConnection, identity.clinic_id)
    return {
        "connected": connection is not None,
        "configured": bool(connection and connection.spreadsheet_id and connection.range_name),
        "spreadsheet_id": connection.spreadsheet_id if connection else None,
        "range_name": connection.range_name if connection else None,
        "allowed_columns": connection.allowed_columns if connection else [],
    }


@router.put("/configuration")
def configuration(
    body: SheetConfiguration,
    identity: Annotated[Identity, Depends(current_identity)],
    session: Database,
) -> dict[str, str]:
    try:
        configure_sheet(
            session,
            identity,
            body.spreadsheet_id,
            body.range_name,
            body.allowed_columns,
        )
    except PermissionError as exc:
        raise HTTPException(403, "Permissão insuficiente") from exc
    except LookupError as exc:
        raise HTTPException(409, "Conecte uma conta Google antes de configurar a planilha") from exc
    except GoogleSheetsAPIError as exc:
        raise HTTPException(502, "Não foi possível validar a planilha no Google Sheets") from exc
    except ValueError as exc:
        raise HTTPException(422, "Faixa ou colunas da planilha inválidas") from exc
    return {"status": "configured"}


@router.delete("/connection", status_code=204)
def disconnect(
    identity: Annotated[Identity, Depends(current_identity)], session: Database
) -> Response:
    try:
        disconnect_sheet(session, identity)
    except PermissionError as exc:
        raise HTTPException(403, "Permissão insuficiente") from exc
    except ValueError as exc:
        raise HTTPException(503, "Não foi possível remover a conexão") from exc
    return Response(status_code=204)
