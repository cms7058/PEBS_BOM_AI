from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.config import settings
from app.db import engine
from app.models import Base
from app.routes import agent, bom, export, hierarchy, upload
from app.services.storage import store


def _ensure_local_dirs() -> None:
    """Create SQLite parent dir + local upload dir if they don't exist."""
    # SQLite file parent
    if settings.database_url.startswith("sqlite"):
        # urls look like sqlite+aiosqlite:///./data/app.db
        _, _, path = settings.database_url.partition(":///")
        if path:
            Path(path).expanduser().parent.mkdir(parents=True, exist_ok=True)
    # Local object store path
    if settings.storage_backend == "local":
        Path(settings.storage_local_path).expanduser().mkdir(parents=True, exist_ok=True)


@asynccontextmanager
async def lifespan(app: FastAPI):
    _ensure_local_dirs()
    # Dev-mode schema bootstrap. Alembic migrations replace this in prod.
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    store.ensure_bucket()
    yield


app = FastAPI(title="PEBS BOM API", version="0.0.1", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=[o.strip() for o in settings.api_cors_origins.split(",") if o.strip()],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(upload.router)
app.include_router(bom.router)
app.include_router(hierarchy.router)
app.include_router(agent.router)
app.include_router(export.router)


@app.get("/health")
async def health() -> dict:
    return {
        "ok": True,
        "provider": settings.llm_provider,
        "model": settings.llm_model,
        "storage": settings.storage_backend,
        "has_api_key": bool(settings.minimax_plan_api_key),
    }
