"""Regression guard for SPAIN_PROVINCES' known "delegación" overflow codes.

Catastro splits its INSPIRE Buildings ATOM feed by its own territorial
office, not strictly by INE province -- several provinces have a *second*
delegación, numbered outside the normal province range, covering a subset
of their own municipalities. Missing one of these numbers from
`SPAIN_PROVINCES` doesn't error or warn anywhere -- the municipalities
filed under it are just silently absent from every crawl, permanently
(confirmed this session: Jerez de la Frontera + 7 others under 53, Vigo +
3 others under 54, both missing from an earlier version of this dict for
long enough that their absence went unnoticed until a much later
municipality-code investigation stumbled onto it).
"""

from exposure.region import SPAIN_PROVINCES

# Every non-standard delegación code confirmed (this session and
# previously, see region.py's own comments) to carry real municipalities
# that don't otherwise appear under their true INE province's own feed.
_KNOWN_OVERFLOW_DELEGACIONES = {
    "51",  # Cartagena-area Murcia/Asturias overflow (e.g. Cartagena)
    "52",  # same pattern (e.g. Gijón)
    "53",  # Jerez de la Frontera + 7 other Cádiz municipalities
    "54",  # Vigo + 3 other Pontevedra municipalities
    "55",  # Ceuta ("Territorial office 55 Ceuta")
    "56",  # Melilla ("Territorial office 56 Melilla")
}


def test_spain_provinces_includes_every_known_overflow_delegacion():
    missing = _KNOWN_OVERFLOW_DELEGACIONES - set(SPAIN_PROVINCES)
    assert not missing, (
        f"SPAIN_PROVINCES is missing known overflow delegación code(s) {missing} -- "
        "the municipalities filed under them will be silently absent from any crawl "
        "using this dict, with no error or warning anywhere in the pipeline"
    )
