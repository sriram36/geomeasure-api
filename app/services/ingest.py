"""Upload validation, safe archive extraction and feature reading."""
import math
import re
import shutil
import zipfile
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Any, BinaryIO

import numpy as np
import pandas as pd
import pyogrio
from pyproj import CRS
from shapely.geometry.base import BaseGeometry

from app.config import settings
from app.exceptions import (
    EmptyFile,
    FileTooLarge,
    InvalidArchive,
    InvalidGeoFile,
    MissingCRS,
    UnsupportedFileType,
)

EXTENSIONS = {".zip": "shapefile", ".kml": "kml"}
_CHUNK = 1024 * 1024
# Columns the GDAL KML driver always emits; dropped when they hold only defaults.
_KML_NOISE = {
    "tessellate", "extrude", "visibility", "drawOrder", "icon",
    "altitudeMode", "timestamp", "begin", "end", "id",
}


@dataclass
class ParsedFeature:
    index: int
    layer: str | None
    geometry: BaseGeometry | None
    properties: dict[str, Any]
    crs: CRS


def detect_file_type(filename: str) -> str:
    ext = Path(filename).suffix.lower()
    if ext not in EXTENSIONS:
        raise UnsupportedFileType(
            f"Unsupported file type '{ext or filename}'. "
            "Upload a .zip containing a Shapefile, or a .kml file."
        )
    return EXTENSIONS[ext]


def safe_filename(filename: str) -> str:
    name = Path(filename.replace("\\", "/")).name
    name = re.sub(r"[^A-Za-z0-9._-]", "_", name)
    return (name or "upload")[:200]


def save_upload(src: BinaryIO, dest: Path, max_bytes: int) -> int:
    """Stream the upload to disk, enforcing the size limit while reading."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    size = 0
    with dest.open("wb") as out:
        while chunk := src.read(_CHUNK):
            size += len(chunk)
            if size > max_bytes:
                raise FileTooLarge(f"File exceeds the {max_bytes // (1024 * 1024)} MB upload limit.")
            out.write(chunk)
    if size == 0:
        raise EmptyFile("Uploaded file is empty.")
    return size


# --- Shapefile (zip) ----------------------------------------------------------
def _is_junk(name: str) -> bool:
    parts = name.replace("\\", "/").split("/")
    return "__MACOSX" in parts or parts[-1] == ".DS_Store" or parts[-1].startswith("._")


def _has_sibling(shp: Path, ext: str) -> bool:
    return any(p.suffix.lower() == ext and p.stem == shp.stem for p in shp.parent.iterdir())


def extract_shapefiles(zip_path: Path, dest_dir: Path) -> list[Path]:
    """Safely extract a zip and return the complete .shp files inside it.

    Guards: zip-slip (path traversal), too many entries, and decompression bombs
    (limit enforced on the bytes actually written, not on the zip's header).
    """
    if not zipfile.is_zipfile(zip_path):
        raise InvalidArchive("The file is not a valid zip archive.")
    dest_dir.mkdir(parents=True, exist_ok=True)
    root = dest_dir.resolve()
    written = 0
    try:
        with zipfile.ZipFile(zip_path) as zf:
            infos = [i for i in zf.infolist() if not i.is_dir() and not _is_junk(i.filename)]
            if len(infos) > settings.max_zip_entries:
                raise InvalidArchive(f"Archive has too many files (limit {settings.max_zip_entries}).")
            for info in infos:
                target = (root / info.filename).resolve()
                if not target.is_relative_to(root):
                    raise InvalidArchive("Archive contains unsafe file paths.")
                target.parent.mkdir(parents=True, exist_ok=True)
                with zf.open(info) as src, target.open("wb") as out:
                    while chunk := src.read(_CHUNK):
                        written += len(chunk)
                        if written > settings.max_uncompressed_bytes:
                            raise InvalidArchive("Archive is too large once extracted.")
                        out.write(chunk)
    except (zipfile.BadZipFile, NotImplementedError, RuntimeError) as exc:
        raise InvalidArchive(f"Corrupt or unsupported zip archive: {exc}") from exc

    shp_files = sorted(p for p in root.rglob("*") if p.is_file() and p.suffix.lower() == ".shp")
    if not shp_files:
        raise InvalidArchive("No .shp file found in the archive.")

    complete, problems = [], []
    for shp in shp_files:
        missing = [e for e in (".shx", ".dbf") if not _has_sibling(shp, e)]
        if missing:
            problems.append(f"{shp.name} is missing {', '.join(missing)}")
        else:
            complete.append(shp)
    if not complete:
        raise InvalidArchive(
            "Incomplete shapefile: " + "; ".join(problems) + ". A .shp, .shx and .dbf are required."
        )
    return complete


# --- Reading ------------------------------------------------------------------
def _clean_value(value: Any) -> Any:
    """Make attribute values JSON-serialisable (numpy/pandas scalars, NaN, dates)."""
    if value is None:
        return None
    if not isinstance(value, (list, tuple, dict, np.ndarray)):
        try:
            if pd.isna(value):
                return None
        except (TypeError, ValueError):
            pass
    if isinstance(value, np.bool_):
        return bool(value)
    if isinstance(value, np.integer):
        return int(value)
    if isinstance(value, (float, np.floating)):
        f = float(value)
        return f if math.isfinite(f) else None
    if isinstance(value, (datetime, date, pd.Timestamp)):
        return value.isoformat()
    if isinstance(value, bytes):
        return value.decode("utf-8", "replace")
    if isinstance(value, (str, int, bool)):
        return value
    return str(value)


def _clean_properties(record: dict[str, Any], file_type: str) -> dict[str, Any]:
    props = {str(k): _clean_value(v) for k, v in record.items()}
    if file_type == "kml":
        props = {
            k: v for k, v in props.items()
            if not (k in _KML_NOISE and v in (None, -1, 0, ""))
        }
    return props


def read_features(
    paths: list[Path], file_type: str, assume_crs: CRS | None = None
) -> list[ParsedFeature]:
    """Read every feature of every layer of the given files."""
    features: list[ParsedFeature] = []
    for path in paths:
        try:
            layers = [str(row[0]) for row in pyogrio.list_layers(path)]
        except Exception as exc:
            raise InvalidGeoFile(f"Could not read '{path.name}' as a geospatial file: {exc}") from exc

        for layer in layers:
            try:
                gdf = pyogrio.read_dataframe(path, layer=layer)
            except Exception as exc:
                raise InvalidGeoFile(f"Could not read layer '{layer}' of '{path.name}': {exc}") from exc
            if len(gdf) == 0:
                continue

            crs = gdf.crs
            if crs is None:
                if file_type == "kml":
                    crs = CRS.from_epsg(4326)  # KML is always WGS84 lon/lat
                elif assume_crs is not None:
                    crs = assume_crs
                else:
                    raise MissingCRS(
                        f"'{path.name}' has no CRS (.prj missing or unreadable). Add the .prj file, "
                        "or re-upload with ?assume_crs=EPSG:<code>."
                    )

            geom_col = gdf.geometry.name
            attrs = gdf.drop(columns=[geom_col])
            records = attrs.to_dict("records") if len(attrs.columns) else [{} for _ in range(len(gdf))]
            layer_name = path.stem if file_type == "shapefile" else layer

            for geom, record in zip(gdf.geometry, records):
                features.append(
                    ParsedFeature(
                        index=len(features),
                        layer=layer_name,
                        geometry=geom if isinstance(geom, BaseGeometry) else None,
                        properties=_clean_properties(record, file_type),
                        crs=crs,
                    )
                )

    if not features:
        raise InvalidGeoFile("No features found in the file.")
    return features


def cleanup(path: Path) -> None:
    shutil.rmtree(path, ignore_errors=True)
