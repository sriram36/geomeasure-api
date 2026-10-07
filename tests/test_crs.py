import pytest
from pyproj import CRS
from shapely.geometry import Point, box

from app.services.crs import (
    crs_label,
    is_web_mercator,
    linear_unit_factor,
    select_measurement_crs,
    utm_epsg,
)


@pytest.mark.parametrize(
    "lon,lat,expected",
    [
        (77.59, 12.97, 32643),    # Bengaluru
        (78.82, 14.47, 32644),    # Kadapa
        (151.21, -33.87, 32756),  # Sydney (southern hemisphere -> 327xx)
        (-0.12, 51.5, 32630),     # London
        (-122.4, 37.8, 32610),    # San Francisco
        (180.0, 10.0, 32601),     # antimeridian wraps to zone 1
        (10.0, 88.0, 32661),      # UPS north
        (10.0, -85.0, 32761),     # UPS south
    ],
)
def test_utm_zone_selection(lon, lat, expected):
    assert utm_epsg(lon, lat) == expected


def test_geographic_crs_goes_to_utm():
    sel = select_measurement_crs(box(77.5, 12.9, 77.6, 13.0), CRS.from_epsg(4326))
    assert sel.crs.to_epsg() == 32643 and sel.method == "utm" and sel.unit_factor == 1.0


def test_web_mercator_is_not_used_for_measuring():
    assert is_web_mercator(CRS.from_epsg(3857))
    geom = box(8_600_000, 1_450_000, 8_601_000, 1_451_000)  # around Bengaluru
    sel = select_measurement_crs(geom, CRS.from_epsg(3857))
    assert sel.crs.to_epsg() == 32643 and sel.method == "utm"


def test_projected_crs_is_measured_natively():
    sel = select_measurement_crs(Point(500000, 1400000).buffer(10), CRS.from_epsg(32643))
    assert sel.method == "native" and sel.crs.to_epsg() == 32643 and sel.unit_factor == 1.0


def test_us_survey_foot_unit_factor():
    assert linear_unit_factor(CRS.from_epsg(2263)) == pytest.approx(0.3048006096)


def test_crs_label():
    assert crs_label(CRS.from_epsg(4326)) == "EPSG:4326"
    assert crs_label(None) is None
