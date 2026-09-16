# twin-r web

React + MapLibre GL frontend for the twin-r seismic scenario simulator. See
the repo root [`README.md`](../../README.md) for the full quickstart and
[`docs/milestone-1-plan.md`](../../docs/milestone-1-plan.md) for context.

## Local dev

Needs `data/exposure/buildings.pmtiles` (from `pipelines/exposure`) copied
into `public/data/buildings.pmtiles`, and the scenario function
(`services/scenario`) running -- see root README.

```bash
npm install
npm run dev
```

Env vars (optional, `.env.local`):

- `VITE_SCENARIO_API_URL` -- scenario function base URL (default `http://localhost:8000`)
- `VITE_BUILDINGS_PMTILES_URL` -- buildings PMTiles URL (default `/data/buildings.pmtiles`)

## Known gotcha

Vite's dependency pre-bundler mishandles `maplibre-gl`'s worker file --
`vite.config.ts` excludes it from pre-bundling (`optimizeDeps.exclude`).
Without that, the map canvas stays blank with no console error. See
`docs/milestone-1-plan.md` §7 "Implementation gotchas" for this and the
`setFeatureState`/`sourceLayer` gotcha in `DamageMap.tsx`.
