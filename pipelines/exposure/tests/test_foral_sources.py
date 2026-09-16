"""Pure-parsing/logic tests for the Basque Country + Navarra cadastral
source modules (alava.py, navarra.py, gipuzkoa.py, vizcaya.py) -- no
network involved, same "trimmed synthetic fragment, verified by hand
against the real thing" approach as test_catastro_parsing.py.
"""

from __future__ import annotations

from exposure import gipuzkoa
from exposure.alava import _EPSG_4258_ENTRY_RE, _MUNICIPALITY_FILENAME_RE
from exposure.vizcaya import _MUNICIPALITY_RE, _filter_xml

# Trimmed from Alava's real Buildings.atom feed (geo.araba.eus) -- has one
# <entry> per CRS/format; verified this session that the real feed's
# EPSG:4258 entry has this exact <category>...</category>...href=... shape.
SAMPLE_ALAVA_ATOM = """
<feed>
  <entry>
    <category term="http://www.opengis.net/def/crs/EPSG/0/3042" label="ETRS89 / UTM zone 30N (N-E)"/>
    <title>"INSPIRE" Buildings - EPSG:3042 (GML)</title>
    <link rel="alternate" href="https://geo.araba.eus/deskargak/INSPIRE/BU/GML/3042/BU_3042_GML.zip"/>
  </entry>
  <entry>
    <category term="http://www.opengis.net/def/crs/EPSG/0/4258" label="ETRS89"/>
    <title>"INSPIRE" Buildings - EPSG:4258 (GML)</title>
    <link rel="alternate" href="https://geo.araba.eus/deskargak/INSPIRE/BU/GML/4258/BU_4258_GML.zip"/>
  </entry>
</feed>
"""


def test_epsg_4258_entry_regex_picks_the_right_crs_entry():
    match = _EPSG_4258_ENTRY_RE.search(SAMPLE_ALAVA_ATOM)
    assert match is not None
    assert match.group(1).endswith("BU_4258_GML.zip")


def test_municipality_filename_regex_extracts_araba_code():
    match = _MUNICIPALITY_FILENAME_RE.search("ES.AFA.BU.0101_4258.gml")
    assert match is not None
    assert match.group(1) == "0101"

    # A buildingpart-style or differently-suffixed name shouldn't match --
    # Alava's zip doesn't have one, but the regex should stay specific to
    # the exact observed filename shape rather than matching loosely.
    assert _MUNICIPALITY_FILENAME_RE.search("ES.AFA.BU.0101_3042.gml") is None


def test_split_quarters_a_bbox():
    bbox = (0.0, 0.0, 4.0, 8.0)  # (min_lat, min_lon, max_lat, max_lon)
    children = gipuzkoa._split(bbox)
    assert len(children) == 4
    # Every child is half the parent's height and width.
    for min_lat, min_lon, max_lat, max_lon in children:
        assert max_lat - min_lat == 2.0
        assert max_lon - min_lon == 4.0
    # Together, the four quadrants exactly tile the parent bbox with no
    # gap or overlap in their combined extent.
    min_lats = [c[0] for c in children]
    min_lons = [c[1] for c in children]
    max_lats = [c[2] for c in children]
    max_lons = [c[3] for c in children]
    assert min(min_lats) == bbox[0]
    assert min(min_lons) == bbox[1]
    assert max(max_lats) == bbox[2]
    assert max(max_lons) == bbox[3]


# Trimmed from Bizkaia's real Municipios GetFeature response
# (geo.bizkaia.eus) -- verified this session that a real municipality's
# Codigo_Mun immediately precedes its Descripcio with no other tags
# between them, and that Descripcio is right-padded with spaces.
SAMPLE_VIZCAYA_MUNICIPIOS = (
    '<wfs:member><Katastro_Catastro_WFS:Municipios gml:id="Municipios.17601">'
    "<Katastro_Catastro_WFS:OBJECTID>17601</Katastro_Catastro_WFS:OBJECTID>"
    "<Katastro_Catastro_WFS:Codigo_Pro>48</Katastro_Catastro_WFS:Codigo_Pro>"
    "<Katastro_Catastro_WFS:Codigo_Mun>1</Katastro_Catastro_WFS:Codigo_Mun>"
    "<Katastro_Catastro_WFS:Descripcio>ABADIÑO                                      </Katastro_Catastro_WFS:Descripcio>"
    "</Katastro_Catastro_WFS:Municipios></wfs:member>"
    '<wfs:member><Katastro_Catastro_WFS:Municipios gml:id="Municipios.17627">'
    "<Katastro_Catastro_WFS:OBJECTID>17627</Katastro_Catastro_WFS:OBJECTID>"
    "<Katastro_Catastro_WFS:Codigo_Pro>48</Katastro_Catastro_WFS:Codigo_Pro>"
    "<Katastro_Catastro_WFS:Codigo_Mun>20</Katastro_Catastro_WFS:Codigo_Mun>"
    "<Katastro_Catastro_WFS:Descripcio>BILBAO                                       </Katastro_Catastro_WFS:Descripcio>"
    "</Katastro_Catastro_WFS:Municipios></wfs:member>"
)


def test_municipality_regex_extracts_code_and_name():
    matches = _MUNICIPALITY_RE.findall(SAMPLE_VIZCAYA_MUNICIPIOS)
    assert matches == [
        ("1", "ABADIÑO                                      "),
        ("20", "BILBAO                                       "),
    ]


def test_filter_xml_embeds_the_codigo_mun_literal():
    xml = _filter_xml(20)
    assert "<Literal>20</Literal>" in xml
    assert "Codigo_Mun" in xml
