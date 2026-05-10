"""internal beta limits

Revision ID: 0002_internal_beta_limits
Revises: 0001_baseline
Create Date: 2026-05-10
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0002_internal_beta_limits"
down_revision = "0001_baseline"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("app_users", sa.Column("trial_expires_at", sa.DateTime(), nullable=True))
    op.add_column("app_users", sa.Column("bom_import_limit", sa.Integer(), nullable=True))
    op.add_column("app_users", sa.Column("bom_export_limit", sa.Integer(), nullable=True))
    op.add_column(
        "app_users",
        sa.Column("bom_import_count", sa.Integer(), nullable=False, server_default="0"),
    )
    op.add_column(
        "app_users",
        sa.Column("bom_export_count", sa.Integer(), nullable=False, server_default="0"),
    )


def downgrade() -> None:
    op.drop_column("app_users", "bom_export_count")
    op.drop_column("app_users", "bom_import_count")
    op.drop_column("app_users", "bom_export_limit")
    op.drop_column("app_users", "bom_import_limit")
    op.drop_column("app_users", "trial_expires_at")
