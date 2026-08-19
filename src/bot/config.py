from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class BotSettings(BaseSettings):
    """Bot-related settings, read from environment / .env."""

    model_config = SettingsConfigDict(
        env_prefix="BOT_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    token: SecretStr
    drop_pending_updates: bool = True


class LoggingSettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="LOG_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    level: str = "INFO"


class GoogleAISettings(BaseSettings):
    """Gemini API settings, used by PromptParserService."""

    model_config = SettingsConfigDict(
        env_prefix="GOOGLE_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    api_key: SecretStr
    model: str = "gemini-2.5-flash"


class OpenAISettings(BaseSettings):
    """OpenAI API settings, used as an alternative i2v generation backend."""

    model_config = SettingsConfigDict(
        env_prefix="OPENAI_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    api_key: SecretStr


class Settings(BaseSettings):
    bot: BotSettings = BotSettings()  # type: ignore[call-arg]
    logging: LoggingSettings = LoggingSettings()
    google_ai: GoogleAISettings = GoogleAISettings()  # type: ignore[call-arg]
    openai: OpenAISettings = OpenAISettings()  # type: ignore[call-arg]


def get_settings() -> Settings:
    return Settings()
