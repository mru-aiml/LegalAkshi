"""Application configuration (pydantic-settings). Never commit real credentials."""
from __future__ import annotations

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    DATABASE_URL: str = "postgresql://postgres:postgres@localhost:5432/legalakshi"
    CORS_ORIGINS: str = "http://localhost:5174"
    ENGINE_VERSION: str = "1.0.0"
    SCORING_POLICY_CODE: str = "DEFAULT-2026"
    LOW_CONFIDENCE_THRESHOLD: float = 0.6

    @property
    def cors_origins(self) -> list[str]:
        return [o.strip() for o in self.CORS_ORIGINS.split(",") if o.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()
