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
