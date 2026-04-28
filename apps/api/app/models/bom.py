from datetime import datetime
from uuid import uuid4

from sqlalchemy import JSON, DateTime, Float, ForeignKey, Integer, String, Text
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

    # UI metadata (used by G6 for style overrides, set by Agent)
    style: Mapped[dict] = mapped_column(JSON, default=dict)
    # Link back to source: {"type": "excel_row", "row": 12} | {"type": "cad_node", "id": "..."}
    source_ref: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    confidence: Mapped[float] = mapped_column(Float, default=1.0)

    sort_order: Mapped[int] = mapped_column(Integer, default=0)

    bom: Mapped[BOM] = relationship(back_populates="nodes")
