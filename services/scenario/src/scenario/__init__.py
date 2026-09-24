"""twiner scenario function: rupture -> ground motion -> damage.

Domain logic only in this package -- no AWS/Lambda-specific code here (see
docs/decisions/0001-compute-and-iac.md). `handler.py` (Lambda) and
`local.py` (dev server) are the only two places allowed to import AWS or
web-framework concerns; everything else is plain Python callable both ways.
"""
