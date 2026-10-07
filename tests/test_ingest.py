import math
import zipfile

import geopandas as gpd
import pytest
from pyproj import CRS
from shapely.geometry import Point, box

from app.exceptions import (
    FileTooLarge, InvalidArchive, InvalidGeoFile, MissingCRS, UnsupportedFileType,
)
from app.services import ingest
from tests.factories import KML_SAMPLE, make_shapefile_zip, make_zip, square_lonlat


def test_detect_file_type():
    assert ingest.detect_file_type("a.ZIP") == "shapefile"
    assert ingest.detect_file_type("a.kml") == "kml"
    with pytest.raises(UnsupportedFileType):
        ingest.detect_file_type("a.geojson")


def test_safe_filename():
    assert ingest.safe_filename("../../etc/passwd") == "passwd"
    assert ingest.safe_filename("my file (1).kml") == "my_file__1_.kml"


def test_save_upload_enforces_limit(tmp_path):
    import io
    with pytest.raises(FileTooLarge):
        ingest.save_upload(io.BytesIO(b"x" * 100), tmp_path / "f", max_bytes=10)


def test_zip_slip_is_rejected(tmp_path):
    z = make_zip(tmp_path, {"../evil.shp": b"x", "ok.shx": b"x", "ok.dbf": b"x"})
    with pytest.raises(InvalidArchive, match="unsafe"):
        ingest.extract_shapefiles(z, tmp_path / "out")
    assert not (tmp_path / "evil.shp").exists()


def test_absolute_path_entry_is_rejected(tmp_path):
    z = make_zip(tmp_path, {"/tmp/evil.shp": b"x"})
    with pytest.raises(InvalidArchive, match="unsafe"):
        ingest.extract_shapefiles(z, tmp_path / "out")


def test_missing_sidecar_files(tmp_path):
    z = make_zip(tmp_path, {"a.shp": b"x", "a.shx": b"x"})
    with pytest.raises(InvalidArchive, match=r"missing \.dbf"):
        ingest.extract_shapefiles(z, tmp_path / "out")


def test_zip_without_shp(tmp_path):
    z = make_zip(tmp_path, {"readme.txt": b"hello"})
    with pytest.raises(InvalidArchive, match="No .shp"):
        ingest.extract_shapefiles(z, tmp_path / "out")


def test_not_a_zip(tmp_path):
    p = tmp_path / "x.zip"
    p.write_bytes(b"definitely not a zip")
    with pytest.raises(InvalidArchive):
        ingest.extract_shapefiles(p, tmp_path / "out")


def test_decompression_bomb_guard(tmp_path, monkeypatch):
    monkeypatch.setattr(ingest.settings, "max_uncompressed_bytes", 1000)
    z = make_zip(tmp_path, {"a.shp": b"0" * 5000, "a.shx": b"x", "a.dbf": b"x"})
    with pytest.raises(InvalidArchive, match="too large"):
        ingest.extract_shapefiles(z, tmp_path / "out")


def test_read_shapefile_features_and_properties(tmp_path):
    gdf = gpd.GeoDataFrame(
        {"name": ["A", "B"], "count": [3, 4], "ratio": [0.5, float("nan")]},
        geometry=[square_lonlat(77.59, 12.97), square_lonlat(77.60, 12.97)],
        crs=4326,
    )
    z = make_shapefile_zip(gdf, tmp_path, "plots")
    shp = ingest.extract_shapefiles(z, tmp_path / "out")
    feats = ingest.read_features(shp, "shapefile")
    assert [f.index for f in feats] == [0, 1]
    assert feats[0].properties == {"name": "A", "count": 3, "ratio": 0.5}
    assert feats[1].properties["ratio"] is None  # NaN is not valid JSON
    assert feats[0].crs.to_epsg() == 4326 and feats[0].layer == "plots"


def test_shapefile_without_prj_needs_crs(tmp_path):
    gdf = gpd.GeoDataFrame({"a": [1]}, geometry=[Point(77.59, 12.97)], crs=4326)
    z = make_shapefile_zip(gdf, tmp_path, "pts", drop_prj=True)
    shp = ingest.extract_shapefiles(z, tmp_path / "out")
    with pytest.raises(MissingCRS):
        ingest.read_features(shp, "shapefile")
    feats = ingest.read_features(shp, "shapefile", assume_crs=CRS.from_epsg(4326))
    assert feats[0].crs.to_epsg() == 4326


def test_read_kml_all_layers_with_properties(tmp_path):
    p = tmp_path / "s.kml"
    p.write_text(KML_SAMPLE)
    feats = ingest.read_features([p], "kml")
    assert len(feats) == 4
    by_name = {f.properties.get("Name"): f for f in feats}
    assert set(by_name) == {"Plot A", "Road", "Well", "Mixed"}
    assert by_name["Plot A"].properties["description"] == "North field"
    assert by_name["Plot A"].properties["crop"] == "rice"
    assert by_name["Plot A"].layer == "Plots"
    assert all(f.crs.to_epsg() == 4326 for f in feats)
    assert "tessellate" not in by_name["Plot A"].properties  # KML driver noise removed


def test_garbage_kml(tmp_path):
    p = tmp_path / "bad.kml"
    p.write_text("this is not xml")
    with pytest.raises(InvalidGeoFile):
        ingest.read_features([p], "kml")


def test_kml_with_no_features(tmp_path):
    p = tmp_path / "empty.kml"
    p.write_text('<?xml version="1.0"?><kml xmlns="http://www.opengis.net/kml/2.2"><Document/></kml>')
    with pytest.raises(InvalidGeoFile):
        ingest.read_features([p], "kml")
