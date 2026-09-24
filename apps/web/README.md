# twiner web

React + MapLibre GL frontend for the twiner seismic scenario simulator. See
the repo root [`README.md`](../../README.md) for the full quickstart and
[`docs/milestone-1-plan.md`](../../docs/milestone-1-plan.md) for context.

## Local dev

Needs `buildings.pmtiles`/`debris.pmtiles`/`municipalities.pmtiles` (from
`pipelines/exposure`, see root README and
[ADR-0013](../../docs/decisions/0013-municipal-boundary-choropleth.md))
copied into `public/data/`, and the scenario function
(`services/scenario`) running -- see root README.

```bash
npm install
npm run dev
```

Env vars (optional, `.env.local`):

- `VITE_SCENARIO_API_URL` -- scenario function base URL (default `http://localhost:8000`)
- `VITE_S3_DATA_BUCKET` -- the public data bucket's name (the stack's `DataBucketName` output); buildings/debris/municipalities PMTiles are read from its `tiles/` prefix in eu-south-2. Unset: served locally from `/data/*.pmtiles` (`apps/web/public/data`)

## Known gotcha

Vite's dependency pre-bundler mishandles `maplibre-gl`'s worker file --
`vite.config.ts` excludes it from pre-bundling (`optimizeDeps.exclude`).
Without that, the map canvas stays blank with no console error. See
`docs/milestone-1-plan.md` §7 "Implementation gotchas" for this and the
`setFeatureState`/`sourceLayer` gotcha in `DamageMap.tsx`.
