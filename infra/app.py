#!/usr/bin/env python3
"""CDK app entrypoint. Run via `npx aws-cdk <command>` from this directory
(or repo root with `--app "uv run --package twiner-infra python infra/app.py"`).

See docs/decisions/0001-compute-and-iac.md for why CDK/Lambda, and
docs/milestone-1-plan.md §7 task 8 for what this stack needs to cover.

Status: not yet `cdk synth`-tested against a real AWS account -- this is a
first-pass skeleton (buckets + scenario Lambda + function URL), written to
be reviewed and iterated on rather than deployed as-is.
"""

import aws_cdk as cdk
from stacks.twiner_stack import TwinerStack

app = cdk.App()
# Deliberately still "TwinR-MVP" after the twin-r -> twiner rename: a
# CloudFormation stack can't be renamed, so changing this ID would make CDK
# create a brand-new stack (new, empty buckets -- the data bucket's
# multi-GB tiles/parquet re-uploaded by hand -- and new Function URLs)
# rather than update the deployed one. The generated bucket names
# (twinr-mvp-...) derive from it too.
TwinerStack(app, "TwinR-MVP")
app.synth()
