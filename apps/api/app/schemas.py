from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field


class BOMNodeOut(BaseModel):
    id: str
    parent_id: str | None = None
    level: int = 0
    part_number: str | None = None
    part_name: str
    description: str | None = None
    quantity: float = 1.0
    uom: str = "EA"
    material: str | None = None
    weight: float | None = None
    supplier: str | None = None
    unit_cost: float | None = None
    notes: str | None = None
    style: dict[str, Any] = Field(default_factory=dict)
    source_ref: dict[str, Any] | None = None
    confidence: float = 1.0
    sort_order: int = 0
    # Non-std component classification (filled by agent's bom_classify_* tools).
    category_id: str | None = None
    # Denormalized Chinese label so frontend can render without a 2nd request.
    category_name: str | None = None
    spec: dict[str, Any] = Field(default_factory=dict)
    # User-confirmed standard material mapping.
    part_id: str | None = None
    mapping_status: str = "unmapped"

    # MBOM scaffolding — null on all current data; populated only when the
    # MBOM module is built (post 30 paying PBOM customers, see business
    # analysis). Surfaced in API now so future frontend features can read
    # them without a schema change.
    operation_seq: int | None = None
    operation_desc: str | None = None
    fixture_ref: str | None = None
    consumed_by_op: int | None = None
    standard_time_min: float | None = None

    class Config:
        from_attributes = True


class BOMOut(BaseModel):
    id: str
    name: str
    source_file_id: str | None
    nodes: list[BOMNodeOut]

    class Config:
        from_attributes = True


class BOMListItem(BaseModel):
    id: str
    name: str
    node_count: int


class UploadResponse(BaseModel):
    bom_id: str
    file_id: str
    name: str
    node_count: int


class AgentChatRequest(BaseModel):
    bom_id: str
    message: str
    history: list[dict[str, Any]] = Field(default_factory=list)
    user_name: str | None = None
    # Optional model override. Frontend's model picker passes id like
    # "MiniMax-M2.7" or "deepseek-v4-pro"; backend looks it up in the
    # MODEL_REGISTRY. Falls back to settings.llm_model if absent or unknown.
    model: str | None = None


class PartOut(BaseModel):
    id: str
    sku_internal: str | None = None
    name_standard: str
    part_number: str | None = None
    category_id: str | None = None
    category_name: str | None = None
    brand: str | None = None
    supplier: str | None = None
    uom: str = "EA"
    unit_cost: float | None = None
    typical_lead_time: str | None = None
    status: str = "active"
    usage_count: int = 0
    last_used_at: datetime | None = None
    spec: dict[str, Any] = Field(default_factory=dict)
    notes: str | None = None

    class Config:
        from_attributes = True


class PartListOut(BaseModel):
    items: list[PartOut]
    total: int


class PartReferenceOut(BaseModel):
    bom_id: str
    bom_name: str
    node_id: str
    node_label: str
    part_number: str | None = None
    quantity: float = 1.0
    uom: str = "EA"
    supplier: str | None = None
    unit_cost: float | None = None


class PartAliasOut(BaseModel):
    raw_name: str
    raw_part_number: str | None = None
    confirmed_at: datetime | None = None


class PartDetailOut(BaseModel):
    part: PartOut
    references: list[PartReferenceOut] = Field(default_factory=list)
    aliases: list[PartAliasOut] = Field(default_factory=list)


class PartDraftRowOut(BaseModel):
    action: str = "create"
    name_standard: str
    sku_internal: str | None = None
    part_number: str | None = None
    category_id: str | None = None
    category_name: str | None = None
    brand: str | None = None
    supplier: str | None = None
    uom: str = "EA"
    unit_cost: float | None = None
    typical_lead_time: str | None = None
    notes: str | None = None
    risk: str | None = None


class PartImportDraftOut(BaseModel):
    id: str
    status: str
    source_type: str
    rows: list[PartDraftRowOut]
    created_at: datetime
    confirmed_at: datetime | None = None

    class Config:
        from_attributes = True


class PartImportConfirmOut(BaseModel):
    draft: PartImportDraftOut
    created: list[PartOut] = Field(default_factory=list)


class PartPatch(BaseModel):
    sku_internal: str | None = None
    name_standard: str | None = None
    part_number: str | None = None
    category_id: str | None = None
    brand: str | None = None
    supplier: str | None = None
    uom: str | None = None
    unit_cost: float | None = None
    typical_lead_time: str | None = None
    status: str | None = None
    notes: str | None = None


class ComponentCategoryCreate(BaseModel):
    name_zh: str
    name_en: str | None = None
    parent_id: str | None = None
    description: str | None = None


class BrandCreate(BaseModel):
    name: str
    categories: list[str] = Field(default_factory=list)
    region: str | None = None
    notes: str | None = None


class SuggestionReferenceOut(BaseModel):
    """Best historical BOMNode that triggered a cross-BOM match.

    Set when the suggestion's score was lifted by recognising the same
    component in another (already-mapped) BOM. Lets the UI render a
    clickable "曾在 BOM-X 节点 Y" link so users can jump there for context.
    """

    bom_id: str
    bom_name: str | None = None
    node_id: str
    node_label: str


class PartSuggestionOut(BaseModel):
    part: PartOut
    score: float
    reason: str
    reference: SuggestionReferenceOut | None = None


class MappingStatusOut(BaseModel):
    node_id: str
    status: str
    mapped_part: PartOut | None = None
    suggestions: list[PartSuggestionOut] = Field(default_factory=list)


class MappingScanItemOut(BaseModel):
    node_id: str
    node_label: str
    status: str
    mapped_part: PartOut | None = None
    suggestions: list[PartSuggestionOut] = Field(default_factory=list)


class RiskTagOut(BaseModel):
    code: str
    severity: str  # "info" | "warn" | "critical"
    message: str


class RiskScanItemOut(BaseModel):
    node_id: str
    node_label: str
    tags: list[RiskTagOut] = Field(default_factory=list)


class RiskScanOut(BaseModel):
    bom_id: str
    total_nodes: int
    flagged_nodes: int  # nodes with at least one tag
    severity_counts: dict[str, int] = Field(default_factory=dict)
    items: list[RiskScanItemOut] = Field(default_factory=list)


class MappingScanOut(BaseModel):
    bom_id: str
    total_nodes: int
    confirmed_count: int
    unmapped_count: int
    candidate_count: int
    items: list[MappingScanItemOut] = Field(default_factory=list)


class SubscriptionPlanOut(BaseModel):
    id: str
    name: str
    tenant_type: str
    description: str | None = None
    price_label: str | None = None
    price_cents: int = 0
    currency: str = "CNY"
    duration_days: int = 365
    seat_limit: int | None = None
    bom_limit: int | None = None
    enabled: bool = True
    sort_order: int = 0

    class Config:
        from_attributes = True


class TenantOut(BaseModel):
    id: str
    name: str
    tenant_type: str
    subscription_plan_id: str
    status: str
    owner_name: str | None = None

    class Config:
        from_attributes = True


class AppUserOut(BaseModel):
    id: str
    tenant_id: str
    username: str
    display_name: str
    role: str
    email: str | None = None
    phone: str | None = None
    status: str

    class Config:
        from_attributes = True


class FeatureFlagOut(BaseModel):
    id: str
    plan_id: str
    feature_key: str
    feature_name: str
    description: str | None = None
    enabled: bool
    config: dict[str, Any] = Field(default_factory=dict)

    class Config:
        from_attributes = True


class AdminOverviewOut(BaseModel):
    plans: list[SubscriptionPlanOut]
    tenants: list[TenantOut]
    features: list[FeatureFlagOut]
    users: list[AppUserOut] = Field(default_factory=list)


class FeatureFlagPatch(BaseModel):
    enabled: bool | None = None
    feature_name: str | None = None
    description: str | None = None
    config: dict[str, Any] | None = None


class SubscriptionPlanPatch(BaseModel):
    name: str | None = None
    description: str | None = None
    price_label: str | None = None
    price_cents: int | None = None
    currency: str | None = None
    duration_days: int | None = None
    seat_limit: int | None = None
    bom_limit: int | None = None
    enabled: bool | None = None


class AdminLoginRequest(BaseModel):
    username: str
    password: str


class AdminLoginOut(BaseModel):
    token: str
    user: AppUserOut


class AppUserPatch(BaseModel):
    display_name: str | None = None
    email: str | None = None
    phone: str | None = None
    role: str | None = None
    status: str | None = None
    password: str | None = None


class RegisterRequest(BaseModel):
    plan_id: str
    username: str
    password: str
    display_name: str | None = None
    email: str | None = None
    email_code: str
    payment_order_id: str


class EmailCodeRequest(BaseModel):
    email: str
    purpose: str = "register"


class EmailCodeOut(BaseModel):
    ok: bool = True
    message: str
    dev_code: str | None = None
    expires_in_seconds: int = 600


class PaymentOrderCreate(BaseModel):
    plan_id: str
    email: str


class PaymentOrderOut(BaseModel):
    id: str
    plan_id: str
    email: str
    amount_cents: int
    currency: str
    duration_days: int
    provider: str
    status: str
    checkout_url: str | None = None

    class Config:
        from_attributes = True
