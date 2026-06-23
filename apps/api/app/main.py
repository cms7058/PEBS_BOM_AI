from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import text

from app.config import settings
from app.db import engine
from app.models import Base
from app.routes import admin, agent, amiba, amiba_workbench, bom, brands, categories, export, hierarchy, parts, upload
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


async def _ensure_dev_sqlite_schema(conn) -> None:
    """Tiny dev migration shim for SQLite.

    create_all() creates new tables but does not add columns to existing local
    DBs. Alembic should replace this before production.
    """
    if not settings.database_url.startswith("sqlite"):
        return
    rows = await conn.execute(text("PRAGMA table_info(bom_nodes)"))
    cols = {row[1] for row in rows.fetchall()}
    if "part_id" not in cols:
        await conn.execute(text("ALTER TABLE bom_nodes ADD COLUMN part_id VARCHAR(36)"))
    if "mapping_status" not in cols:
        await conn.execute(
            text("ALTER TABLE bom_nodes ADD COLUMN mapping_status VARCHAR(16) DEFAULT 'unmapped'")
        )
    rows = await conn.execute(text("PRAGMA table_info(parts)"))
    part_cols = {row[1] for row in rows.fetchall()}
    part_columns = {
        "supplier": "VARCHAR(128)",
        "uom": "VARCHAR(32) DEFAULT 'EA'",
        "unit_cost": "FLOAT",
        "typical_lead_time": "VARCHAR(64)",
        "status": "VARCHAR(16) DEFAULT 'active'",
        "usage_count": "INTEGER DEFAULT 0",
        "last_used_at": "DATETIME",
    }
    for name, ddl in part_columns.items():
        if name not in part_cols:
            await conn.execute(text(f"ALTER TABLE parts ADD COLUMN {name} {ddl}"))
    await conn.execute(
        text(
            "CREATE TABLE IF NOT EXISTS part_import_drafts ("
            "id VARCHAR(36) PRIMARY KEY, "
            "tenant_id VARCHAR(64), "
            "source_type VARCHAR(32), "
            "status VARCHAR(16), "
            "rows JSON, "
            "created_by VARCHAR(128), "
            "created_at DATETIME, "
            "confirmed_at DATETIME)"
        )
    )
    rows = await conn.execute(text("PRAGMA table_info(subscription_plans)"))
    plan_cols = {row[1] for row in rows.fetchall()}
    plan_columns = {
        "price_cents": "INTEGER DEFAULT 0",
        "currency": "VARCHAR(8) DEFAULT 'CNY'",
        "duration_days": "INTEGER DEFAULT 365",
    }
    for name, ddl in plan_columns.items():
        if name not in plan_cols:
            await conn.execute(text(f"ALTER TABLE subscription_plans ADD COLUMN {name} {ddl}"))
    rows = await conn.execute(text("PRAGMA table_info(amiba_connectors)"))
    conn_cols = {row[1] for row in rows.fetchall()}
    if conn_cols:  # 表已存在（旧版无同步列）才补列；不存在时 create_all 会建全
        for name, ddl in {
            "last_sync_at": "DATETIME",
            "last_sync_summary": "VARCHAR(512)",
        }.items():
            if name not in conn_cols:
                await conn.execute(text(f"ALTER TABLE amiba_connectors ADD COLUMN {name} {ddl}"))
    rows = await conn.execute(text("PRAGMA table_info(bom_projects)"))
    proj_cols = {row[1] for row in rows.fetchall()}
    if proj_cols and "bom_id" not in proj_cols:
        await conn.execute(text("ALTER TABLE bom_projects ADD COLUMN bom_id VARCHAR(36)"))


@asynccontextmanager
async def lifespan(app: FastAPI):
    _ensure_local_dirs()
    if settings.db_auto_create:
        # Native-dev schema bootstrap. Production/private deployments should
        # set DB_AUTO_CREATE=false and run Alembic migrations explicitly.
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
            await _ensure_dev_sqlite_schema(conn)
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
app.include_router(brands.router)
app.include_router(categories.router)
app.include_router(parts.router)
app.include_router(admin.router)
app.include_router(amiba.router)
app.include_router(amiba_workbench.router)


@app.get("/health")
async def health() -> dict:
    return {
        "ok": True,
        "provider": settings.llm_provider,
        "model": settings.llm_model,
        "storage": settings.storage_backend,
        "deployment_mode": settings.deployment_mode,
        "tenant_mode": settings.tenant_mode,
        "has_api_key": bool(settings.minimax_plan_api_key),
    }
