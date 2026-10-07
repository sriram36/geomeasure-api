from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict


class FileInfo(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    filename: str
    file_type: str
    feature_count: int
    crs: str | None
    status: str
    error: str | None = None
    created_at: datetime
    completed_at: datetime | None = None


class ErrorResponse(BaseModel):
    detail: str
    file: FileInfo | None = None


class MeasurementItem(BaseModel):
    index: int
    layer: str | None
    geometry_type: str
    measurement_status: str  # MEASURED | NOT_APPLICABLE | UNSUPPORTED | FAILED
    measurements: dict[str, float]
    source_crs: str | None
    measurement_crs: str | None
    properties: dict[str, Any]
    warnings: list[str]
    reason: str | None


class MeasurementsResponse(BaseModel):
    file_id: str
    total: int
    limit: int
    offset: int
    summary: dict[str, Any]
    items: list[MeasurementItem]


class FeatureItem(BaseModel):
    index: int
    layer: str | None
    geometry_type: str
    geometry: dict[str, Any] | None
    crs: str | None
    properties: dict[str, Any]


class FeaturesResponse(BaseModel):
    file_id: str
    total: int
    limit: int
    offset: int
    items: list[FeatureItem]
