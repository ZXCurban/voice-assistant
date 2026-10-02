"""Application settings loaded from environment (12-factor)."""

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Runtime configuration. Extend with new sections, do not hardcode secrets."""

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    app_name: str = "hackathon-skeleton"
    app_env: str = "local"
    app_debug: bool = False
    # Reserved for future domain endpoints. Not applied to GET /health,
    # which stays at the root as a stable public contract.
    api_v1_prefix: str = "/api/v1"
    log_level: str = "INFO"

    database_url: str = "postgresql+asyncpg://postgres:postgres@localhost:5432/app"
    redis_url: str = "redis://localhost:6379/0"

    # Local LLM (llama-server, OpenAI-compatible HTTP). No secrets here.
    llm_base_url: str = "http://127.0.0.1:8080"
    llm_model: str = "clinic-assistant"
    llm_timeout_s: float = 120.0
    llm_max_tokens: int = 512
    llm_temperature: float = 0.3


@lru_cache
def get_settings() -> Settings:
    """Return cached settings instance."""
    return Settings()
