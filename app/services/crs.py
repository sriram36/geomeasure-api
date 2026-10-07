"""CRS helpers: choosing a projected CRS suitable for measuring, and reprojecting.

Strategy
--------
* Geographic CRS (e.g. EPSG:4326)  -> reproject each feature to the UTM zone of its
  own centroid (UPS near the poles). Degrees are never used for area/length.
* Web Mercator (EPSG:3857)         -> same as geographic. Mercator hugely inflates area
  away from the equator, so it is not a measurement CRS.
* Any other projected CRS          -> measured natively; areas/lengths are converted to
  metres using the CRS's own linear unit (handles feet etc.).
"""
import threading
from dataclasses import dataclass

from pyproj import CRS, Transformer
from shapely.geometry import Point
from shapely.geometry.base import BaseGeometry
from shapely.ops import transform as shapely_transform

WEB_MERCATOR_EPSG = {3857, 900913, 3785}
UPS_NORTH, UPS_SOUTH = 32661, 32761


@dataclass(frozen=True)
class MeasurementCRS:
    crs: CRS
    method: str  # "utm" | "ups" | "native"
    unit_factor: float  # metres per CRS linear unit


def crs_label(crs: CRS | None) -> str | None:
    """Human-friendly CRS id, e.g. 'EPSG:4326' (falls back to the CRS name)."""
    if crs is None:
        return None
    epsg = crs.to_epsg()
    if epsg:
        return f"EPSG:{epsg}"
    return crs.name or "UNKNOWN"


def is_web_mercator(crs: CRS) -> bool:
    if crs.to_epsg() in WEB_MERCATOR_EPSG:
        return True
    name = (crs.name or "").lower().replace("_", " ")
    return "web mercator" in name or "pseudo-mercator" in name


def utm_epsg(lon: float, lat: float) -> int:
    """EPSG code of the WGS84 UTM zone (or UPS polar zone) containing lon/lat."""
    if lat > 84:
        return UPS_NORTH
    if lat < -80:
        return UPS_SOUTH
    lon = ((lon + 180) % 360) - 180
    zone = min(max(int((lon + 180) // 6) + 1, 1), 60)
    return (32600 if lat >= 0 else 32700) + zone


def linear_unit_factor(crs: CRS) -> float:
    """Metres per linear unit of a projected CRS (1.0 if it cannot be determined)."""
    try:
        factor = crs.axis_info[0].unit_conversion_factor
        if factor and factor > 0:
            return float(factor)
    except Exception:  # pragma: no cover - defensive
        pass
    return 1.0


_local = threading.local()


def _transformer(src: CRS, dst: CRS) -> Transformer:
    # Transformers are cached per thread: cheap reuse without sharing across threads.
    cache = getattr(_local, "cache", None)
    if cache is None or len(cache) > 128:
        cache = _local.cache = {}
    key = (src, dst)
    if key not in cache:
        cache[key] = Transformer.from_crs(src, dst, always_xy=True)
    return cache[key]


def reproject(geom: BaseGeometry, src: CRS, dst: CRS) -> BaseGeometry:
    if src == dst:
        return geom
    return shapely_transform(_transformer(src, dst).transform, geom)


def representative_lonlat(geom: BaseGeometry, src: CRS) -> tuple[float, float]:
    """Centroid of the geometry expressed as lon/lat (used to pick the UTM zone)."""
    c = geom.centroid
    if src.is_geographic:
        return c.x, c.y
    lon, lat = _transformer(src, CRS.from_epsg(4326)).transform(c.x, c.y)
    return lon, lat


def select_measurement_crs(geom: BaseGeometry, src: CRS) -> MeasurementCRS:
    if src.is_geographic or is_web_mercator(src):
        lon, lat = representative_lonlat(geom, src)
        epsg = utm_epsg(lon, lat)
        method = "ups" if epsg in (UPS_NORTH, UPS_SOUTH) else "utm"
        return MeasurementCRS(CRS.from_epsg(epsg), method, 1.0)
    return MeasurementCRS(src, "native", linear_unit_factor(src))
