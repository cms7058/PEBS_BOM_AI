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
