"""The milestone-1 stack: static data storage + the scenario Lambda.

Deliberately minimal (docs/milestone-1-plan.md §8 "explicitly out of
scope"): one data bucket for pipeline outputs (buildings.parquet,
exposure.parquet, fragility.parquet, buildings.pmtiles), one results bucket
for scenario outputs, one Lambda (packaged as a container image -- the
scenario package pulls in openquake.hazardlib/numpy/scipy, comfortably over
the zip-based Lambda size limit), exposed via a Function URL (no API
Gateway -- nothing here needs its extra features yet).
"""

from __future__ import annotations

from pathlib import Path

from aws_cdk import CfnOutput, Duration, RemovalPolicy, Stack
from aws_cdk import aws_ecr_assets as ecr_assets
from aws_cdk import aws_iam as iam
from aws_cdk import aws_lambda as lambda_
from aws_cdk import aws_s3 as s3
from aws_cdk.aws_lambda_python_alpha import PythonFunction
from constructs import Construct

REPO_ROOT = Path(__file__).resolve().parents[2]
SCENARIO_SERVICE_DIR = REPO_ROOT / "services" / "scenario"
TILES_SERVICE_DIR = REPO_ROOT / "services" / "tiles"

# The Cloudflare Pages frontend origin, allowed to fetch PMTiles/parquet
# directly out of the data bucket (browser range requests -- see
# apps/web's pmtiles/DuckDB-over-httpfs usage). localhost:5173 stays
# allowed too so local dev can point VITE_S3_DATA_BUCKET at the real bucket
# without a CORS error. Update this if the Cloudflare domain changes.
FRONTEND_ORIGINS = ["https://twiner.arredon.do", "http://localhost:5173"]

# Part of every scenario_id (services/scenario/src/scenario/scenario_id.py),
# so it's what invalidates the scenario result cache after a data change:
# **bump it (and `cdk deploy`) whenever anything the scenario function reads
# from the data bucket is re-uploaded** -- exposure/buildings-cloud/
# fragility/faults parquet. Any string works; a date keeps it readable.
# (Calculation-code changes are covered separately, by scenario_id.py's
# API_VERSION.) See docs/decisions/0018-scenario-result-cache.md.
DATA_VERSION = "2026-09-24"


class TwinerStack(Stack):
    def __init__(self, scope: Construct, construct_id: str, **kwargs) -> None:
        super().__init__(scope, construct_id, **kwargs)

        # Pipeline outputs: buildings/exposure/fragility parquet + PMTiles.
        # Written by pipelines/* running locally or in CI, not by the stack
        # itself. Public-read + CORS: the frontend fetches PMTiles tiles and
        # (for DuckDB-in-browser experiments, if any) parquet directly from
        # here via HTTP range requests, not through the Lambda -- none of
        # this data is sensitive (public Catastro/QAFI/IGN sources, see
        # DATA-SOURCES.md), so bucket-level public read is the simplest
        # thing that works without standing up CloudFront yet.
        data_bucket = s3.Bucket(
            self,
            "DataBucket",
            removal_policy=RemovalPolicy.RETAIN,
            block_public_access=s3.BlockPublicAccess(
                block_public_acls=True,
                ignore_public_acls=True,
                block_public_policy=False,
                restrict_public_buckets=False,
            ),
            cors=[
                s3.CorsRule(
                    allowed_methods=[s3.HttpMethods.GET, s3.HttpMethods.HEAD],
                    allowed_origins=FRONTEND_ORIGINS,
                    allowed_headers=["*"],
                    max_age=3000,
                )
            ],
            versioned=False,
        )
        data_bucket.add_to_resource_policy(
            iam.PolicyStatement(
                actions=["s3:GetObject"],
                resources=[data_bucket.arn_for_objects("*")],
                # The standard CDK idiom for a public-read bucket policy --
                # pyrefly flags it as a structural mismatch because the
                # jsii-generated stub for AnyPrincipal.add_to_principal_policy
                # names its parameter `_statement` instead of `statement`,
                # not a real type error (`cdk synth` succeeds; this is the
                # same shape used in AWS's own CDK examples).
                principals=[iam.AnyPrincipal()],  # pyrefly: ignore
            )
        )

        # Scenario results (thin building_id -> damage JSON, see
        # docs/decisions/0003-precomputed-building-tiles.md). Short
        # lifecycle -- these are cheap to recompute, not a system of record.
        results_bucket = s3.Bucket(
            self,
            "ResultsBucket",
            removal_policy=RemovalPolicy.DESTROY,
            auto_delete_objects=True,
            block_public_access=s3.BlockPublicAccess.BLOCK_ALL,
            # A year, not the original 30 days: the bucket doubles as the
            # scenario result cache (ADR-0018), which bin/warm-scenario-cache
            # fills for every fault x probability level, and a monthly
            # expiry would silently empty it. Correctness doesn't depend on
            # expiry (scenario_ids change whenever API_VERSION or
            # DATA_VERSION does, orphaning old entries), and storage stays
            # small (a full sweep is ~0.1-1.3GB). The flip side: a
            # *forgotten* version bump now serves stale results for up to
            # a year instead of a month.
            lifecycle_rules=[s3.LifecycleRule(expiration=Duration.days(365))],
            # Private and backend-only: written by scenario_fn, read by
            # scenario_fn (the scenario cache) and tiles_fn (the tile
            # joins). The browser never reads it -- no public policy, no
            # CORS, no presigned URLs (ADR-0019).
        )

        scenario_fn = lambda_.DockerImageFunction(
            self,
            "ScenarioFunction",
            code=lambda_.DockerImageCode.from_image_asset(
                # Repo root, not SCENARIO_SERVICE_DIR -- the image also
                # needs services/tiles (results_store.py, imported by
                # handler.py's compute path to write S3 results the tiles
                # Lambda reads back), a sibling directory Docker can't see
                # if the build context is scoped to services/scenario/
                # alone (confirmed the hard way: an earlier build without
                # this pointed the context at services/scenario/ only,
                # and every scenario request failed at runtime with
                # `ModuleNotFoundError: No module named 'tiles'`).
                str(REPO_ROOT),
                file="services/scenario/Dockerfile",
                # Without this, `docker build` targets the host machine's
                # own architecture -- fine on an x86_64 CI runner, but on
                # Apple Silicon it builds a linux/arm64 image. fiona (a
                # transitive openquake.hazardlib runtime dependency, see
                # services/scenario/pyproject.toml) doesn't have a
                # prebuilt wheel for every arm64-Linux/cpython combination,
                # so pip falls back to a from-source build that needs
                # GDAL's `gdal-config` -- which the Lambda base image
                # doesn't have -- and fails. Pinning amd64 sidesteps that
                # entirely (best wheel coverage for the scientific-Python
                # stack this function depends on) and matches the
                # function's own architecture below, regardless of what
                # machine `cdk deploy` runs on.
                platform=ecr_assets.Platform.LINUX_AMD64,
            ),
            architecture=lambda_.Architecture.X86_64,
            # Lambda's allotted network throughput scales with memory, and
            # this function is dominated by S3 range-reads over httpfs
            # (engine.py's _load_sites), not CPU -- more memory buys more
            # bandwidth for the same per-ms cost math the 1536MB default
            # was on, at zero extra risk to the always-free tier (400,000
            # GB-seconds/month covers ~200k requests/month at ~1s each
            # here, comfortably above expected MVP traffic). Bumped again
            # from 2048 to 3008 after observing a real large-fault request
            # peak at 2047MB/2048MB -- uncomfortably close to an outright
            # OOM kill, not just slow (docs/known-issues-cloud-deploy.md
            # has the open questions on *why* it's this memory/time-heavy;
            # this is a headroom mitigation, not that root-cause fix).
            #
            # timeout bumped 60s -> 120s for the same reason from the other
            # direction: real fault-mode requests observed in the 12-33s
            # range, with enough variance that some hit the old 60s ceiling
            # outright (confirmed via CloudWatch: `Status: timeout` at
            # exactly 60000ms, no exception -- these surfaced to the
            # frontend as a 502, since a Function URL's own response wait
            # is bounded by the Lambda's configured timeout).
            memory_size=3008,
            timeout=Duration.seconds(120),
            environment={
                # numba (a transitive dep via openquake.hazardlib's
                # baselib.performance, used for its @compile-decorated
                # geodetic functions) writes its JIT disk cache next to the
                # source .py file by default -- fine locally, but Lambda's
                # filesystem outside /tmp is read-only, so without this the
                # function crashes on every cold start with
                # "RuntimeError: cannot cache function ...: no locator
                # available", confirmed against the real deployed Lambda.
                "NUMBA_CACHE_DIR": "/tmp/numba_cache",
                # Lambda doesn't set $HOME. DuckDB's `INSTALL httpfs`
                # resolves a home directory to find its extensions
                # directory and fails hard without one ("IO Error: Can't
                # find the home directory at ''") -- confirmed against the
                # real deployed Lambda, and it's the one DuckDB call every
                # request path goes through (db.ensure_httpfs). Also fixes
                # a (non-fatal, but noisy) matplotlib warning about the
                # same missing $HOME when writing its config cache.
                "HOME": "/tmp",
                "MPLCONFIGDIR": "/tmp/matplotlib",
                # buildings-cloud.parquet: a single spatially-sorted file
                # (pipelines/exposure/compact_cloud_cli.py), not the
                # `parts/*.buildings.parquet` glob local dev uses -- see
                # region.compact_buildings_for_cloud's docstring for why
                # the glob doesn't work well over S3.
                "TWINER_BUILDINGS_PATH": f"s3://{data_bucket.bucket_name}/exposure/buildings-cloud.parquet",
                "TWINER_EXPOSURE_PATH": f"s3://{data_bucket.bucket_name}/exposure/exposure.parquet",
                "TWINER_FRAGILITY_PATH": f"s3://{data_bucket.bucket_name}/fragility/fragility.parquet",
                # Missing here would 500 every fault-mode (non-manual)
                # scenario request in the cloud -- handler.py falls back to
                # a local-only default path that doesn't exist in Lambda.
                "TWINER_FAULTS_PATH": f"s3://{data_bucket.bucket_name}/faults/qafi_faults.parquet",
                "TWINER_MUNICIPALITIES_PATH": f"s3://{data_bucket.bucket_name}/exposure/municipalities.parquet",
                "TWINER_RESULTS_BUCKET": results_bucket.bucket_name,
                # Content-addressed scenario cache (ADR-0018): a repeat of
                # an already-computed scenario returns the stored result
                # from the results bucket instead of recomputing. Set to
                # "0" to force every request to recompute.
                "TWINER_SCENARIO_CACHE": "1",
                "TWINER_DATA_VERSION": DATA_VERSION,
            },
        )
        data_bucket.grant_read(scenario_fn)
        # read, not just write: a scenario cache hit reads the stored
        # response back (ADR-0018).
        results_bucket.grant_read_write(scenario_fn)

        # Deliberately a separate, lightweight (zip-packaged, not
        # container-image) Lambda from scenario_fn above -- that one pulls
        # in openquake.hazardlib/numpy/scipy/fiona/GDAL (confirmed 7-9s+
        # cold starts even for its own routes that never touch physics,
        # see services/scenario/handler.py's own comment on why
        # engine.py/ground_motion.py are imported lazily there). This
        # function needs only pandas/pmtiles/mapbox_vector_tile's protobuf
        # schema (services/tiles' own tile_join.py), so its cold start and
        # deployment package stay small regardless of how heavy the
        # compute Lambda gets, and a tile-request burst (a zoomed map
        # fires a dozen-plus at once) never competes with scenario compute
        # for the same function's concurrency/memory budget.
        tiles_fn = PythonFunction(
            self,
            "TilesFunction",
            # `entry` must be the directory containing both the handler
            # module and a requirements.txt/uv.lock the bundler can find
            # (see services/tiles/src/requirements.txt's own comment on
            # why it lives here and not up at services/tiles/pyproject.toml,
            # a src-layout package) -- points at `src/`, not the package
            # root, so `tiles/` (with its own __init__.py) lands at the
            # Lambda's package root and `tiles.handler.handler` resolves.
            entry=str(TILES_SERVICE_DIR / "src"),
            index="tiles/handler.py",
            handler="handler",
            runtime=lambda_.Runtime.PYTHON_3_12,
            architecture=lambda_.Architecture.X86_64,
            # No CPU/network-heavy work here (see tile_join.py's own
            # measurements -- tens of ms per tile once warm) -- well below
            # scenario_fn's memory, kept modest on purpose rather than
            # copying that function's reasoning over.
            memory_size=512,
            timeout=Duration.seconds(10),
            environment={
                # buildings.pmtiles and debris.pmtiles are read from their
                # default keys (tiles/*.pmtiles, services/tiles/handler.py),
                # the same ones docs/deploy-aws-setup.md uploads to.
                "TWINER_DATA_BUCKET": data_bucket.bucket_name,
                "TWINER_RESULTS_BUCKET": results_bucket.bucket_name,
            },
        )
        # Read-only both ways -- this function never writes to either
        # bucket, only scenario_fn (results) and the pipelines (data) do.
        data_bucket.grant_read(tiles_fn)
        results_bucket.grant_read(tiles_fn)

        tiles_function_url = tiles_fn.add_function_url(
            auth_type=lambda_.FunctionUrlAuthType.NONE,  # MVP: no auth yet, see milestone-1-plan §8
            cors=lambda_.FunctionUrlCorsOptions(
                allowed_origins=FRONTEND_ORIGINS,
                allowed_methods=[lambda_.HttpMethod.GET],
                allowed_headers=["content-type"],
            ),
        )

        function_url = scenario_fn.add_function_url(
            auth_type=lambda_.FunctionUrlAuthType.NONE,  # MVP: no auth yet, see milestone-1-plan §8
            # Without this, the browser fetch from scenarioApi.ts fails
            # with a CORS error -- local.py's FastAPI dev server adds
            # CORSMiddleware for local dev, but handler.py (this Lambda)
            # doesn't add CORS headers itself, and a Function URL doesn't
            # add any by default either. Same origin list as the data
            # bucket's CORS rule above.
            cors=lambda_.FunctionUrlCorsOptions(
                allowed_origins=FRONTEND_ORIGINS,
                allowed_methods=[lambda_.HttpMethod.GET, lambda_.HttpMethod.POST],
                allowed_headers=["content-type"],
            ),
        )

        CfnOutput(self, "DataBucketName", value=data_bucket.bucket_name)
        CfnOutput(self, "ResultsBucketName", value=results_bucket.bucket_name)
        CfnOutput(self, "ScenarioFunctionUrl", value=function_url.url)
        CfnOutput(self, "TilesFunctionUrl", value=tiles_function_url.url)
