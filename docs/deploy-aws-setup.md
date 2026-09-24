# AWS account setup for the twiner backend

Follow this once, before the first `cdk deploy`. See ADR-0001 and ADR-0016
for why the stack looks the way it does. Costs: the pieces here (S3, one
Lambda behind a Function URL) fit comfortably in AWS's *always-free* tier
(not the 12-month trial one) at MVP traffic -- Lambda's 1M requests +
400,000 GB-seconds/month and S3's 5GB standard storage / 20,000 GET /
2,000 PUT are permanent, not time-limited. The 320MB `buildings-cloud.parquet`
plus `exposure.parquet`/`fragility.parquet`/PMTiles will likely push S3
storage over that 5GB free allowance (PMTiles alone: debris.pmtiles is
9.26GB) -- budget a few dollars/month for S3 storage regardless; a Budget
alarm (step 5) catches anything unexpected.

## 1. Create the AWS account

1. Go to https://aws.amazon.com/ and click "Create an AWS Account".
2. You'll need an email, a payment card (required even for free-tier
   usage, for identity verification and to cover any overage), and phone
   verification.
3. Pick the **Basic support plan** (free) when asked.

## 2. Lock down the root user, create an admin IAM identity

The root login (the email/password from step 1) should never be used
day-to-day -- it can't be permission-restricted. Everything from here on
uses a separate IAM identity instead.

1. Enable MFA on the root user: IAM console → "Security credentials"
   (top-right account menu) → "Assign MFA device". Use an authenticator
   app (Authy, 1Password, Google Authenticator).
2. Enable **IAM Identity Center** (Console search "IAM Identity Center" →
   "Enable"). This gives you SSO-based CLI credentials that expire
   automatically, instead of long-lived access keys sitting in a file.
3. Create yourself a user: Identity Center → "Users" → "Add user" (your
   email, a display name). Verify via the email it sends.
4. Create a permission set: Identity Center → "Permission sets" → "Create
   permission set" → "Predefined" → **AdministratorAccess** (fine for an
   MVP with one developer; scope this down later if others join). Name it
   e.g. `AdministratorAccess`.
5. Create an AWS account assignment: Identity Center → "AWS accounts" →
   select your account → "Assign users" → pick yourself + the permission
   set from step 4.

## 3. Configure the AWS CLI to use it

```
aws configure sso
```

- SSO session name: anything, e.g. `twiner`
- SSO start URL: shown on the Identity Center dashboard ("AWS access
  portal URL")
- SSO region: the region Identity Center itself is enabled in (shown on
  the same dashboard -- often `us-east-1` regardless of where you'll
  deploy resources)
- Pick your account + the `AdministratorAccess` permission set when
  prompted
- CLI default client Region: pick where the *stack* should live (see
  step 4 below) -- `eu-south-2`
- Give the resulting profile a name, e.g. `twiner-admin`

Verify it works:

```
aws sts get-caller-identity --profile twiner-admin
```

Every CDK/AWS CLI command below assumes `--profile twiner-admin` (or
`AWS_PROFILE=twiner-admin` exported in your shell) -- SSO tokens expire after a
few hours, re-run `aws sso login --profile twiner-admin` when a command starts
failing with a token-expired error.

## 4. Pick a region

The Lambda and both S3 buckets should be in the same region (cross-region
S3 reads add latency and, past the free tier, egress cost). Use
**`eu-south-2`** (Aragón, Spain) -- lowest latency to Spanish users, and
confirmed fully supported for everything this stack needs: IAM Identity
Center (`sso`/`identitystore`/`oidc` endpoints all listed for it), Lambda
container images + ECR (part of the region's service list since launch),
and CDK bootstrap/asset publishing (the CDK project's `eu-south-2`
region-registration issue -- github.com/aws/aws-cdk#23619 -- is closed and
merged, so CDK treats it as a normal, fully-registered region rather than a
partial one). `eu-west-1` (Ireland) remains a fine fallback if you ever hit
a region-specific gap, but there's no reason to default to it here.

This is a separate choice from where **IAM Identity Center** itself lives
(step 2-3) -- Identity Center only stores auth/permission metadata, not
your application data, so leaving it wherever it first got enabled (even a
different region) doesn't affect where the actual stack's data and compute
end up. Set the stack's region via `cdk.json`/`--profile`'s configured
region, or pass
`--region` explicitly.

## 5. Set a budget alarm

Before deploying anything: Console → "Billing and Cost Management" →
"Budgets" → "Create budget" → "Zero spend budget" (alerts at any
non-free-tier spend) or a small fixed monthly budget (e.g. $5) with an
alert at 80%/100%. This is the safety net for "did I accidentally leave
something expensive running."

## 6. Bootstrap CDK

If needed, install the CDK Node CLI tool:

```
npm install -g aws-cdk
```

Then, one-time per account+region:

```
cd infra
cdk bootstrap aws://<ACCOUNT_ID>/<REGION> --profile twiner-admin
```

(`<ACCOUNT_ID>` from `aws sts get-caller-identity`.) This creates the
S3 bucket + IAM roles CDK itself uses to deploy — a one-time setup
cost, not part of the app's own stack.

## 7. Deploy the stack

```
cd infra
cdk deploy --profile twiner-admin
```

This builds the scenario Docker image locally (needs Docker running —
`docker ps` should succeed first) and pushes it to a CDK-managed ECR
repo, then creates the S3 buckets + Lambda + Function URL. Note the
`ScenarioFunctionUrl`/`DataBucketName` outputs it prints at the end —
you'll need both for the data upload (docs/deploy-data-upload.md, if
written) and the Cloudflare frontend env vars.

## 8. Upload data

Build the cloud-ready buildings file first (see ADR-0016 for why this is
separate from the local `parts/*.buildings.parquet` glob):

```
uv run --package twiner-exposure python -m exposure.compact_cloud_cli \
    data/exposure/parts data/exposure/buildings-cloud.parquet
```

Then upload everything the Lambda's env vars point at (see
`infra/stacks/twiner_stack.py`'s `environment={...}` for the exact keys):

```
aws s3 cp data/exposure/buildings-cloud.parquet \
    s3://<DataBucketName>/exposure/buildings-cloud.parquet --profile twiner-admin
aws s3 cp data/exposure/exposure.parquet \
    s3://<DataBucketName>/exposure/exposure.parquet --profile twiner-admin
aws s3 cp data/fragility/fragility.parquet \
    s3://<DataBucketName>/fragility/fragility.parquet --profile twiner-admin
aws s3 cp data/faults/qafi_faults.parquet \
    s3://<DataBucketName>/faults/qafi_faults.parquet --profile twiner-admin
aws s3 cp data/exposure/municipalities.parquet \
    s3://<DataBucketName>/exposure/municipalities.parquet --profile twiner-admin

# PMTiles the frontend fetches directly, EXCEPT buildings.pmtiles below,
# which the tiles Lambda (services/tiles) also range-reads server-side for
# the per-scenario tile-join endpoint -- its key must match
# TWINER_BUILDINGS_PMTILES_KEY (infra/stacks/twiner_stack.py; defaults to
# this exact path, "tiles/buildings.pmtiles", so no override needed if you
# don't move it). debris.pmtiles/municipalities.pmtiles must sit next to it
# under tiles/ too: the frontend derives all three URLs from
# VITE_S3_DATA_BUCKET + these fixed keys (apps/web DamageMap.tsx's
# pmtilesUrl).
aws s3 cp data/exposure/buildings.pmtiles \
    s3://<DataBucketName>/tiles/buildings.pmtiles --profile twiner-admin
aws s3 cp data/exposure/debris.pmtiles \
    s3://<DataBucketName>/tiles/debris.pmtiles --profile twiner-admin
aws s3 cp data/exposure/municipalities.pmtiles \
    s3://<DataBucketName>/tiles/municipalities.pmtiles --profile twiner-admin
```

## 9. Smoke-test

```
curl -s "<ScenarioFunctionUrl>scenarios/fault?fault_id=<some fault_id>" | gunzip
```

(the response is gzip-compressed, see `handler.py`'s `_response`). Expect
a small JSON payload with `scenario_id`/`n_damaged`/`municipality_stats`
(no per-building list -- ADR-0019), not a 500 — a 500 here almost always
means a missing/mistyped S3 key from step 8, or a still-propagating IAM
permission. Run the same curl twice: the second should come back `"cached": true`,
the scenario result cache from ADR-0018.)

**After uploading new data** (any of step 8's parquet files), bump
`DATA_VERSION` in `infra/stacks/twiner_stack.py` and `cdk deploy`, or the
scenario cache keeps serving results computed from the old data -- see
ADR-0018.
