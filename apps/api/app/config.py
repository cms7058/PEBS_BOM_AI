from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

def _find_env_root() -> Path:
    """Find a sensible env root in both native dev and Docker images."""
    current = Path(__file__).resolve()
    for directory in current.parents:
        if (directory / ".env").exists() or (directory / ".env.production").exists():
            return directory
        if (directory / "pnpm-workspace.yaml").exists() or (directory / "docker-compose.prod.yml").exists():
            return directory
    return Path.cwd()


_PROJECT_ROOT = _find_env_root()


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
    # Deployment modes:
    # - private: customer private deployment, normally single tenant
    # - cloud: PEBS hosted/cloud deployment, tenant resolved by auth later
    deployment_mode: str = "private"
    # Tenant mode:
    # - single: every row uses default_tenant_id
    # - multi: future cloud mode reads tenant from auth/request context
    tenant_mode: str = "single"
    default_tenant_id: str = "default"
    # Native dev keeps create_all + SQLite shim for fast iteration. Production
    # should set DB_AUTO_CREATE=false and run Alembic migrations explicitly.
    db_auto_create: bool = True

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
