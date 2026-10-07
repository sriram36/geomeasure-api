import pytest
from pyproj import CRS, Transformer
from shapely.geometry import (
    GeometryCollection, LineString, MultiPolygon, Point, Polygon, box,
)

from app.services.measure import (
    FAILED, MEASURED, NOT_APPLICABLE, UNSUPPORTED, geodesic_measure, measure_geometry, summarize,
)
from tests.factories import square_lonlat

WGS84 = CRS.from_epsg(4326)

LOCATIONS = [
    ("bengaluru", 77.59, 12.97),
    ("kadapa", 78.82, 14.47),
    ("sydney", 151.21, -33.87),
    ("london", -0.12, 51.5),
    ("anchorage", -149.9, 61.2),
    ("quito", -78.5, -0.2),
]


@pytest.mark.parametrize("name,lon,lat", LOCATIONS)
def test_polygon_area_matches_geodesic(name, lon, lat):
    poly = square_lonlat(lon, lat, side_m=1000)
    res = measure_geometry(poly, WGS84)
    assert res.status == MEASURED
    geo = geodesic_measure(poly)
    assert res.measurements["area_m2"] == pytest.approx(geo["area_m2"], rel=0.003)
    assert res.measurements["area_m2"] == pytest.approx(1_000_000, rel=0.003)
    assert res.measurements["area_ha"] == pytest.approx(res.measurements["area_m2"] / 10_000, abs=1e-3)
    assert res.measurement_crs.startswith("EPSG:326") or res.measurement_crs.startswith("EPSG:327")


def test_area_is_not_computed_in_degrees():
    """A 1-degree box has area 1.0 'degrees squared' - the real answer is ~12,000 km2."""
    res = measure_geometry(box(77, 12, 78, 13), WGS84)
    assert res.measurements["area_m2"] > 1e10


def test_large_polygon_close_to_geodesic():
    poly = box(77.0, 12.0, 77.5, 12.5)
    res = measure_geometry(poly, WGS84)
    assert res.measurements["area_m2"] == pytest.approx(geodesic_measure(poly)["area_m2"], rel=0.003)


def test_polygon_with_hole_subtracts_hole():
    outer = square_lonlat(77.59, 12.97, 1000)
    hole = square_lonlat(77.59, 12.97, 500)
    donut = Polygon(outer.exterior.coords, [hole.exterior.coords])
    res = measure_geometry(donut, WGS84)
    assert res.measurements["area_m2"] == pytest.approx(750_000, rel=0.003)


def test_multipolygon_sums_parts():
    a, b = square_lonlat(77.59, 12.97, 100), square_lonlat(77.60, 12.97, 200)
    res = measure_geometry(MultiPolygon([a, b]), WGS84)
    assert res.geometry_type == "MultiPolygon"
    assert res.measurements["area_m2"] == pytest.approx(10_000 + 40_000, rel=0.003)


def test_linestring_length_matches_geodesic():
    line = LineString([(78.80, 14.40), (78.81, 14.41), (78.83, 14.42)])
    res = measure_geometry(line, WGS84)
    assert res.status == MEASURED
    assert res.measurements["length_m"] == pytest.approx(geodesic_measure(line)["length_m"], rel=0.003)
    assert res.measurements["length_km"] == pytest.approx(res.measurements["length_m"] / 1000, abs=1e-3)


def test_web_mercator_source_is_reprojected_not_measured_directly():
    ll = square_lonlat(77.59, 12.97, 1000)
    to_merc = Transformer.from_crs(4326, 3857, always_xy=True)
    from shapely.ops import transform
    merc = transform(to_merc.transform, ll)
    naive = merc.area  # inflated by 1/cos^2(lat)
    assert naive > 1_040_000
    res = measure_geometry(merc, CRS.from_epsg(3857))
    assert res.measurements["area_m2"] == pytest.approx(1_000_000, rel=0.003)


def test_projected_crs_in_us_feet_is_converted_to_metres():
    sq = box(1_000_000, 200_000, 1_000_100, 200_100)  # 100 ft x 100 ft
    res = measure_geometry(sq, CRS.from_epsg(2263))
    assert res.measurement_crs == "EPSG:2263"
    assert res.measurements["area_m2"] == pytest.approx(10_000 * 0.3048006096**2, rel=1e-6)
    assert res.measurements["perimeter_m"] == pytest.approx(400 * 0.3048006096, rel=1e-6)


def test_projected_metric_crs_measured_natively():
    sq = box(500_000, 1_400_000, 500_100, 1_400_100)
    res = measure_geometry(sq, CRS.from_epsg(32643))
    assert res.measurements["area_m2"] == pytest.approx(10_000)


def test_altitude_is_ignored():
    flat = square_lonlat(77.59, 12.97, 100)
    raised = Polygon([(x, y, 500 + i * 10) for i, (x, y) in enumerate(flat.exterior.coords)])
    a, b = measure_geometry(flat, WGS84), measure_geometry(raised, WGS84)
    assert a.measurements == b.measurements


def test_invalid_bowtie_is_repaired_with_warning():
    bowtie = Polygon([(500_000, 1_400_000), (500_100, 1_400_100), (500_100, 1_400_000), (500_000, 1_400_100)])
    assert not bowtie.is_valid
    res = measure_geometry(bowtie, CRS.from_epsg(32643))
    assert res.status == MEASURED
    assert res.measurements["area_m2"] == pytest.approx(5_000)
    assert any("make_valid" in w for w in res.warnings)


def test_point_needs_no_measurement():
    res = measure_geometry(Point(77.59, 12.97), WGS84)
    assert res.status == NOT_APPLICABLE and res.measurements == {}


def test_geometry_collection_is_unsupported_not_a_crash():
    gc = GeometryCollection([Point(0, 0), LineString([(0, 0), (1, 1)])])
    res = measure_geometry(gc, WGS84)
    assert res.status == UNSUPPORTED and "GeometryCollection" in res.reason


def test_null_and_empty_geometries():
    assert measure_geometry(None, WGS84).status == UNSUPPORTED
    assert measure_geometry(Polygon(), WGS84).status == UNSUPPORTED


def test_out_of_range_lonlat_fails_that_feature_only():
    res = measure_geometry(box(200, 10, 201, 11), WGS84)
    assert res.status == FAILED and "range" in res.reason


def test_summary_totals():
    results = [
        measure_geometry(square_lonlat(77.59, 12.97, 100), WGS84),
        measure_geometry(LineString([(78.80, 14.40), (78.81, 14.41)]), WGS84),
        measure_geometry(Point(1, 1), WGS84),
        measure_geometry(GeometryCollection([Point(0, 0), Point(1, 1)]), WGS84),
    ]
    s = summarize(results)
    assert s["feature_count"] == 4
    assert s["by_status"] == {MEASURED: 2, NOT_APPLICABLE: 1, UNSUPPORTED: 1}
    assert s["total_area_m2"] == pytest.approx(10_000, rel=0.003)
    assert s["total_length_m"] > 1000
