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
from aws_cdk import aws_lambda as lambda_
from aws_cdk import aws_s3 as s3
from constructs import Construct

SCENARIO_SERVICE_DIR = Path(__file__).resolve().parents[2] / "services" / "scenario"


class TwinRStack(Stack):
    def __init__(self, scope: Construct, construct_id: str, **kwargs) -> None:
        super().__init__(scope, construct_id, **kwargs)

        # Pipeline outputs: buildings/exposure/fragility parquet + PMTiles.
        # Written by pipelines/* running locally or in CI, not by the stack
        # itself -- this bucket just needs to exist and be publicly
        # readable for the PMTiles layer the frontend fetches directly.
        data_bucket = s3.Bucket(
            self,
            "DataBucket",
            removal_policy=RemovalPolicy.RETAIN,
            block_public_access=s3.BlockPublicAccess.BLOCK_ALL,
            versioned=False,
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
            lifecycle_rules=[s3.LifecycleRule(expiration=Duration.days(30))],
        )

        scenario_fn = lambda_.DockerImageFunction(
            self,
            "ScenarioFunction",
            code=lambda_.DockerImageCode.from_image_asset(str(SCENARIO_SERVICE_DIR)),
            memory_size=1536,
            timeout=Duration.seconds(60),
            environment={
                "TWIN_R_BUILDINGS_PATH": f"s3://{data_bucket.bucket_name}/exposure/buildings.parquet",
                "TWIN_R_EXPOSURE_PATH": f"s3://{data_bucket.bucket_name}/exposure/exposure.parquet",
                "TWIN_R_FRAGILITY_PATH": f"s3://{data_bucket.bucket_name}/fragility/fragility.parquet",
                "TWIN_R_MUNICIPALITIES_PATH": f"s3://{data_bucket.bucket_name}/exposure/municipalities.parquet",
                "TWIN_R_RESULTS_BUCKET": results_bucket.bucket_name,
            },
        )
        data_bucket.grant_read(scenario_fn)
        results_bucket.grant_write(scenario_fn)

        function_url = scenario_fn.add_function_url(
            auth_type=lambda_.FunctionUrlAuthType.NONE,  # MVP: no auth yet, see milestone-1-plan §8
        )

        CfnOutput(self, "DataBucketName", value=data_bucket.bucket_name)
        CfnOutput(self, "ResultsBucketName", value=results_bucket.bucket_name)
        CfnOutput(self, "ScenarioFunctionUrl", value=function_url.url)
