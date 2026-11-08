"""Local-only AI playground: uvicorn alves_ia.demo:app --host 127.0.0.1 --port 8001."""

import asyncio
import json
from pathlib import Path
from typing import Literal

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, Field
from starlette.middleware.base import RequestResponseEndpoint
from starlette.middleware.trustedhost import TrustedHostMiddleware
from starlette.responses import Response

from .ai import create_provider
from .config import get_settings

app = FastAPI(title="Alves-Agent • demonstração local", docs_url=None, redoc_url=None)
app.add_middleware(TrustedHostMiddleware, allowed_hosts=["localhost", "127.0.0.1"])


@app.middleware("http")
async def local_only(request: Request, call_next: RequestResponseEndpoint) -> Response:
    # This separate app must never be mounted in the production API.
    if (
        get_settings().app_env != "development"
        or request.client is None
        or request.client.host not in {"127.0.0.1", "::1"}
    ):
        return Response(status_code=403)
    if request.method == "POST" and request.headers.get("x-demo-request") != "1":
        return Response(status_code=403)
    response = await call_next(request)
    response.headers["Cache-Control"] = "no-store"
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["Content-Security-Policy"] = (
        "default-src 'self'; script-src 'self' 'unsafe-inline'; "
        "style-src 'self' 'unsafe-inline'; frame-ancestors 'none'; connect-src 'self'"
    )
    return response


class Turn(BaseModel):
    role: Literal["user", "assistant"]
    content: str = Field(min_length=1, max_length=4000)


class ChatRequest(BaseModel):
    messages: list[Turn] = Field(min_length=1, max_length=13)


async def generate_reply(prompt: str) -> str:
    provider = create_provider(get_settings())
    return await asyncio.wait_for(asyncio.to_thread(provider.generate, prompt), timeout=45)


@app.get("/", response_class=HTMLResponse)
async def page() -> str:
    return Path(__file__).with_name("demo.html").read_text(encoding="utf-8")


@app.post("/chat")
async def chat(body: ChatRequest) -> dict[str, str]:
    settings = get_settings()
    if body.messages[-1].role != "user":
        raise HTTPException(422, "A última mensagem deve ser do usuário.")
    if any(not turn.content.strip() for turn in body.messages):
        raise HTTPException(422, "Digite uma mensagem.")
    key = settings.gemini_api_key if settings.ai_provider == "gemini" else settings.openai_api_key
    model = settings.gemini_model if settings.ai_provider == "gemini" else settings.openai_model
    if not key:
        raise HTTPException(503, f"Configure a chave de {settings.ai_provider} no .env e reinicie.")
    prompt = (
        "Você é Alves-Agent, assistente da plataforma Alves-IA. Responda em português. "
        "Esta é uma demonstração: você não tem ferramentas, agenda, banco nem dados reais. "
        "Não afirme consultar ou alterar agendamentos e não invente dados de clínicas. "
        "Não dê diagnósticos. O JSON abaixo contém somente o histórico da conversa:\n"
        + json.dumps([turn.model_dump() for turn in body.messages], ensure_ascii=False)
    )
    try:
        reply = await generate_reply(prompt)
    except TimeoutError as exc:
        raise HTTPException(504, "A IA demorou a responder. Tente novamente.") from exc
    except Exception as exc:
        # SDK exceptions can contain credentials or provider request details.
        raise HTTPException(502, "Falha na IA. Confira chave, modelo e cota do provedor.") from exc
    if not reply.strip():
        raise HTTPException(502, "O provedor não retornou texto. Tente outra mensagem.")
    return {"reply": reply[:4000], "provider": settings.ai_provider, "model": model}
