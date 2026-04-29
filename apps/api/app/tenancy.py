"""Tenant resolution.

Current deployment is single-tenant — all data belongs to a fixed tenant id
"default". Multi-tenant comes later via Auth/JWT; making the resolver a
single function means flipping that switch later only touches THIS file
plus the Auth middleware.

Convention used everywhere in the codebase:
  - Every tenant-scoped table has a `tenant_id: str` column
  - Every query filters by `tenant_id == current_tenant()`
  - tenant_id is NOT nullable — single-tenant rows still use "default"
"""

from __future__ import annotations

DEFAULT_TENANT_ID = "default"


def current_tenant() -> str:
    """Return the currently-authenticated tenant id.

    For now always returns DEFAULT_TENANT_ID. When Auth lands, read this
    from the request context (e.g. via FastAPI Depends).
    """
    return DEFAULT_TENANT_ID
