import uuid
from datetime import datetime, timezone

from sqlalchemy import JSON, DateTime, ForeignKey, Index, Integer, String, Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base

# JSONB on Postgres, plain JSON elsewhere (SQLite is used for tests / quick local runs).
JSONType = JSON().with_variant(JSONB(), "postgresql")

PROCESSING = "PROCESSING"
COMPLETED = "COMPLETED"
FAILED = "FAILED"


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _new_id() -> str:
    return uuid.uuid4().hex


class UploadedFile(Base):
    __tablename__ = "uploaded_files"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_new_id)
    filename: Mapped[str] = mapped_column(String(255))
    file_type: Mapped[str] = mapped_column(String(20))  # "shapefile" | "kml"
    status: Mapped[str] = mapped_column(String(20), index=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    crs: Mapped[str | None] = mapped_column(String(255), nullable=True)
    feature_count: Mapped[int] = mapped_column(Integer, default=0)
    summary: Mapped[dict | None] = mapped_column(JSONType, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class Feature(Base):
    __tablename__ = "features"
    __table_args__ = (Index("ix_features_file_order", "file_id", "feature_index"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    file_id: Mapped[str] = mapped_column(
        String(32), ForeignKey("uploaded_files.id", ondelete="CASCADE"), index=True
    )
    feature_index: Mapped[int] = mapped_column(Integer)
    layer: Mapped[str | None] = mapped_column(String(255), nullable=True)
    geometry_type: Mapped[str] = mapped_column(String(50))
    geometry: Mapped[dict | None] = mapped_column(JSONType, nullable=True)  # GeoJSON, source CRS
    crs: Mapped[str | None] = mapped_column(String(255), nullable=True)  # source CRS
    properties: Mapped[dict] = mapped_column(JSONType, default=dict)
    measurement_status: Mapped[str] = mapped_column(String(20))
    measurements: Mapped[dict] = mapped_column(JSONType, default=dict)
    measurement_crs: Mapped[str | None] = mapped_column(String(255), nullable=True)
    warnings: Mapped[list] = mapped_column(JSONType, default=list)
    reason: Mapped[str | None] = mapped_column(Text, nullable=True)
