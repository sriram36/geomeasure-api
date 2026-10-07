import logging
import shutil
import uuid
from pathlib import Path

from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile
from fastapi.encoders import jsonable_encoder
from fastapi.responses import JSONResponse
from pyproj import CRS
from pyproj.exceptions import CRSError
from sqlalchemy import func, select
from sqlalchemy.orm import Session, defer

from app.config import settings
from app.db import get_db
from app.exceptions import AppError, InvalidCRS, ProcessingError, UnsupportedFileType
from app.models import COMPLETED, FAILED, PROCESSING, Feature, UploadedFile
from app.schemas import (
    ErrorResponse,
    FeatureItem,
    FeaturesResponse,
    FileInfo,
    MeasurementItem,
    MeasurementsResponse,
)
from app.services import ingest
from app.services.processor import process_upload

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/files", tags=["files"])


def _parse_assume_crs(value: str | None) -> CRS | None:
    if not value:
        return None
    try:
        return CRS.from_user_input(value)
    except CRSError as exc:
        raise InvalidCRS(f"'{value}' is not a valid CRS (try e.g. EPSG:4326).") from exc


def _get_file_or_404(db: Session, file_id: str) -> UploadedFile:
    record = db.get(UploadedFile, file_id)
    if record is None:
        raise HTTPException(status_code=404, detail="File not found.")
    return record


def _require_completed(record: UploadedFile) -> None:
    if record.status != COMPLETED:
        detail = f"File status is {record.status}; results are only available for COMPLETED files."
        if record.error:
            detail += f" Error: {record.error}"
        raise HTTPException(status_code=409, detail=detail)


def _mark_failed(db: Session, file_id: str, message: str) -> UploadedFile:
    db.rollback()
    record = db.get(UploadedFile, file_id)
    record.status = FAILED
    record.error = message
    db.commit()
    return record


@router.post(
    "/",
    response_model=FileInfo,
    status_code=201,
    summary="Upload and process a Shapefile (.zip) or KML",
    responses={
        400: {"model": ErrorResponse, "description": "Empty file"},
        413: {"model": ErrorResponse, "description": "File too large"},
        415: {"model": ErrorResponse, "description": "Unsupported file type"},
        422: {"model": ErrorResponse, "description": "File could not be processed (record is kept as FAILED)"},
    },
)
def upload_file(
    file: UploadFile = File(..., description="A .zip containing a Shapefile, or a .kml file"),
    assume_crs: str | None = Query(
        None,
        description="CRS to assume for a shapefile that has no .prj (e.g. EPSG:4326). "
        "Ignored when the file declares its own CRS.",
    ),
    db: Session = Depends(get_db),
):
    filename = Path((file.filename or "").replace("\\", "/")).name
    if not filename:
        raise UnsupportedFileType("Uploaded file has no filename.")
    file_type = ingest.detect_file_type(filename)
    crs_hint = _parse_assume_crs(assume_crs)

    file_id = uuid.uuid4().hex
    record_dir = settings.upload_dir / file_id
    stored_path = record_dir / ingest.safe_filename(filename)
    try:
        ingest.save_upload(file.file, stored_path, settings.max_upload_bytes)
    except AppError:
        shutil.rmtree(record_dir, ignore_errors=True)
        raise

    record = UploadedFile(id=file_id, filename=filename[:255], file_type=file_type, status=PROCESSING)
    db.add(record)
    db.commit()

    try:
        return process_upload(db, record, stored_path, record_dir / "extracted", crs_hint)
    except ProcessingError as exc:
        failed = _mark_failed(db, file_id, exc.message)
        body = {"detail": exc.message, "file": FileInfo.model_validate(failed)}
        return JSONResponse(status_code=exc.status_code, content=jsonable_encoder(body))
    except Exception:
        logger.exception("Unexpected error while processing file %s", file_id)
        failed = _mark_failed(db, file_id, "Unexpected error while processing the file.")
        body = {"detail": failed.error, "file": FileInfo.model_validate(failed)}
        return JSONResponse(status_code=500, content=jsonable_encoder(body))


@router.get("/{file_id}/", response_model=FileInfo, summary="File information")
def get_file(file_id: str, db: Session = Depends(get_db)):
    return _get_file_or_404(db, file_id)


def _page(db: Session, file_id: str, limit: int, offset: int, geometry_type: str | None,
          measurement_status: str | None, defer_geometry: bool):
    conditions = [Feature.file_id == file_id]
    if geometry_type:
        conditions.append(func.lower(Feature.geometry_type) == geometry_type.lower())
    if measurement_status:
        conditions.append(Feature.measurement_status == measurement_status.upper())
    total = db.scalar(select(func.count(Feature.id)).where(*conditions)) or 0
    query = select(Feature).where(*conditions).order_by(Feature.feature_index).limit(limit).offset(offset)
    if defer_geometry:
        query = query.options(defer(Feature.geometry))
    return total, db.scalars(query).all()


@router.get(
    "/{file_id}/measurements/",
    response_model=MeasurementsResponse,
    summary="Per-feature measurements (area for polygons, length for lines)",
)
def get_measurements(
    file_id: str,
    limit: int = Query(100, ge=1, le=1000),
    offset: int = Query(0, ge=0),
    geometry_type: str | None = Query(None, description="Filter, e.g. Polygon, LineString, Point"),
    measurement_status: str | None = Query(
        None, description="Filter: MEASURED, NOT_APPLICABLE, UNSUPPORTED or FAILED"
    ),
    db: Session = Depends(get_db),
):
    record = _get_file_or_404(db, file_id)
    _require_completed(record)
    total, rows = _page(db, file_id, limit, offset, geometry_type, measurement_status, defer_geometry=True)
    items = [
        MeasurementItem(
            index=f.feature_index,
            layer=f.layer,
            geometry_type=f.geometry_type,
            measurement_status=f.measurement_status,
            measurements=f.measurements or {},
            source_crs=f.crs,
            measurement_crs=f.measurement_crs,
            properties=f.properties or {},
            warnings=f.warnings or [],
            reason=f.reason,
        )
        for f in rows
    ]
    return MeasurementsResponse(
        file_id=file_id, total=total, limit=limit, offset=offset, summary=record.summary or {}, items=items
    )


@router.get(
    "/{file_id}/features/",
    response_model=FeaturesResponse,
    summary="Extracted features: id, geometry type, geometry (GeoJSON), CRS, properties",
)
def get_features(
    file_id: str,
    limit: int = Query(100, ge=1, le=1000),
    offset: int = Query(0, ge=0),
    geometry_type: str | None = Query(None),
    db: Session = Depends(get_db),
):
    record = _get_file_or_404(db, file_id)
    _require_completed(record)
    total, rows = _page(db, file_id, limit, offset, geometry_type, None, defer_geometry=False)
    items = [
        FeatureItem(
            index=f.feature_index,
            layer=f.layer,
            geometry_type=f.geometry_type,
            geometry=f.geometry,
            crs=f.crs,
            properties=f.properties or {},
        )
        for f in rows
    ]
    return FeaturesResponse(file_id=file_id, total=total, limit=limit, offset=offset, items=items)
