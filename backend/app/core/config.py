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

    # Local LLM (OpenAI-compatible HTTP: llama-server, vLLM, Ollama, …).
    # The model name is config only — prompts/tools carry no model-specific
    # tokens, so swapping models is a config change, not a code change.
    # No secrets here.
    llm_base_url: str = "http://127.0.0.1:8080"
    llm_model: str = "clinic-assistant"
    llm_timeout_s: float = 300.0
    llm_max_tokens: int = 512
    llm_temperature: float = 0.1
    # Payload dialect selector (only "openai-compatible" is implemented;
    # new dialects plug in via app.ai.client without touching prompts).
    llm_provider: str = "openai-compatible"
    # Hybrid reasoning models (e.g. Qwen3) need an explicit flag to put
    # tokens into `content` instead of `reasoning_content`. Templates that
    # do not know this variable ignore it, so leaving it off is safe for
    # other models; set true only for models where you want thinking on.
    llm_enable_thinking: bool = False
    llm_tool_choice: str = "auto"


@lru_cache
def get_settings() -> Settings:
    """Return cached settings instance."""
    return Settings()
