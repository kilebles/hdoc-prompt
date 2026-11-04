"""Настройки веб-API: переменные окружения или .env в папке программы (не в git)."""

from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

from promptgen.config import ROOT


class Settings(BaseSettings):
    """API_TOKEN, CORS_ORIGINS, HOST, PORT — те же имена, что в окружении."""

    model_config = SettingsConfigDict(env_file=ROOT / ".env", env_file_encoding="utf-8", extra="ignore")

    api_token: str = Field(default="", description="Bearer-токен сайта (тот же, что у fl-api и других модулей); "
                                                    "пусто — без проверки")
    cors_origins: list[str] = Field(
        default=["http://localhost:3000", "http://127.0.0.1:3000", "http://localhost:3100", "http://127.0.0.1:3100"])
    host: str = Field(default="0.0.0.0")
    port: int = Field(default=8040)
    watch_every: float = Field(default=2.0, description="Секунд между проверками изменений для SSE")
    root: Path = Field(default=ROOT)
