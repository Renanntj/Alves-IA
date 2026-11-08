from functools import lru_cache
from typing import Literal

from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    app_env: str = "development"
    app_name: str = "Alves IA"
    debug: bool = False
    host: str = "0.0.0.0"
    port: int = 8000
    ai_provider: Literal["gemini", "openai"] = "gemini"
    gemini_api_key: str = ""
    gemini_model: str = "gemini-2.5-flash"
    openai_api_key: str = ""
    openai_model: str = "gpt-4o-mini"
    timezone: str = "America/Recife"
    agent_name: str = "Alves-Agent"
    database_url: SecretStr = SecretStr("")
    jwt_secret: SecretStr = SecretStr("")
    jwt_issuer: str = "alves-ia"
    jwt_audience: str = "alves-ia-api"
    whatsapp_app_secret: SecretStr = SecretStr("")
    whatsapp_verify_token: SecretStr = SecretStr("")
    whatsapp_access_token: SecretStr = SecretStr("")
    whatsapp_api_version: str = ""
    redis_url: SecretStr = SecretStr("redis://redis:6379/0")

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )


@lru_cache
def get_settings() -> Settings:
    return Settings()
