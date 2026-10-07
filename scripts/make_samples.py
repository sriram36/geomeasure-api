"""Regenerate the sample files in ./samples (run from the repo root: python scripts/make_samples.py)."""
import shutil
import sys
import zipfile
from pathlib import Path

import geopandas as gpd
from shapely.geometry import LineString, Point, Polygon

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from tests.factories import KML_SAMPLE, square_lonlat  # noqa: E402

OUT = ROOT / "samples"
OUT.mkdir(exist_ok=True)


def zip_shapefile(gdf: gpd.GeoDataFrame, name: str) -> None:
    tmp = OUT / f"_{name}"
    tmp.mkdir(exist_ok=True)
    gdf.to_file(tmp / f"{name}.shp", driver="ESRI Shapefile")
    with zipfile.ZipFile(OUT / f"{name}.zip", "w") as zf:
        for f in sorted(tmp.iterdir()):
            zf.write(f, f.name)
    shutil.rmtree(tmp)


# KML with a polygon, a line, a point and an (unsupported) mixed MultiGeometry
(OUT / "survey.kml").write_text(KML_SAMPLE)

# Shapefile in EPSG:4326 - two plots in different UTM zones (Kadapa 44N, Sydney 56S)
zip_shapefile(
    gpd.GeoDataFrame(
        {"name": ["Kadapa plot", "Sydney plot"], "owner": ["A", "B"]},
        geometry=[square_lonlat(78.82, 14.47, 250), square_lonlat(151.21, -33.87, 100)],
        crs=4326,
    ),
    "parcels_wgs84",
)

# Shapefile already projected (UTM 43N) - lines
zip_shapefile(
    gpd.GeoDataFrame(
        {"road": ["NH-44", "Service road"]},
        geometry=[
            LineString([(500_000, 1_436_000), (500_800, 1_436_600)]),
            LineString([(500_100, 1_436_100), (500_100, 1_436_400), (500_300, 1_436_400)]),
        ],
        crs=32643,
    ),
    "roads_utm43n",
)
print("samples written to", OUT)
