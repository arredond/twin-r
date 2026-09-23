# Frontend deployment: Cloudflare Pages

`apps/web` deploys as a static build to Cloudflare Pages, served at
`https://twin-r.arredon.do`. See ADR-0016 for why (Pages caps individual
assets at 25MB, so PMTiles/parquet aren't bundled into the deploy -- they're
fetched at runtime from the public S3 data bucket the AWS side sets up,
docs/deploy-aws-setup.md).

## 1. One-time: connect the repo

1. Cloudflare dashboard → **Workers & Pages** → "Create" → "Pages" →
   "Connect to Git" → pick this repo.
2. Build settings:
   - **Root directory**: `apps/web`
   - **Build command**: `npm run build`
   - **Build output directory**: `dist`
   - **Framework preset**: Vite (if offered) or leave as "None" — the
     defaults above are enough, no Pages-specific config needed.
3. Environment variables (Pages project → Settings → Environment
   variables), for **Production** (add matching **Preview** values too if
   you want preview deploys to hit the same backend):
   - `VITE_SCENARIO_API_URL` — the `ScenarioFunctionUrl` CDK output from
     `deploy-aws-setup.md` step 7 (e.g.
     `https://xxxxxxxx.lambda-url.eu-west-1.on.aws/`)
   - `VITE_TILES_API_URL` — the `TilesFunctionUrl` CDK output, same step
     (a separate Lambda/Function URL from the scenario one above -- see
     infra/stacks/twin_r_stack.py's own comment on why they're split).
     Falls back to `VITE_SCENARIO_API_URL` if unset, which is wrong in
     production (that's the scenario function's URL, not the tiles one) --
     always set this explicitly for a real deploy.
   - `VITE_BUILDINGS_PMTILES_URL` — `https://<DataBucketName>.s3.<region>.amazonaws.com/tiles/buildings.pmtiles`
   - `VITE_DEBRIS_PMTILES_URL` — same bucket, `tiles/debris.pmtiles`
   - `VITE_MUNICIPALITIES_PMTILES_URL` — same bucket, `tiles/municipalities.pmtiles`

   These match the `import.meta.env.VITE_*` reads already in
   `scenarioApi.ts`/`DamageMap.tsx` — no code change needed once they're
   set.
4. Deploy. Cloudflare builds and gives you a `*.pages.dev` URL first —
   confirm the app actually loads and can hit the Lambda before wiring the
   custom domain.

## 2. Custom domain: `twin-r.arredon.do`

Since `arredon.do` is already a Cloudflare-managed zone:

1. Pages project → "Custom domains" → "Set up a custom domain" →
   `twin-r.arredon.do`.
2. Cloudflare adds the CNAME automatically (it manages the zone) and
   provisions the certificate — no manual DNS or cert step needed, unlike
   a non-Cloudflare-DNS domain.
3. Wait for the domain to show "Active" (usually under a minute since DNS
   is already on Cloudflare).

## 3. CORS reminder

The S3 data bucket's CORS policy (`infra/stacks/twin_r_stack.py`,
`FRONTEND_ORIGINS`) must include `https://twin-r.arredon.do` — it does by
default in the stack as written, but if you change the domain, update that
list and re-run `cdk deploy` before the frontend will be able to fetch
PMTiles from it (the browser will show a CORS error in devtools, not a 404,
if this drifts).

## 4. Redeploys

Every push to the connected branch (main, per Cloudflare Pages' default)
triggers a rebuild automatically — no separate deploy step once step 1 is
done. For a manual one-off build from local: `npx wrangler pages deploy
apps/web/dist --project-name=<pages-project-name>` (needs `wrangler`
installed and `wrangler login` run once).
