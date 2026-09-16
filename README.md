# twin-r

A digital twin for multi-hazard risk assessment in Spain, starting with
seismic risk. Milestone 1 built a modern clone of UPM's
[MERISUR](docs/merisur.md) web simulator, prototyped against Lorca;
milestone 2 expanded exposure coverage region by region -- Murcia +
Andalucía first, then nationwide via Catastro's INSPIRE feed, then the
Basque Country and Navarra (which run separate Foral cadastral systems, so
they needed their own crawlers -- see
[`docs/decisions/0005-region-scale-crawling.md`](docs/decisions/0005-region-scale-crawling.md)
and [`docs/basque-navarra-cadastral-sources.md`](docs/basque-navarra-cadastral-sources.md)).
All of Spain is now covered.
See [`docs/milestone-1-plan.md`](docs/milestone-1-plan.md) for the
milestone-1 plan and [`docs/decisions/`](docs/decisions/) for architecture
decisions.

## Layout

```
apps/web/         React + MapLibre frontend
services/scenario/  Scenario function (rupture -> ground motion -> damage);
                     runs as a local dev server or an AWS Lambda
pipelines/           Three ETL pipelines (faults, exposure, fragility) --
                     see pipelines/README.md for how each works
infra/               AWS CDK app (S3 buckets + scenario Lambda)
docs/                Research notes, plans, and architecture decisions
bin/twinr            Start/stop/restart the local dev stack (see below)
```

Python packages are a `uv` workspace (one `.venv` for everything under
`services/` and `pipelines/`); the frontend is a separate npm project under
`apps/web/`.

## Quickstart

Requires: `uv`, Node.js, [`tippecanoe`](https://github.com/felt/tippecanoe)
(`brew install tippecanoe`).

```bash
uv sync --all-packages

# 1. Run the data pipelines (writes into ./data/, gitignored)
uv run python -m faults data/faults/qafi_faults.parquet   # requires `unar` (brew install unar)
uv run python -m exposure data/exposure/raw/lorca data/exposure/buildings.parquet \
    data/exposure/exposure.parquet data/exposure/buildings.pmtiles
uv run python -m fragility data/fragility/fragility.parquet

# 1b. Or crawl a whole region instead of one municipality -- defaults to
# Murcia + Andalucía's 9 provinces, pass --spain for every province
# reachable through this pipeline (resumable, safe to re-run/Ctrl-C -- see
# docs/decisions/0005-region-scale-crawling.md and pipelines/README.md).
# buildings.parquet ends up *partitioned*: point TWIN_R_BUILDINGS_PATH at
# "<parts_dir>/*.buildings.parquet".
uv run python -m exposure.region_cli data/exposure_region/raw data/exposure_region/parts \
    data/exposure_region/exposure.parquet data/exposure_region/buildings.pmtiles

# 2. Copy whichever buildings.pmtiles you built into the frontend's static assets
cp data/exposure/buildings.pmtiles apps/web/public/data/buildings.pmtiles

# 3. Start both the scenario API and the frontend together
npm install --prefix apps/web
./bin/twinr start   # see `twinr status`/`twinr attach`/`twinr stop`/`twinr restart`
```

Open http://localhost:5173, submit a manual rupture or pick a fault in the
sidebar, and the map colors buildings by resulting damage state.
`bin/twinr` defaults to whichever dataset `TWIN_R_BUILDINGS_PATH`/
`TWIN_R_EXPOSURE_PATH` point at (see the script's own comments) -- set
those env vars before `twinr start` to point at a different one, e.g. the
single-municipality Lorca dataset from step 1 instead of a region crawl
from step 1b.

## Tests

```bash
uv run pytest              # all Python packages
cd apps/web && npx tsc --noEmit && npm run build
```

## Dev tooling

- **Linting/formatting**: [`ruff`](https://docs.astral.sh/ruff/) (`uv run ruff check .`, `uv run ruff format .`).
- **Type checking**: [`pyrefly`](https://pyrefly.org/) (`uv run pyrefly check`).
- **Pre-commit hooks** run both automatically: `uv run pre-commit install` once per clone, then every commit runs `ruff check --fix`, `ruff format`, and `pyrefly check`. Run manually over everything with `uv run pre-commit run --all-files`.
