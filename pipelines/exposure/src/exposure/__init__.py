"""twiner exposure pipeline: Catastro buildings -> buildings.parquet + exposure.parquet.

Source: Spanish Directorate General for Cadastre (Catastro), INSPIRE
"Buildings" ATOM download service. Verified working root feed (see
docs/merisur.md and docs/milestone-1-plan.md for why we build from Catastro
rather than reusing any MERISUR dataset -- none is public):

    https://www.catastro.hacienda.gob.es/INSPIRE/buildings/ES.SDGC.bu.atom.xml

Note: this host's TLS certificate chain isn't in curl's default trust store
in some environments (verified during development) but resolves fine via
Python `requests` + `certifi`. Not a data problem, just an environment quirk
-- if it recurs, don't assume the service is down.

No original MERISUR building/vulnerability data exists publicly for Lorca
(docs/merisur.md §4.4) -- this pipeline reconstructs exposure from scratch:
footprint + floors + construction year from Catastro, then a documented
heuristic (taxonomy.py) assigns each building a GEM-taxonomy-ish class
matching the fragility functions in pipelines/fragility.
"""

from .catastro import MunicipalityRef, download_buildings
from .parse import load_buildings
from .taxonomy import assign_taxonomy

LORCA = MunicipalityRef(name="LORCA", ine_code="30024")

__all__ = ["LORCA", "MunicipalityRef", "assign_taxonomy", "download_buildings", "load_buildings"]
