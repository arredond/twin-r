"""Pure regex-parsing tests for catastro.py -- no network involved.

Uses a trimmed-down synthetic ATOM feed fragment shaped like the real
Catastro response (verified by hand against real feed output when this
module was built), so these tests catch a regressed regex without needing
a live request.
"""

from exposure.catastro import _MUNICIPALITY_ENTRY_RE, _PROVINCE_LINK_RE

SAMPLE_ROOT_FEED = """
<feed>
  <entry>
    <title>Territorial office 30 Murcia</title>
    <link rel="enclosure" href="http://www.catastro.hacienda.gob.es/INSPIRE/buildings/30/ES.SDGC.bu.atom_30.xml"/>
  </entry>
  <entry>
    <title>Territorial office 28 Madrid</title>
    <link rel="enclosure" href="http://www.catastro.hacienda.gob.es/INSPIRE/buildings/28/ES.SDGC.bu.atom_28.xml"/>
  </entry>
</feed>
"""

SAMPLE_PROVINCE_FEED = """
<feed>
  <entry>
    <title> 30024-LORCA buildings</title>
    <content type="xhtml"><div></div></content>
    <link rel="enclosure" href="https://www.catastro.hacienda.gob.es/INSPIRE/Buildings/30/30024-LORCA/A.ES.SDGC.BU.30024.zip" type="application/atom+xml"/>
    <id>https://www.catastro.hacienda.gob.es/INSPIRE/Buildings/30/30024-LORCA/A.ES.SDGC.BU.30024.zip</id>
  </entry>
  <entry>
    <title> 28900-MADRID buildings</title>
    <content type="xhtml"><div></div></content>
    <link rel="enclosure" href="https://www.catastro.hacienda.gob.es/INSPIRE/Buildings/28/28900-MADRID/A.ES.SDGC.BU.28900.zip" type="application/atom+xml"/>
  </entry>
</feed>
"""


def test_province_link_regex_extracts_url_and_code():
    matches = _PROVINCE_LINK_RE.findall(SAMPLE_ROOT_FEED)
    assert len(matches) == 2
    url, code = matches[0]
    assert code == "30"
    assert url.endswith("ES.SDGC.bu.atom_30.xml")


def test_municipality_entry_regex_extracts_code_name_and_zip_url():
    matches = _MUNICIPALITY_ENTRY_RE.findall(SAMPLE_PROVINCE_FEED)
    assert len(matches) == 2

    code, name, zip_url = matches[0]
    assert code == "30024"
    assert name.strip() == "LORCA"
    assert zip_url.endswith("A.ES.SDGC.BU.30024.zip")


def test_municipality_entry_regex_handles_madrid_capital_code_mismatch():
    # Regression case: Madrid capital is filed under Catastro's internal
    # code 28900, not its real INE code 28079 -- the parser must not assume
    # the two coincide (see MunicipalityRef's docstring).
    matches = _MUNICIPALITY_ENTRY_RE.findall(SAMPLE_PROVINCE_FEED)
    code, name, _ = matches[1]
    assert code == "28900"
    assert name.strip() == "MADRID"


def test_municipality_entry_regex_tolerates_extra_whitespace_between_tags():
    # re.DOTALL is load-bearing: real feed content has other XML elements
    # (content, id, updated...) between the title and the zip href, not
    # just whitespace -- confirm the non-greedy `.*?` still finds the
    # *first* href after the title rather than skipping past it or
    # matching too eagerly across entries.
    fragment = """
    <entry>
      <title>30024-LORCA buildings</title>
      <content>...</content>
      <updated>2026-01-01T00:00:00Z</updated>
      <link href="https://example.org/first.zip"/>
    </entry>
    <entry>
      <title>30025-LORQUI buildings</title>
      <link href="https://example.org/second.zip"/>
    </entry>
    """
    matches = _MUNICIPALITY_ENTRY_RE.findall(fragment)
    assert matches == [
        ("30024", "LORCA", "https://example.org/first.zip"),
        ("30025", "LORQUI", "https://example.org/second.zip"),
    ]
