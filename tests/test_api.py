import geopandas as gpd
import pytest
from shapely.geometry import LineString, Point

from app.config import settings
from tests.factories import KML_SAMPLE, make_shapefile_zip, make_zip, square_lonlat


def upload(client, name, data, **params):
    return client.post("/api/files/", files={"file": (name, data)}, params=params)


def test_health(client):
    assert client.get("/health").json() == {"status": "ok"}


def test_kml_end_to_end(client):
    r = upload(client, "survey.kml", KML_SAMPLE.encode())
    assert r.status_code == 201, r.text
    info = r.json()
    assert info["filename"] == "survey.kml"
    assert info["feature_count"] == 4
    assert info["crs"] == "EPSG:4326"
    assert info["status"] == "COMPLETED"

    assert client.get(f"/api/files/{info['id']}/").json()["feature_count"] == 4

    m = client.get(f"/api/files/{info['id']}/measurements/").json()
    assert m["total"] == 4
    by_name = {i["properties"]["Name"]: i for i in m["items"]}
    plot = by_name["Plot A"]
    assert plot["measurement_status"] == "MEASURED"
    assert plot["measurements"]["area_m2"] == pytest.approx(11_939, rel=0.01)
    assert plot["measurement_crs"] == "EPSG:32644" and plot["source_crs"] == "EPSG:4326"
    assert by_name["Road"]["measurements"]["length_m"] == pytest.approx(1545.5, rel=0.01)
    assert by_name["Well"]["measurement_status"] == "NOT_APPLICABLE"
    assert by_name["Mixed"]["measurement_status"] == "UNSUPPORTED"
    assert m["summary"]["by_status"] == {"MEASURED": 2, "NOT_APPLICABLE": 1, "UNSUPPORTED": 1}
    assert m["summary"]["total_area_m2"] == pytest.approx(plot["measurements"]["area_m2"])


def test_measurement_filters_and_pagination(client):
    fid = upload(client, "s.kml", KML_SAMPLE.encode()).json()["id"]
    page = client.get(f"/api/files/{fid}/measurements/", params={"limit": 2, "offset": 1}).json()
    assert page["total"] == 4 and [i["index"] for i in page["items"]] == [1, 2]
    polys = client.get(f"/api/files/{fid}/measurements/", params={"geometry_type": "polygon"}).json()
    assert polys["total"] == 1 and polys["items"][0]["geometry_type"] == "Polygon"
    unsup = client.get(f"/api/files/{fid}/measurements/", params={"measurement_status": "unsupported"}).json()
    assert unsup["total"] == 1
    assert client.get(f"/api/files/{fid}/measurements/", params={"limit": 0}).status_code == 422


def test_features_endpoint_returns_geometry_crs_and_properties(client):
    fid = upload(client, "s.kml", KML_SAMPLE.encode()).json()["id"]
    f = client.get(f"/api/files/{fid}/features/", params={"geometry_type": "Polygon"}).json()
    item = f["items"][0]
    assert item["geometry"]["type"] == "Polygon" and item["crs"] == "EPSG:4326"
    assert item["properties"]["crop"] == "rice"


def test_shapefile_wgs84(client, tmp_path):
    gdf = gpd.GeoDataFrame(
        {"name": ["A", "B"]}, geometry=[square_lonlat(77.59, 12.97, 200), square_lonlat(151.21, -33.87, 100)], crs=4326
    )
    z = make_shapefile_zip(gdf, tmp_path, "parcels")
    r = upload(client, "parcels.zip", z.read_bytes())
    assert r.status_code == 201, r.text
    fid = r.json()["id"]
    assert r.json()["crs"] == "EPSG:4326"
    items = client.get(f"/api/files/{fid}/measurements/").json()["items"]
    assert items[0]["measurements"]["area_m2"] == pytest.approx(40_000, rel=0.003)
    assert items[1]["measurements"]["area_m2"] == pytest.approx(10_000, rel=0.003)
    assert items[0]["measurement_crs"] == "EPSG:32643"
    assert items[1]["measurement_crs"] == "EPSG:32756"  # per-feature UTM zone, southern hemisphere


def test_shapefile_already_projected(client, tmp_path):
    gdf = gpd.GeoDataFrame(
        {"id": [1]}, geometry=[LineString([(500_000, 1_400_000), (500_300, 1_400_400)])], crs=32643
    )
    z = make_shapefile_zip(gdf, tmp_path, "roads")
    r = upload(client, "roads.zip", z.read_bytes())
    assert r.json()["crs"] == "EPSG:32643"
    item = client.get(f"/api/files/{r.json()['id']}/measurements/").json()["items"][0]
    assert item["measurements"]["length_m"] == pytest.approx(500.0)
    assert item["measurement_crs"] == "EPSG:32643"


def test_shapefile_points_only(client, tmp_path):
    gdf = gpd.GeoDataFrame({"n": [1, 2]}, geometry=[Point(77.59, 12.97), Point(77.6, 12.98)], crs=4326)
    z = make_shapefile_zip(gdf, tmp_path, "wells")
    r = upload(client, "wells.zip", z.read_bytes())
    s = client.get(f"/api/files/{r.json()['id']}/measurements/").json()["summary"]
    assert s["by_status"] == {"NOT_APPLICABLE": 2} and s["total_area_m2"] == 0


def test_missing_prj_fails_cleanly_then_assume_crs_works(client, tmp_path):
    gdf = gpd.GeoDataFrame({"a": [1]}, geometry=[square_lonlat(77.59, 12.97)], crs=4326)
    z = make_shapefile_zip(gdf, tmp_path, "noprj", drop_prj=True)
    r = upload(client, "noprj.zip", z.read_bytes())
    assert r.status_code == 422
    body = r.json()
    assert "no CRS" in body["detail"] and body["file"]["status"] == "FAILED"
    failed_id = body["file"]["id"]
    assert client.get(f"/api/files/{failed_id}/").json()["status"] == "FAILED"
    assert client.get(f"/api/files/{failed_id}/measurements/").status_code == 409

    ok = upload(client, "noprj.zip", z.read_bytes(), assume_crs="EPSG:4326")
    assert ok.status_code == 201 and ok.json()["crs"] == "EPSG:4326"


def test_invalid_assume_crs(client):
    r = upload(client, "s.kml", KML_SAMPLE.encode(), assume_crs="not-a-crs")
    assert r.status_code == 422 and "valid CRS" in r.json()["detail"]


def test_unsupported_extension(client):
    r = upload(client, "data.geojson", b"{}")
    assert r.status_code == 415


def test_empty_file(client):
    assert upload(client, "e.kml", b"").status_code == 400


def test_too_large(client, monkeypatch):
    monkeypatch.setattr(settings, "max_upload_bytes", 100)
    assert upload(client, "big.kml", b"x" * 500).status_code == 413


def test_corrupt_zip_is_failed_record_not_500(client):
    r = upload(client, "bad.zip", b"not really a zip")
    assert r.status_code == 422 and r.json()["file"]["status"] == "FAILED"


def test_zip_slip_via_api(client, tmp_path):
    z = make_zip(tmp_path, {"../../evil.shp": b"x", "a.shx": b"x", "a.dbf": b"x"})
    r = upload(client, "evil.zip", z.read_bytes())
    assert r.status_code == 422 and "unsafe" in r.json()["detail"]


def test_garbage_kml_is_failed(client):
    r = upload(client, "bad.kml", b"<kml>nope")
    assert r.status_code == 422 and r.json()["file"]["status"] == "FAILED"


def test_unknown_id_is_404(client):
    for path in ("", "measurements/", "features/"):
        assert client.get(f"/api/files/doesnotexist/{path}").status_code == 404
