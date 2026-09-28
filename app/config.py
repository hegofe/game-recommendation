from typing import Literal

from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    # AWS endpoint and credentials are read by boto3 from the standard AWS_* env vars.
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    s3_bucket: str = "game-catalog"
    catalog_key: str = "catalog.json"
    plays_table: str = "plays"

    redis_url: str = "redis://localhost:6379/0"

    llm_provider: Literal["gemini", "ollama"] = "gemini"
    google_api_key: SecretStr | None = None
    gemini_model: str = "gemini-3.5-flash-lite"
    ollama_base_url: str = "http://localhost:11434"
    ollama_model: str = "qwen2.5:1.5b"
    llm_timeout_seconds: float = 90.0
    llm_max_concurrency: int = 2

    num_recommendations: int = 5
    history_limit: int = 100
    full_personalization_plays: int = 10
    max_per_provider: int = 3
    reco_cache_ttl_seconds: int = 24 * 60 * 60


settings = Settings()
