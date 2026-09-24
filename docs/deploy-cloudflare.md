# Frontend deployment: Cloudflare Pages

`apps/web` deploys as a static build to Cloudflare Pages, served at
`https://twiner.arredon.do`. See ADR-0016 for why (Pages caps individual
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
   - Node version comes from `apps/web/.node-version` (pinned to 22, which
     Vite 8 supports) rather than whatever the Pages build image defaults
     to.
3. Environment variables (Pages project → Settings → Environment
   variables), for **Production** (add matching **Preview** values too if
   you want preview deploys to hit the same backend):
   - `VITE_SCENARIO_API_URL` — the `ScenarioFunctionUrl` CDK output from
     `deploy-aws-setup.md` step 7 (e.g.
     `https://xxxxxxxx.lambda-url.eu-west-1.on.aws/`)
   - `VITE_TILES_API_URL` — the `TilesFunctionUrl` CDK output, same step
     (a separate Lambda/Function URL from the scenario one above -- see
     infra/stacks/twiner_stack.py's own comment on why they're split).
     Falls back to `VITE_SCENARIO_API_URL` if unset, which is wrong in
     production (that's the scenario function's URL, not the tiles one) --
     always set this explicitly for a real deploy.
   - `VITE_S3_DATA_BUCKET` — the `DataBucketName` CDK output. The
     frontend builds all three PMTiles URLs (`tiles/buildings.pmtiles`,
     `tiles/debris.pmtiles`, `tiles/municipalities.pmtiles`) from it, with
     the region hardcoded to `eu-south-2` in `DamageMap.tsx` — update that
     constant if the stack ever moves region.

   These match the `import.meta.env.VITE_*` reads already in
   `scenarioApi.ts`/`DamageMap.tsx` — no code change needed once they're
   set. Current values for the deployed `TwinR-MVP` stack (`eu-south-2`;
   re-check with `aws cloudformation describe-stacks --stack-name
   TwinR-MVP --query 'Stacks[0].Outputs'` if the stack is ever recreated):

   ```
   VITE_SCENARIO_API_URL=https://naw44z44znff5je2ohuyyai7ce0gwpiu.lambda-url.eu-south-2.on.aws/
   VITE_TILES_API_URL=https://y76dsobafxm4ibmxcoif5crxsy0dsysg.lambda-url.eu-south-2.on.aws/
   VITE_S3_DATA_BUCKET=twinr-mvp-databuckete3889a50-qnfinvnljqx9
   ```
4. Deploy. Cloudflare builds and gives you a `*.pages.dev` URL. Only the
   static shell is testable there: `FRONTEND_ORIGINS` (step 3 below)
   allows `twiner.arredon.do` and localhost only, so on `*.pages.dev` (and
   on preview deploys) every Lambda/S3 request fails CORS. Do the full
   end-to-end check on the custom domain.

## 2. Custom domain: `twiner.arredon.do`

Since `arredon.do` is already a Cloudflare-managed zone:

1. Pages project → "Custom domains" → "Set up a custom domain" →
   `twiner.arredon.do`.
2. Cloudflare adds the CNAME automatically (it manages the zone) and
   provisions the certificate — no manual DNS or cert step needed, unlike
   a non-Cloudflare-DNS domain.
3. Wait for the domain to show "Active" (usually under a minute since DNS
   is already on Cloudflare).

## 3. CORS reminder

The S3 data bucket's CORS policy (`infra/stacks/twiner_stack.py`,
`FRONTEND_ORIGINS`) must include `https://twiner.arredon.do` — it does by
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
