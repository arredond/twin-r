# twiner

## Before starting any task

- Use a `git worktree` for your work rather than editing directly in
  whichever checkout you land in -- multiple sessions/agents work in this
  repo at once, and a shared main checkout gets uncommitted work from
  other sessions mixed in with yours.
- From inside a new worktree, run `bin/link-data` right away. `data/` and
  `apps/web/public/data/` are gitignored multi-GB pipeline outputs
  (buildings/debris/municipalities PMTiles, exposure parquet, etc.) that a
  fresh worktree doesn't have; the script symlinks both to the main
  worktree's copies instead of needing a from-scratch ETL rerun. It's a
  no-op in the main worktree and refuses to touch either path if it
  already has real content, so it's always safe to run.
- Look through `docs/` (including `docs/decisions/` for ADRs) for
  anything relevant to the task before making changes -- this project has
  accumulated a fair amount of non-obvious context (data source gotchas,
  rejected approaches, validation results) that isn't necessarily visible
  from the code alone.

## Running the app locally

- Always start the local dev stack with `bin/twiner start` (or `restart`),
  never by manually running `uvicorn`/`npm run dev` yourself -- it runs
  both the scenario API and web frontend in a tmux session (`twiner
  status`/`attach`/`stop` manage it). Frontend: http://localhost:5173.
  Scenario API: http://localhost:8000. `bin/twiner start prod` points the
  local frontend at the deployed AWS backend instead.
- Do not use the claude-in-chrome browser extension/tools on this
  project. Debug the frontend via curl/JS logic review, network request
  logs, and console output instead.

See the root `README.md` for the full quickstart, layout, and dev tooling.
