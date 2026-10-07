"""Helpers that build small geospatial test files on the fly."""
import zipfile
from pathlib import Path

import geopandas as gpd
from pyproj import Transformer
from shapely.geometry import Polygon, box

from app.services.crs import utm_epsg


def square_lonlat(lon: float, lat: float, side_m: float = 100.0) -> Polygon:
    """A side_m x side_m metre square (built in UTM) returned as lon/lat coordinates."""
    epsg = utm_epsg(lon, lat)
    to_utm = Transformer.from_crs(4326, epsg, always_xy=True)
    to_ll = Transformer.from_crs(epsg, 4326, always_xy=True)
    x, y = to_utm.transform(lon, lat)
    half = side_m / 2
    corners = [(x - half, y - half), (x + half, y - half), (x + half, y + half), (x - half, y + half)]
    return Polygon([to_ll.transform(cx, cy) for cx, cy in corners])


def make_shapefile_zip(gdf: gpd.GeoDataFrame, directory: Path, name: str = "data", drop_prj: bool = False) -> Path:
    shp_dir = directory / f"{name}_shp"
    shp_dir.mkdir(parents=True, exist_ok=True)
    gdf.to_file(shp_dir / f"{name}.shp", driver="ESRI Shapefile")
    if drop_prj:
        (shp_dir / f"{name}.prj").unlink()
    zip_path = directory / f"{name}.zip"
    with zipfile.ZipFile(zip_path, "w") as zf:
        for f in shp_dir.iterdir():
            zf.write(f, f.name)
    return zip_path


def make_zip(directory: Path, entries: dict[str, bytes], name: str = "custom.zip") -> Path:
    path = directory / name
    with zipfile.ZipFile(path, "w") as zf:
        for entry, data in entries.items():
            zf.writestr(entry, data)
    return path


KML_SAMPLE = """<?xml version="1.0" encoding="UTF-8"?>
<kml xmlns="http://www.opengis.net/kml/2.2"><Document><name>Survey</name>
<Folder><name>Plots</name>
<Placemark><name>Plot A</name><description>North field</description>
<ExtendedData><Data name="crop"><value>rice</value></Data></ExtendedData>
<Polygon><outerBoundaryIs><LinearRing><coordinates>
78.8,14.4,120 78.801,14.4,120 78.801,14.401,121 78.8,14.401,120 78.8,14.4,120
</coordinates></LinearRing></outerBoundaryIs></Polygon></Placemark>
<Placemark><name>Road</name><LineString><coordinates>78.8,14.4,0 78.81,14.41,0</coordinates></LineString></Placemark>
</Folder>
<Placemark><name>Well</name><Point><coordinates>78.805,14.405,0</coordinates></Point></Placemark>
<Placemark><name>Mixed</name><MultiGeometry>
<Point><coordinates>78.8,14.4</coordinates></Point>
<LineString><coordinates>78.8,14.4 78.81,14.41</coordinates></LineString>
</MultiGeometry></Placemark>
</Document></kml>
"""

__all__ = ["square_lonlat", "make_shapefile_zip", "make_zip", "KML_SAMPLE", "box"]
