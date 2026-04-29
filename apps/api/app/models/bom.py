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

    # UI metadata (used by G6 for style overrides, set by Agent)
    style: Mapped[dict] = mapped_column(JSON, default=dict)
    # Link back to source: {"type": "excel_row", "row": 12} | {"type": "cad_node", "id": "..."}
    source_ref: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    confidence: Mapped[float] = mapped_column(Float, default=1.0)

    sort_order: Mapped[int] = mapped_column(Integer, default=0)

    bom: Mapped[BOM] = relationship(back_populates="nodes")
    category: Mapped["ComponentCategory | None"] = relationship(lazy="joined")

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
