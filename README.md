# twiner

A digital twin for multi-hazard risk assessment in Spain, starting with
seismic risk. Milestone 1 built a modern clone of UPM's
[MERISUR](docs/merisur.md) web simulator, prototyped against Lorca;
milestone 2 expanded exposure coverage region by region -- Murcia +
Andalucía first, then nationwide via Catastro's INSPIRE feed, then the
Basque Country and Navarra (which run separate Foral cadastral systems, so
they needed their own crawlers -- see
[`docs/decisions/0005-region-scale-crawling.md`](docs/decisions/0005-region-scale-crawling.md)
and [`docs/basque-navarra-cadastral-sources.md`](docs/basque-navarra-cadastral-sources.md)).
All of Spain is now covered, in the single `data/exposure` dataset (the
Lorca-only and Murcia+Andalucía-only datasets those milestones used along
the way have been retired).
See [`docs/milestone-1-plan.md`](docs/milestone-1-plan.md) for the
milestone-1 plan, [`docs/decisions/`](docs/decisions/) for architecture
decisions, and [`DATA-SOURCES.md`](DATA-SOURCES.md) for every external
dataset in use, with links to the actual resource (ATOM feed / WFS
endpoint) rather than a homepage.

## Layout

```
apps/web/         React + MapLibre frontend
services/scenario/  Scenario function (rupture -> ground motion -> damage);
                     runs as a local dev server or an AWS Lambda
pipelines/           Three ETL pipelines (faults, exposure, fragility) --
                     see pipelines/README.md for how each works
infra/               AWS CDK app (S3 buckets + scenario Lambda)
docs/                Research notes, plans, and architecture decisions
bin/twiner            Start/stop/restart the local dev stack (see below)
```

Python packages are a `uv` workspace (one `.venv` for everything under
`services/` and `pipelines/`); the frontend is a separate npm project under
`apps/web/`.

## Quickstart

Requires: `uv`, Node.js, [`tippecanoe`](https://github.com/felt/tippecanoe)
(`brew install tippecanoe`).

```bash
uv sync --all-packages

# 1. Run the data pipelines (writes into ./data/, gitignored). The
# exposure crawl covers all of Spain -- Catastro's national INSPIRE feed
# via --spain, plus the Basque Country/Navarra's own separate Foral
# cadastral systems via --basque-navarra (resumable, safe to re-run/Ctrl-C
# -- see docs/decisions/0005-region-scale-crawling.md and
# pipelines/README.md). buildings.parquet ends up *partitioned*
# ("data/exposure/parts/*.buildings.parquet") -- there's no single
# combined buildings file, by design (see region.py's own docstring);
# exposure.parquet is combined into one file.
uv run python -m faults data/faults/qafi_faults.parquet   # requires `unar` (brew install unar)
uv run python -m exposure.region_cli data/exposure/raw data/exposure/parts \
    data/exposure/exposure.parquet data/exposure/buildings.pmtiles \
    --spain --basque-navarra
uv run python -m fragility data/fragility/fragility.parquet

# 1b. Municipal boundaries + aggregate stats (ADR-0013) -- powers the map's
# low-zoom choropleth. Needs the parts_dir from step 1 to count buildings
# per municipality. Independent of the exposure crawl otherwise -- always
# one national download.
uv run python -m exposure.municipalities_cli data/exposure/muni_raw data/exposure/parts \
    data/exposure/municipalities.pmtiles data/exposure/municipalities.parquet

# 2. Copy whichever buildings.pmtiles/debris.pmtiles/municipalities.pmtiles
# you built into the frontend's static assets
cp data/exposure/buildings.pmtiles apps/web/public/data/buildings.pmtiles
cp data/exposure/municipalities.pmtiles apps/web/public/data/municipalities.pmtiles

# 3. Start both the scenario API and the frontend together
npm install --prefix apps/web
./bin/twiner start   # see `twiner status`/`twiner attach`/`twiner stop`/`twiner restart`
```

Open http://localhost:5173, submit a manual rupture or pick a fault in the
sidebar, and the map colors buildings by resulting damage state.
`bin/twiner` defaults to whichever dataset `TWINER_BUILDINGS_PATH`/
`TWINER_EXPOSURE_PATH` point at (see the script's own comments) -- set
those env vars before `twiner start` to point at a different one, e.g. a
single-municipality test crawl (`uv run python -m exposure <raw_dir>
<buildings.parquet> <exposure.parquet> <buildings.pmtiles>`, pointed at a
directory other than `data/exposure` so it doesn't collide with the full
national dataset) instead of the full national crawl above.

## Working in multiple worktrees

`data/` and `apps/web/public/data/` are gitignored (the pipeline outputs
above run into the multi-GB range) and a `git worktree` doesn't share a
working directory with the one it was added from, so a fresh worktree has
neither by default. Run `bin/link-data` from inside a new worktree to
symlink both to the main worktree's copies instead of re-running the ETL
there -- it's a no-op in the main worktree itself, and refuses to touch
either path if it already has real content.

## Tests

```bash
uv run pytest              # all Python packages
cd apps/web && npx tsc --noEmit && npm run build
```

## Cloud deployment

See [`docs/decisions/0016-cloud-deployment.md`](docs/decisions/0016-cloud-deployment.md)
for the architecture (AWS Lambda + S3 backend, Cloudflare Pages frontend),
[`docs/deploy-aws-setup.md`](docs/deploy-aws-setup.md) for AWS account
setup + `cdk deploy` + data upload, and
[`docs/deploy-cloudflare.md`](docs/deploy-cloudflare.md) for the frontend.

## Dev tooling

- **Linting/formatting**: [`ruff`](https://docs.astral.sh/ruff/) (`uv run ruff check .`, `uv run ruff format .`).
- **Type checking**: [`pyrefly`](https://pyrefly.org/) (`uv run pyrefly check`).
- **Pre-commit hooks** run both automatically: `uv run pre-commit install` once per clone, then every commit runs `ruff check --fix`, `ruff format`, and `pyrefly check`. Run manually over everything with `uv run pre-commit run --all-files`.
