"""Area / length calculation with graceful handling of unsupported geometries."""
import logging
from dataclasses import dataclass, field

import numpy as np
import shapely
from pyproj import CRS, Geod
from shapely.geometry import MultiPolygon, Polygon
from shapely.geometry.base import BaseGeometry
from shapely.validation import explain_validity

from app.services.crs import crs_label, reproject, select_measurement_crs

logger = logging.getLogger(__name__)

MEASURED = "MEASURED"  # area / length computed
NOT_APPLICABLE = "NOT_APPLICABLE"  # points: nothing to measure
UNSUPPORTED = "UNSUPPORTED"  # e.g. GeometryCollection, empty/null geometry
FAILED = "FAILED"  # supported type, but this feature could not be measured

POLYGONAL = {"Polygon", "MultiPolygon"}
LINEAR = {"LineString", "MultiLineString"}
POINTLIKE = {"Point", "MultiPoint"}

_COORD_TOLERANCE = 1e-6


@dataclass
class MeasureResult:
    status: str
    geometry_type: str
    measurements: dict[str, float] = field(default_factory=dict)
    measurement_crs: str | None = None
    warnings: list[str] = field(default_factory=list)
    reason: str | None = None


def measure_geometry(geom: BaseGeometry | None, src_crs: CRS) -> MeasureResult:
    """Measure one geometry. Never raises: problems are reported in the result."""
    if geom is None:
        return MeasureResult(UNSUPPORTED, "Unknown", reason="Null geometry")
    gtype = geom.geom_type
    if geom.is_empty:
        return MeasureResult(UNSUPPORTED, gtype, reason="Empty geometry")
    if gtype in POINTLIKE:
        return MeasureResult(NOT_APPLICABLE, gtype, reason="Points have no area or length")
    if gtype not in POLYGONAL and gtype not in LINEAR:
        return MeasureResult(UNSUPPORTED, gtype, reason=f"Measurement is not supported for {gtype}")
    try:
        return _measure(geom, gtype, src_crs)
    except Exception as exc:
        logger.warning("Measurement failed for a %s: %s", gtype, exc)
        return MeasureResult(FAILED, gtype, reason=f"Measurement failed: {exc}")


def _measure(geom: BaseGeometry, gtype: str, src_crs: CRS) -> MeasureResult:
    warnings: list[str] = []
    geom = shapely.force_2d(geom)  # altitude must not influence planar measurements

    if src_crs.is_geographic:
        _check_lonlat_range(geom)

    if gtype in POLYGONAL and not geom.is_valid:
        why = explain_validity(geom)
        geom = _repair_polygonal(geom)
        warnings.append(f"Invalid polygon repaired with make_valid ({why})")
        if geom.is_empty:
            warnings.append("Geometry collapsed to zero area after repair")
            return MeasureResult(
                MEASURED, gtype, {"area_m2": 0.0, "area_ha": 0.0, "perimeter_m": 0.0}, None, warnings
            )

    target = select_measurement_crs(geom, src_crs)
    projected = reproject(geom, src_crs, target.crs)
    if not np.isfinite(shapely.get_coordinates(projected)).all():
        raise ValueError("coordinates could not be transformed to the measurement CRS")

    f = target.unit_factor
    if gtype in POLYGONAL:
        area_m2 = projected.area * f * f
        measurements = {
            "area_m2": _r(area_m2),
            "area_ha": _r(area_m2 / 10_000),
            "perimeter_m": _r(projected.length * f),
        }
    else:
        length_m = projected.length * f
        measurements = {"length_m": _r(length_m), "length_km": _r(length_m / 1000)}

    return MeasureResult(MEASURED, gtype, measurements, crs_label(target.crs), warnings)


def _r(value: float) -> float:
    return round(float(value), 4)


def _check_lonlat_range(geom: BaseGeometry) -> None:
    minx, miny, maxx, maxy = geom.bounds
    t = _COORD_TOLERANCE
    if minx < -180 - t or maxx > 180 + t or miny < -90 - t or maxy > 90 + t:
        raise ValueError("coordinates are outside the valid longitude/latitude range")


def _iter_polygons(geom: BaseGeometry):
    if geom.geom_type == "Polygon":
        yield geom
    elif hasattr(geom, "geoms"):
        for part in geom.geoms:
            yield from _iter_polygons(part)


def _repair_polygonal(geom: BaseGeometry) -> BaseGeometry:
    """make_valid, keeping only the polygonal parts (it may emit lines/points too)."""
    polygons = [p for p in _iter_polygons(shapely.make_valid(geom)) if not p.is_empty]
    if not polygons:
        return Polygon()
    return polygons[0] if len(polygons) == 1 else MultiPolygon(polygons)


# --- Independent check, used by the tests and handy for debugging ------------
_GEOD = Geod(ellps="WGS84")


def geodesic_measure(geom_lonlat: BaseGeometry) -> dict[str, float]:
    """Ellipsoidal (geodesic) area/length of a lon/lat geometry - no projection involved."""
    if geom_lonlat.geom_type in POLYGONAL:
        area, perimeter = _GEOD.geometry_area_perimeter(shapely.force_2d(geom_lonlat))
        return {"area_m2": abs(area), "perimeter_m": perimeter}
    if geom_lonlat.geom_type in LINEAR:
        return {"length_m": _GEOD.geometry_length(shapely.force_2d(geom_lonlat))}
    return {}


def summarize(results: list[MeasureResult]) -> dict:
    by_type: dict[str, int] = {}
    by_status: dict[str, int] = {}
    area_m2 = length_m = 0.0
    for r in results:
        by_type[r.geometry_type] = by_type.get(r.geometry_type, 0) + 1
        by_status[r.status] = by_status.get(r.status, 0) + 1
        area_m2 += r.measurements.get("area_m2", 0.0)
        length_m += r.measurements.get("length_m", 0.0)
    return {
        "feature_count": len(results),
        "by_geometry_type": by_type,
        "by_status": by_status,
        "total_area_m2": _r(area_m2),
        "total_area_ha": _r(area_m2 / 10_000),
        "total_length_m": _r(length_m),
        "total_length_km": _r(length_m / 1000),
    }
