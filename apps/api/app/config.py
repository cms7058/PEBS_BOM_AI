from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

# Project root .env lives two levels above apps/api/
# app/config.py -> app/ -> apps/api -> apps -> <root>
_PROJECT_ROOT = Path(__file__).resolve().parents[3]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        # Look for .env at project root first, then apps/api/.env as fallback
        env_file=(_PROJECT_ROOT / ".env", Path(".env")),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # LLM — default provider used when a chat request omits the `model` field.
    llm_provider: str = "minimaxPlan"
    llm_model: str = "MiniMax-M2.7"
    llm_temperature: float = 0.3
    llm_max_tokens: int = 8192

    # MiniMax Plan (Anthropic-compatible endpoint)
    minimax_plan_api_key: str = ""
    minimax_plan_base_url: str = "https://api.minimaxi.com/anthropic"

    # DeepSeek (OpenAI-compatible endpoint)
    # Real model id is whatever DeepSeek currently exposes — set via env var
    # if the default below stops matching their published name.
    deepseek_api_key: str = ""
    deepseek_base_url: str = "https://api.deepseek.com"
    deepseek_model: str = "deepseek-v4-pro"

    # Database — SQLite by default for native dev. Override via env for Postgres.
    database_url: str = "sqlite+aiosqlite:///./data/app.db"

    # Storage backend: "local" (filesystem) or "minio"
    storage_backend: str = "local"
    storage_local_path: str = "./data/uploads"

    # MinIO (only when storage_backend=minio)
    minio_endpoint: str = "minio:9000"
    minio_root_user: str = "admin"
    minio_root_password: str = "admin12345"
    minio_bucket: str = "pebs-bom"
    minio_secure: bool = False

    # HTTP
    api_cors_origins: str = "http://localhost:3000"


settings = Settings()
