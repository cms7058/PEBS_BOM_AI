"""Tenant resolution.

Private deployments are normally single-tenant: all data belongs to a fixed
tenant id (default: "default"). PEBS hosted/cloud deployments can switch
TENANT_MODE=multi later and resolve tenant from auth/request context.

Convention used everywhere in the codebase:
  - Every tenant-scoped table has a `tenant_id: str` column
  - Every query filters by `tenant_id == current_tenant()`
  - tenant_id is NOT nullable — single-tenant rows still use "default"
"""

from __future__ import annotations

from app.config import settings

DEFAULT_TENANT_ID = settings.default_tenant_id


def current_tenant() -> str:
    """Return the currently-authenticated tenant id.

    In TENANT_MODE=single, always returns DEFAULT_TENANT_ID.
    In TENANT_MODE=multi, auth middleware should set a request-local tenant
    before this function is expanded.
    """
    return DEFAULT_TENANT_ID
