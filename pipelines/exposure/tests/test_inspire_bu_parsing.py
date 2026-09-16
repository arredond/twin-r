"""Tests for inspire_bu.py against a real-shaped (trimmed) INSPIRE
Buildings GML fragment -- verified by hand against real downloaded
samples from all three sources this module serves (Alava's bulk GML,
Navarra's and Gipuzkoa's live WFS) when this module was built; this
fixture mirrors Alava's exact shape (bu-core2d namespace, a single
`anyPoint` construction date) since all three share the same nested
bu-base structure this parser reads off local element names, not a
per-namespace map -- see inspire_bu.py's module docstring.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from exposure.inspire_bu import load_buildings

# Two buildings: one with a full set of attributes (including a two-way
# current-use split, to exercise "pick the highest-percentage one"), one
# with several attributes nil/absent (to exercise the "missing -> NA,
# don't crash" fallbacks) -- same shape observed in real Alava/Gipuzkoa
# data (docs/basque-navarra-cadastral-sources.md).
SAMPLE_GML = """<?xml version="1.0" encoding="UTF-8"?>
<wfs:FeatureCollection xmlns:wfs="http://www.opengis.net/wfs/2.0"
    xmlns:gml="http://www.opengis.net/gml/3.2"
    xmlns:bu-core2d="http://inspire.ec.europa.eu/schemas/bu-core2d/4.0"
    xmlns:bu-base="http://inspire.ec.europa.eu/schemas/bu-base/4.0"
    xmlns:base="http://inspire.ec.europa.eu/schemas/base/3.3"
    xmlns:xlink="http://www.w3.org/1999/xlink"
    xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance">
  <wfs:member>
    <bu-core2d:Building gml:id="ES.TEST.BU.0001">
      <bu-base:dateOfConstruction>
        <bu-base:DateOfEvent>
          <bu-base:anyPoint>1985-06-01T00:00:00</bu-base:anyPoint>
        </bu-base:DateOfEvent>
      </bu-base:dateOfConstruction>
      <bu-base:inspireId>
        <base:Identifier>
          <base:localId>0001</base:localId>
          <base:namespace>ES.TEST.BU</base:namespace>
        </base:Identifier>
      </bu-base:inspireId>
      <bu-base:currentUse>
        <bu-base:CurrentUse>
          <bu-base:currentUse xlink:href="http://inspire.ec.europa.eu/codelist/CurrentUseValue/commerceAndServices"/>
          <bu-base:percentage>30</bu-base:percentage>
        </bu-base:CurrentUse>
      </bu-base:currentUse>
      <bu-base:currentUse>
        <bu-base:CurrentUse>
          <bu-base:currentUse xlink:href="http://inspire.ec.europa.eu/codelist/CurrentUseValue/residential"/>
          <bu-base:percentage>70</bu-base:percentage>
        </bu-base:CurrentUse>
      </bu-base:currentUse>
      <bu-base:numberOfDwellings>4</bu-base:numberOfDwellings>
      <bu-base:numberOfFloorsAboveGround>3</bu-base:numberOfFloorsAboveGround>
      <bu-core2d:geometry2D>
        <bu-base:BuildingGeometry2D>
          <bu-base:geometry>
            <gml:Surface srsName="http://www.opengis.net/def/crs/EPSG/0/4258" srsDimension="2">
              <gml:patches>
                <gml:PolygonPatch>
                  <gml:exterior>
                    <gml:LinearRing>
                      <gml:posList>42.0 -2.0 42.0 -2.001 42.001 -2.001 42.001 -2.0 42.0 -2.0</gml:posList>
                    </gml:LinearRing>
                  </gml:exterior>
                </gml:PolygonPatch>
              </gml:patches>
            </gml:Surface>
          </bu-base:geometry>
          <bu-base:referenceGeometry>true</bu-base:referenceGeometry>
        </bu-base:BuildingGeometry2D>
      </bu-core2d:geometry2D>
    </bu-core2d:Building>
  </wfs:member>
  <wfs:member>
    <bu-core2d:Building gml:id="ES.TEST.BU.0002">
      <bu-base:dateOfConstruction nilReason="http://inspire.ec.europa.eu/codelist/VoidReasonValue/Unpopulated" xsi:nil="true"/>
      <bu-base:inspireId>
        <base:Identifier>
          <base:localId>0002</base:localId>
          <base:namespace>ES.TEST.BU</base:namespace>
        </base:Identifier>
      </bu-base:inspireId>
      <bu-base:numberOfDwellings>0</bu-base:numberOfDwellings>
      <bu-core2d:geometry2D>
        <bu-base:BuildingGeometry2D>
          <bu-base:geometry>
            <gml:Surface srsName="http://www.opengis.net/def/crs/EPSG/0/4258" srsDimension="2">
              <gml:patches>
                <gml:PolygonPatch>
                  <gml:exterior>
                    <gml:LinearRing>
                      <gml:posList>42.1 -2.1 42.1 -2.101 42.101 -2.101 42.101 -2.1 42.1 -2.1</gml:posList>
                    </gml:LinearRing>
                  </gml:exterior>
                </gml:PolygonPatch>
              </gml:patches>
            </gml:Surface>
          </bu-base:geometry>
          <bu-base:referenceGeometry>true</bu-base:referenceGeometry>
        </bu-base:BuildingGeometry2D>
      </bu-core2d:geometry2D>
    </bu-core2d:Building>
  </wfs:member>
</wfs:FeatureCollection>
"""


@pytest.fixture
def sample_gml_path(tmp_path: Path) -> Path:
    path = tmp_path / "sample.gml"
    path.write_text(SAMPLE_GML, encoding="utf-8")
    return path


def test_load_buildings_extracts_core_attributes(sample_gml_path: Path):
    buildings = load_buildings(sample_gml_path)
    assert len(buildings) == 2
    assert set(buildings["building_id"]) == {"ES.TEST.BU.0001", "ES.TEST.BU.0002"}

    b1 = buildings[buildings["building_id"] == "ES.TEST.BU.0001"].iloc[0]
    assert b1["floors"] == 3
    assert b1["construction_year"] == 1985
    assert b1["num_dwellings"] == 4
    # Two current-use splits (30% commerce, 70% residential) -- picks the
    # higher-percentage one, same as the real multi-use Gipuzkoa sample
    # this behavior was verified against.
    assert b1["current_use"] == "residential"


def test_load_buildings_tolerates_missing_attributes(sample_gml_path: Path):
    buildings = load_buildings(sample_gml_path)
    b2 = buildings[buildings["building_id"] == "ES.TEST.BU.0002"].iloc[0]
    # nil construction date, no numberOfFloorsAboveGround, no currentUse
    # at all -- these fall back to NA rather than raising.
    assert pd_isna(b2["construction_year"])
    assert pd_isna(b2["floors"])
    assert pd_isna(b2["current_use"])
    assert b2["num_dwellings"] == 0


def test_load_buildings_reprojects_to_wgs84(sample_gml_path: Path):
    buildings = load_buildings(sample_gml_path)
    assert str(buildings.crs).upper() in ("EPSG:4326", "WGS 84")
    # Geometry coordinates land in (lon, lat) order -- both test features
    # sit at longitude around -2, latitude around 42, not the reverse
    # (would indicate a GML axis-order swap bug -- see inspire_bu.py's
    # note that GDAL, not this module, handles the EPSG:4258 axis flip).
    b1 = buildings[buildings["building_id"] == "ES.TEST.BU.0001"].iloc[0]
    minx, miny, _maxx, _maxy = b1["geometry"].bounds
    assert -2.01 < minx < -1.99
    assert 41.99 < miny < 42.01


def pd_isna(value: Any) -> bool:
    import pandas as pd

    return bool(pd.isna(value))
