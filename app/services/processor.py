"""Orchestrates: extract -> read -> measure -> persist."""
import logging
from datetime import datetime, timezone
from pathlib import Path

from pyproj import CRS
from shapely.geometry import mapping
from shapely.geometry.base import BaseGeometry
from sqlalchemy.orm import Session

from app.models import COMPLETED, Feature, UploadedFile
from app.services import ingest
from app.services.crs import crs_label
from app.services.measure import measure_geometry, summarize

logger = logging.getLogger(__name__)


def _geojson(geom: BaseGeometry | None) -> dict | None:
    if geom is None or geom.is_empty:
        return None
    return mapping(geom)


def process_upload(
    db: Session,
    record: UploadedFile,
    stored_path: Path,
    work_dir: Path,
    assume_crs: CRS | None = None,
) -> UploadedFile:
    """Process a stored upload and persist its features. Raises ProcessingError on bad input."""
    try:
        if record.file_type == "shapefile":
            paths = ingest.extract_shapefiles(stored_path, work_dir)
        else:
            paths = [stored_path]

        parsed = ingest.read_features(paths, record.file_type, assume_crs)

        rows, results = [], []
        for pf in parsed:
            result = measure_geometry(pf.geometry, pf.crs)
            results.append(result)
            rows.append(
                Feature(
                    file_id=record.id,
                    feature_index=pf.index,
                    layer=pf.layer,
                    geometry_type=result.geometry_type,
                    geometry=_geojson(pf.geometry),
                    crs=crs_label(pf.crs),
                    properties=pf.properties,
                    measurement_status=result.status,
                    measurements=result.measurements,
                    measurement_crs=result.measurement_crs,
                    warnings=result.warnings,
                    reason=result.reason,
                )
            )

        labels = {crs_label(pf.crs) for pf in parsed}
        record.crs = labels.pop() if len(labels) == 1 else "MIXED"
        record.feature_count = len(rows)
        record.summary = summarize(results)
        record.status = COMPLETED
        record.completed_at = datetime.now(timezone.utc)
        db.add_all(rows)
        db.commit()
        return record
    finally:
        ingest.cleanup(work_dir)  # extracted files are scratch; the original upload is kept
