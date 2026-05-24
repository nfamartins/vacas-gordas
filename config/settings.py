"""Configurações centrais da aplicação carregadas a partir do arquivo .env."""
from __future__ import annotations

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """
    Configurações da aplicação lidas de variáveis de ambiente / arquivo .env.

    Uso:
        from config.settings import settings
        print(settings.mongo_uri)
    """

    # ── MongoDB ───────────────────────────────────────────────────────────────
    mongo_uri: str = Field(default="mongodb://localhost:27017", alias="MONGO_URI")
    mongo_db: str = Field(default="vacas_gordas", alias="MONGO_DB")

    # ── Anthropic ─────────────────────────────────────────────────────────────
    anthropic_api_key: str = Field(default="", alias="ANTHROPIC_API_KEY")
    anthropic_model_categorizer: str = Field(
        default="claude-haiku-4-5-20251001",
        alias="ANTHROPIC_MODEL_CATEGORIZER",
    )
    anthropic_model_chat: str = Field(
        default="claude-sonnet-4-6",
        alias="ANTHROPIC_MODEL_CHAT",
    )

    # ── Aplicação ─────────────────────────────────────────────────────────────
    app_env: str = Field(default="development", alias="APP_ENV")
    app_debug: bool = Field(default=True, alias="APP_DEBUG")

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        populate_by_name=True,
        extra="ignore",
    )

    @property
    def is_production(self) -> bool:
        """Retorna True se o ambiente configurado é produção."""
        return self.app_env.lower() == "production"

    @property
    def anthropic_configured(self) -> bool:
        """Retorna True se a chave da API Anthropic está definida."""
        return bool(self.anthropic_api_key)


# Singleton — importar este objeto em toda a aplicação
settings = Settings()
