from datetime import datetime
from uuid import uuid4

from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    pass


def _uuid() -> str:
    return str(uuid4())


class UploadedFile(Base):
    __tablename__ = "uploaded_files"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    filename: Mapped[str] = mapped_column(String(512))
    content_type: Mapped[str] = mapped_column(String(128))
    object_key: Mapped[str] = mapped_column(String(512))
    size_bytes: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class BOM(Base):
    __tablename__ = "boms"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    name: Mapped[str] = mapped_column(String(256))
    source_file_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("uploaded_files.id"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, onupdate=datetime.utcnow
    )

    nodes: Mapped[list["BOMNode"]] = relationship(
        back_populates="bom", cascade="all, delete-orphan"
    )


class BOMNodeEdit(Base):
    """Audit-log row written every time a BOMNode field is patched."""

    __tablename__ = "bom_node_edits"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    bom_id: Mapped[str] = mapped_column(String(36), ForeignKey("boms.id"), index=True)
    node_id: Mapped[str] = mapped_column(String(36), ForeignKey("bom_nodes.id"), index=True)
    # Snapshot the part name at edit time so log stays readable even if the
    # node is later renamed or deleted.
    node_label: Mapped[str | None] = mapped_column(String(256), nullable=True)
    field: Mapped[str] = mapped_column(String(64))
    old_value: Mapped[str | None] = mapped_column(Text, nullable=True)
    new_value: Mapped[str | None] = mapped_column(Text, nullable=True)
    user_name: Mapped[str] = mapped_column(String(128), default="anonymous")
    source: Mapped[str] = mapped_column(String(32), default="table")  # table | agent | hierarchy
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, index=True)


class BOMNode(Base):
    __tablename__ = "bom_nodes"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    bom_id: Mapped[str] = mapped_column(String(36), ForeignKey("boms.id"))
    parent_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("bom_nodes.id"), nullable=True
    )

    level: Mapped[int] = mapped_column(Integer, default=0)
    part_number: Mapped[str | None] = mapped_column(String(128), nullable=True)
    part_name: Mapped[str] = mapped_column(String(256))
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    quantity: Mapped[float] = mapped_column(Float, default=1.0)
    uom: Mapped[str] = mapped_column(String(32), default="EA")
    material: Mapped[str | None] = mapped_column(String(128), nullable=True)
    weight: Mapped[float | None] = mapped_column(Float, nullable=True)
    supplier: Mapped[str | None] = mapped_column(String(256), nullable=True)
    unit_cost: Mapped[float | None] = mapped_column(Float, nullable=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)

    # Non-standard component classification.
    # `category_id` references ComponentCategory.id (e.g. "linear_guide").
    # `spec` holds the structured parameter values whose keys are defined by
    # that category's parameter schema (e.g. {"rail_width":25,"length":1500}).
    # Both are nullable so legacy rows stay valid; agent fills them in via
    # the bom_classify_* tools.
    category_id: Mapped[str | None] = mapped_column(
        String(64), ForeignKey("component_categories.id"), nullable=True
    )
    spec: Mapped[dict] = mapped_column(JSON, default=dict)

    # Customer-confirmed material mapping. This is the first layer of user data
    # sedimentation: raw BOM rows can be linked to a tenant's standard Part.
    part_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("parts.id"), nullable=True
    )
    mapping_status: Mapped[str] = mapped_column(String(16), default="unmapped")

    # UI metadata (used by G6 for style overrides, set by Agent)
    style: Mapped[dict] = mapped_column(JSON, default=dict)
    # Link back to source: {"type": "excel_row", "row": 12} | {"type": "cad_node", "id": "..."}
    source_ref: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    confidence: Mapped[float] = mapped_column(Float, default=1.0)

    sort_order: Mapped[int] = mapped_column(Integer, default=0)

    # ─── MBOM scaffolding ────────────────────────────────────────────────
    # Today's product is PBOM (Production / Planning BOM) — what the BOM
    # contains and where it's sourced. MBOM (Manufacturing BOM) is the next
    # logical expansion: same parts viewed by ASSEMBLY OPERATION SEQUENCE,
    # with fixtures/jigs, standard time, and consumables-per-operation.
    #
    # We're not building MBOM features yet (out of scope until ≥ 30 paying
    # PBOM customers — see business analysis). But adding nullable columns
    # now means future MBOM features land without a schema migration on
    # production customer data.
    #
    # Semantics when these are populated (post-MBOM-launch):
    #   · operation_seq      this row IS an assembly operation step
    #                        (e.g. 10, 20, 30 — same convention as ERPs)
    #   · operation_desc     human description of what happens at this step
    #                        (e.g. "把气缸装到底板上，扭矩 8 N·m")
    #   · fixture_ref        jig/fixture used (FK to fixture lib later;
    #                        free text "JIG-001" for now)
    #   · consumed_by_op     this row is a consumable / sub-part used by
    #                        another row whose operation_seq matches
    #   · standard_time_min  rated cycle time for the operation in minutes
    #
    # All null until a customer buys the MBOM module. Backwards compatible
    # with every existing API call — current PBOM serializers ignore them.
    operation_seq: Mapped[int | None] = mapped_column(Integer, nullable=True)
    operation_desc: Mapped[str | None] = mapped_column(Text, nullable=True)
    fixture_ref: Mapped[str | None] = mapped_column(String(128), nullable=True)
    consumed_by_op: Mapped[int | None] = mapped_column(Integer, nullable=True)
    standard_time_min: Mapped[float | None] = mapped_column(Float, nullable=True)

    bom: Mapped[BOM] = relationship(back_populates="nodes")
    category: Mapped["ComponentCategory | None"] = relationship(lazy="joined")
    part: Mapped["Part | None"] = relationship(lazy="joined")

    @property
    def category_name(self) -> str | None:
        """Convenience denormalisation for API responses + UI rendering.
        Avoids a second round trip to fetch ComponentCategory just to show
        the Chinese label on the node card.
        """
        return self.category.name_zh if self.category else None


class ComponentCategory(Base):
    """Brand-agnostic taxonomy of non-standard mechanical components.

    Each row defines a *type* of part (linear guide, ball screw, dowel pin…)
    plus the parameter schema engineers need to fill in to fully describe
    one. Categories are deliberately vendor-neutral — specific brand SKUs
    live in the (future) brand_entries table, not here.

    Why this matters: 国标件 (M8 hex bolt etc.) is commodity — engineers
    don't need help mapping those. The pain is in non-std precision parts
    where each vendor uses its own part-number system. This table is the
    common ground that lets us cross-reference HIWIN / THK / Misumi /
    Yintai without storing any single vendor's catalog.
    """

    __tablename__ = "component_categories"

    # Primary key uses a stable English slug, not UUID, so seed data and
    # imports can reference categories by name (e.g. "linear_guide").
    id: Mapped[str] = mapped_column(String(64), primary_key=True)

    parent_id: Mapped[str | None] = mapped_column(
        String(64), ForeignKey("component_categories.id"), nullable=True
    )

    name_zh: Mapped[str] = mapped_column(String(128))
    name_en: Mapped[str] = mapped_column(String(128))
    description: Mapped[str | None] = mapped_column(Text, nullable=True)

    # Parameter schema — list of dicts:
    #   {name, label_zh, unit?, type: "enum"|"number"|"integer"|"string",
    #    values?: [...], required?: bool, default?: any}
    parameters: Mapped[list] = mapped_column(JSON, default=list)

    # Vendor-neutral list of brands typically active in this category.
    # Free-form list of brand display names — not tied to brand_entries yet.
    common_brands: Mapped[list] = mapped_column(JSON, default=list)

    typical_use: Mapped[str | None] = mapped_column(Text, nullable=True)

    # Reference to GB/ISO/DIN if this category overlaps with a standard
    # part type (most non-std categories will leave this null).
    related_gb: Mapped[str | None] = mapped_column(String(128), nullable=True)

    sort_order: Mapped[int] = mapped_column(Integer, default=0)

    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, onupdate=datetime.utcnow
    )


class Part(Base):
    """Tenant-scoped standard material master.

    A Part is a customer-confirmed engineering fact. The agent may suggest
    mappings, but only a user confirmation should create/link one.
    """

    __tablename__ = "parts"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    tenant_id: Mapped[str] = mapped_column(String(64), default="default", index=True)

    sku_internal: Mapped[str | None] = mapped_column(String(128), nullable=True)
    name_standard: Mapped[str] = mapped_column(String(256))
    part_number: Mapped[str | None] = mapped_column(String(128), nullable=True)
    category_id: Mapped[str | None] = mapped_column(
        String(64), ForeignKey("component_categories.id"), nullable=True
    )
    brand: Mapped[str | None] = mapped_column(String(128), nullable=True)
    supplier: Mapped[str | None] = mapped_column(String(128), nullable=True)
    uom: Mapped[str] = mapped_column(String(32), default="EA")
    unit_cost: Mapped[float | None] = mapped_column(Float, nullable=True)
    typical_lead_time: Mapped[str | None] = mapped_column(String(64), nullable=True)
    status: Mapped[str] = mapped_column(String(16), default="active")
    usage_count: Mapped[int] = mapped_column(Integer, default=0)
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    spec: Mapped[dict] = mapped_column(JSON, default=dict)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_by: Mapped[str | None] = mapped_column(String(128), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, onupdate=datetime.utcnow
    )

    category: Mapped["ComponentCategory | None"] = relationship(lazy="joined")
    aliases: Mapped[list["PartAlias"]] = relationship(
        back_populates="part", cascade="all, delete-orphan"
    )

    @property
    def category_name(self) -> str | None:
        return self.category.name_zh if self.category else None

    __table_args__ = (
        Index("ix_parts_tenant_name", "tenant_id", "name_standard"),
        Index("ix_parts_tenant_number", "tenant_id", "part_number"),
    )


class PartAlias(Base):
    """Raw engineer wording that has been confirmed to refer to a Part."""

    __tablename__ = "part_aliases"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    tenant_id: Mapped[str] = mapped_column(String(64), default="default", index=True)
    part_id: Mapped[str] = mapped_column(String(36), ForeignKey("parts.id"), index=True)
    raw_name: Mapped[str] = mapped_column(String(256))
    raw_part_number: Mapped[str | None] = mapped_column(String(128), nullable=True)
    source_node_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("bom_nodes.id"), nullable=True
    )
    status: Mapped[str] = mapped_column(String(16), default="confirmed")
    confidence: Mapped[float] = mapped_column(Float, default=1.0)
    confirmed_by: Mapped[str | None] = mapped_column(String(128), nullable=True)
    confirmed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    part: Mapped[Part] = relationship(back_populates="aliases")

    __table_args__ = (
        Index("ix_part_aliases_tenant_raw_name", "tenant_id", "raw_name"),
        Index("ix_part_aliases_tenant_raw_number", "tenant_id", "raw_part_number"),
    )


class PartImportDraft(Base):
    """Staged standard-material import/change set.

    Agent-generated CRUD must be confirmed by a human before it writes into
    the tenant material master.
    """

    __tablename__ = "part_import_drafts"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    tenant_id: Mapped[str] = mapped_column(String(64), default="default", index=True)
    source_type: Mapped[str] = mapped_column(String(32), default="chat")
    status: Mapped[str] = mapped_column(String(16), default="draft")
    rows: Mapped[list] = mapped_column(JSON, default=list)
    created_by: Mapped[str | None] = mapped_column(String(128), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    confirmed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class BrandEntry(Base):
    """Per-tenant supplier brand knowledge — the layer that turns generic
    LLM brand recommendations into tenant-specific AVL-aware suggestions.

    Tenancy: every row has tenant_id. Queries MUST filter by it. We never
    surface a tenant's brands to a different tenant unless the row's
    visibility is 'shared'.

    Sources of entries:
      - "chat":  user told the agent "我们用 X 牌"
      - "file":  imported from an Excel/CSV AVL upload (TODO)
      - "url":   scraped from a brand homepage user pasted (gated, TODO)
      - "system": curated baseline shipped with the product

    Recommendation order at query time:
      1. tenant's own private entries  (visibility='private', highest trust)
      2. tenant's own shared entries   (visibility='shared')
      3. other tenants' shared entries (community pool)
      4. LLM's general knowledge fallback (handled in agent prompt, not DB)
    """

    __tablename__ = "brand_entries"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)

    # The pivot. Every query filters by this. NEVER nullable.
    tenant_id: Mapped[str] = mapped_column(String(64), default="default", index=True)

    # Display name as user would write it ("HIWIN 上银").
    name: Mapped[str] = mapped_column(String(128))
    # Other ways this brand might be referenced — used for fuzzy match.
    aliases: Mapped[list] = mapped_column(JSON, default=list)
    # Optional homepage / catalogue URL.
    url: Mapped[str | None] = mapped_column(String(512), nullable=True)
    region: Mapped[str | None] = mapped_column(String(64), nullable=True)

    # List of component_categories.id this brand makes products for.
    categories: Mapped[list] = mapped_column(JSON, default=list)

    # Free-form business attributes; intentionally string-typed so they can
    # carry whatever vocabulary a customer uses ("高端"/"高"/"premium").
    price_tier: Mapped[str | None] = mapped_column(String(32), nullable=True)
    typical_lead_time: Mapped[str | None] = mapped_column(String(64), nullable=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)

    # Provenance + governance
    source: Mapped[str] = mapped_column(String(16), default="chat")
    source_ref: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    visibility: Mapped[str] = mapped_column(String(16), default="private")
    created_by: Mapped[str | None] = mapped_column(String(128), nullable=True)
    confidence: Mapped[float] = mapped_column(Float, default=1.0)
    upvotes: Mapped[int] = mapped_column(Integer, default=0)
    flagged: Mapped[bool] = mapped_column(Boolean, default=False)

    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, onupdate=datetime.utcnow
    )

    __table_args__ = (
        # Most queries hit (tenant_id, name) for dedup checks.
        Index("ix_brand_entries_tenant_name", "tenant_id", "name"),
        # Recommendation queries scan by (tenant_id, visibility).
        Index("ix_brand_entries_tenant_visibility", "tenant_id", "visibility"),
    )


class SubscriptionPlan(Base):
    """Admin-defined product packaging for personal/team/enterprise tenants."""

    __tablename__ = "subscription_plans"

    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    name: Mapped[str] = mapped_column(String(64))
    tenant_type: Mapped[str] = mapped_column(String(16), index=True)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    price_label: Mapped[str | None] = mapped_column(String(64), nullable=True)
    price_cents: Mapped[int] = mapped_column(Integer, default=0)
    currency: Mapped[str] = mapped_column(String(8), default="CNY")
    duration_days: Mapped[int] = mapped_column(Integer, default=365)
    seat_limit: Mapped[int | None] = mapped_column(Integer, nullable=True)
    bom_limit: Mapped[int | None] = mapped_column(Integer, nullable=True)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    sort_order: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, onupdate=datetime.utcnow
    )


class Tenant(Base):
    """Tenant shell for cloud multi-tenant mode and private single-tenant mode."""

    __tablename__ = "tenants"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    name: Mapped[str] = mapped_column(String(128))
    tenant_type: Mapped[str] = mapped_column(String(16), default="personal", index=True)
    subscription_plan_id: Mapped[str] = mapped_column(
        String(32), ForeignKey("subscription_plans.id"), default="personal"
    )
    status: Mapped[str] = mapped_column(String(16), default="active", index=True)
    owner_name: Mapped[str | None] = mapped_column(String(128), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, onupdate=datetime.utcnow
    )

    plan: Mapped["SubscriptionPlan | None"] = relationship(lazy="joined")


class FeatureFlag(Base):
    """Per-plan feature switch, editable by administrators."""

    __tablename__ = "feature_flags"

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=_uuid)
    plan_id: Mapped[str] = mapped_column(
        String(32), ForeignKey("subscription_plans.id"), index=True
    )
    feature_key: Mapped[str] = mapped_column(String(64), index=True)
    feature_name: Mapped[str] = mapped_column(String(128))
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    config: Mapped[dict] = mapped_column(JSON, default=dict)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, onupdate=datetime.utcnow
    )

    plan: Mapped[SubscriptionPlan] = relationship(lazy="joined")

    __table_args__ = (
        Index("ix_feature_flags_plan_feature", "plan_id", "feature_key"),
    )


class AppUser(Base):
    """Application user shell for admin login and future tenant membership."""

    __tablename__ = "app_users"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    tenant_id: Mapped[str] = mapped_column(String(64), default="default", index=True)
    username: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    display_name: Mapped[str] = mapped_column(String(128))
    role: Mapped[str] = mapped_column(String(32), default="member", index=True)
    email: Mapped[str | None] = mapped_column(String(128), nullable=True)
    phone: Mapped[str | None] = mapped_column(String(64), nullable=True)
    password_hash: Mapped[str] = mapped_column(String(128))
    status: Mapped[str] = mapped_column(String(16), default="active", index=True)
    trial_expires_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    bom_import_limit: Mapped[int | None] = mapped_column(Integer, nullable=True)
    bom_export_limit: Mapped[int | None] = mapped_column(Integer, nullable=True)
    bom_import_count: Mapped[int] = mapped_column(Integer, default=0)
    bom_export_count: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, onupdate=datetime.utcnow
    )


class EmailVerificationCode(Base):
    """One-time email verification code for registration."""

    __tablename__ = "email_verification_codes"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    email: Mapped[str] = mapped_column(String(128), index=True)
    code_hash: Mapped[str] = mapped_column(String(128))
    purpose: Mapped[str] = mapped_column(String(32), default="register", index=True)
    status: Mapped[str] = mapped_column(String(16), default="pending", index=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    verified_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class PaymentOrder(Base):
    """Subscription payment order.

    Current implementation is a mock payment provider that marks orders paid
    when the user confirms. The schema is intentionally provider-neutral so it
    can later store WeChat Pay / Alipay / Stripe IDs and callbacks.
    """

    __tablename__ = "payment_orders"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    plan_id: Mapped[str] = mapped_column(
        String(32), ForeignKey("subscription_plans.id"), index=True
    )
    email: Mapped[str] = mapped_column(String(128), index=True)
    amount_cents: Mapped[int] = mapped_column(Integer, default=0)
    currency: Mapped[str] = mapped_column(String(8), default="CNY")
    duration_days: Mapped[int] = mapped_column(Integer, default=365)
    provider: Mapped[str] = mapped_column(String(32), default="mock")
    status: Mapped[str] = mapped_column(String(16), default="pending", index=True)
    checkout_url: Mapped[str | None] = mapped_column(String(512), nullable=True)
    provider_ref: Mapped[str | None] = mapped_column(String(128), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    paid_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    plan: Mapped["SubscriptionPlan | None"] = relationship(lazy="joined")


class AmibaConnector(Base):
    """阿米巴动态智能体接入配置。

    PEBS BOM 作为子工具被接入阿米巴时，阿米巴在「工具接入」页生成连接器令牌，
    携带 amiba_endpoint / amiba_token / enterprise_id / source 跳到本系统的
    `/register` 落地页。落地页把这些参数提交给后端落库为一条记录，后端随即回调
    阿米巴的 `/api/connectors/hello` 完成能力上报（Phase 1）。后续 Phase 2 会用
    这里保存的 endpoint + token 把 BOM 指标回填到阿米巴对应 OTD 节点。
    """

    __tablename__ = "amiba_connectors"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    tenant_id: Mapped[str] = mapped_column(String(64), default="default", index=True)
    enterprise_id: Mapped[str] = mapped_column(String(64), index=True)
    source: Mapped[str] = mapped_column(String(32), default="bom")
    amiba_endpoint: Mapped[str] = mapped_column(String(512))
    amiba_token: Mapped[str] = mapped_column(String(128))
    label: Mapped[str | None] = mapped_column(String(256), nullable=True)
    capabilities: Mapped[list | None] = mapped_column(JSON, nullable=True)
    connected_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    last_hello_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    hello_ok: Mapped[bool] = mapped_column(Boolean, default=False)
    hello_error: Mapped[str | None] = mapped_column(String(512), nullable=True)
    active: Mapped[bool] = mapped_column(Boolean, default=True, index=True)
    # Phase 2：指标回填同步状态
    last_sync_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    last_sync_summary: Mapped[str | None] = mapped_column(String(512), nullable=True)
