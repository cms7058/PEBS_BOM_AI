"""amiba workbench tables

补建阿米巴接入 + 平台登录 + 按产品建项目 + 多人计时 的表。
这些 model 在 288a097 / 832901f 引入，但当时漏了配套迁移，导致
amiba_connectors / amiba_platform_sessions / bom_projects / bom_tasks
四张表从未在生产库创建，/amiba/* 接口一查表即 500。

Revision ID: 0003_amiba_workbench
Revises: 0002_internal_beta_limits
Create Date: 2026-06-25
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0003_amiba_workbench"
down_revision = "0002_internal_beta_limits"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "amiba_connectors",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("tenant_id", sa.String(length=64), nullable=False, server_default="default"),
        sa.Column("enterprise_id", sa.String(length=64), nullable=False),
        sa.Column("source", sa.String(length=32), nullable=False, server_default="bom"),
        sa.Column("amiba_endpoint", sa.String(length=512), nullable=False),
        sa.Column("amiba_token", sa.String(length=128), nullable=False),
        sa.Column("label", sa.String(length=256), nullable=True),
        sa.Column("capabilities", sa.JSON(), nullable=True),
        sa.Column("connected_at", sa.DateTime(), nullable=False),
        sa.Column("last_hello_at", sa.DateTime(), nullable=True),
        sa.Column("hello_ok", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("hello_error", sa.String(length=512), nullable=True),
        sa.Column("active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("last_sync_at", sa.DateTime(), nullable=True),
        sa.Column("last_sync_summary", sa.String(length=512), nullable=True),
    )
    op.create_index("ix_amiba_connectors_tenant_id", "amiba_connectors", ["tenant_id"])
    op.create_index("ix_amiba_connectors_enterprise_id", "amiba_connectors", ["enterprise_id"])
    op.create_index("ix_amiba_connectors_active", "amiba_connectors", ["active"])

    op.create_table(
        "amiba_platform_sessions",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("session_token", sa.String(length=64), nullable=False),
        sa.Column("username", sa.String(length=128), nullable=False),
        sa.Column("display_name", sa.String(length=128), nullable=True),
        sa.Column("amiba_user_id", sa.String(length=64), nullable=True),
        sa.Column("amiba_endpoint", sa.String(length=512), nullable=False),
        sa.Column("tool", sa.String(length=32), nullable=False, server_default="bom"),
        sa.Column("paid_plan", sa.String(length=32), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("last_seen_at", sa.DateTime(), nullable=False),
        sa.Column("active", sa.Boolean(), nullable=False, server_default=sa.true()),
    )
    op.create_index("ix_amiba_platform_sessions_session_token", "amiba_platform_sessions", ["session_token"], unique=True)
    op.create_index("ix_amiba_platform_sessions_username", "amiba_platform_sessions", ["username"])
    op.create_index("ix_amiba_platform_sessions_active", "amiba_platform_sessions", ["active"])

    op.create_table(
        "bom_projects",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("tenant_id", sa.String(length=64), nullable=False, server_default="default"),
        sa.Column("mode", sa.String(length=16), nullable=False, server_default="amiba"),
        sa.Column("bom_id", sa.String(length=36), nullable=True),
        sa.Column("enterprise_id", sa.String(length=64), nullable=True),
        sa.Column("enterprise_name", sa.String(length=256), nullable=True),
        sa.Column("amiba_product_id", sa.String(length=64), nullable=True),
        sa.Column("part_no", sa.String(length=128), nullable=True),
        sa.Column("product_name", sa.String(length=256), nullable=True),
        sa.Column("amiba_endpoint", sa.String(length=512), nullable=True),
        sa.Column("connector_token", sa.String(length=128), nullable=True),
        sa.Column("created_by_username", sa.String(length=128), nullable=True),
        sa.Column("labor_rate", sa.Float(), nullable=False, server_default="80.0"),
        sa.Column("started_at", sa.DateTime(), nullable=False),
        sa.Column("submitted_at", sa.DateTime(), nullable=True),
        sa.Column("status", sa.String(length=16), nullable=False, server_default="active"),
    )
    op.create_index("ix_bom_projects_tenant_id", "bom_projects", ["tenant_id"])
    op.create_index("ix_bom_projects_bom_id", "bom_projects", ["bom_id"])
    op.create_index("ix_bom_projects_enterprise_id", "bom_projects", ["enterprise_id"])
    op.create_index("ix_bom_projects_amiba_product_id", "bom_projects", ["amiba_product_id"])
    op.create_index("ix_bom_projects_status", "bom_projects", ["status"])

    op.create_table(
        "bom_tasks",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("project_id", sa.String(length=36), sa.ForeignKey("bom_projects.id"), nullable=False),
        sa.Column("assignee_username", sa.String(length=128), nullable=True),
        sa.Column("assignee_display", sa.String(length=128), nullable=True),
        sa.Column("scope", sa.String(length=256), nullable=False, server_default="BOM 编制"),
        sa.Column("status", sa.String(length=16), nullable=False, server_default="todo"),
        sa.Column("active_seconds", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("running_since", sa.DateTime(), nullable=True),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
    )
    op.create_index("ix_bom_tasks_project_id", "bom_tasks", ["project_id"])


def downgrade() -> None:
    op.drop_table("bom_tasks")
    op.drop_table("bom_projects")
    op.drop_table("amiba_platform_sessions")
    op.drop_table("amiba_connectors")
