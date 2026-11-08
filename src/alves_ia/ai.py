from __future__ import annotations

from typing import TYPE_CHECKING, Literal, Protocol

if TYPE_CHECKING:
    from .config import Settings


ProviderName = Literal["gemini", "openai"]


class AIProvider(Protocol):
    model: str

    def generate(self, prompt: str) -> str:
        """Generate text from a prompt using the configured provider."""

        ...


class GeminiProvider:
    def __init__(self, api_key: str, model: str) -> None:
        self.api_key = api_key
        self.model = model

    def generate(self, prompt: str) -> str:
        if not self.api_key:
            raise ValueError("GEMINI_API_KEY não configurada")

        import google.genai as genai

        client = genai.Client(api_key=self.api_key)
        response = client.models.generate_content(model=self.model, contents=prompt)
        return response.text or ""


class OpenAIProvider:
    def __init__(self, api_key: str, model: str) -> None:
        self.api_key = api_key
        self.model = model

    def generate(self, prompt: str) -> str:
        if not self.api_key:
            raise ValueError("OPENAI_API_KEY não configurada")

        from openai import OpenAI

        client = OpenAI(api_key=self.api_key)
        response = client.responses.create(model=self.model, input=prompt)
        return response.output_text


def create_provider(settings: Settings | None = None) -> AIProvider:
    if settings is None:
        from .config import get_settings

        settings = get_settings()

    if settings.ai_provider == "gemini":
        return GeminiProvider(settings.gemini_api_key, settings.gemini_model)
    if settings.ai_provider == "openai":
        return OpenAIProvider(settings.openai_api_key, settings.openai_model)
    raise ValueError(f"Provedor de IA não suportado: {settings.ai_provider}")

