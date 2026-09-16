#!/usr/bin/env python3
"""CDK app entrypoint. Run via `npx aws-cdk <command>` from this directory
(or repo root with `--app "uv run --package twin-r-infra python infra/app.py"`).

See docs/decisions/0001-compute-and-iac.md for why CDK/Lambda, and
docs/milestone-1-plan.md §7 task 8 for what this stack needs to cover.

Status: not yet `cdk synth`-tested against a real AWS account -- this is a
first-pass skeleton (buckets + scenario Lambda + function URL), written to
be reviewed and iterated on rather than deployed as-is.
"""

import aws_cdk as cdk
from stacks.twin_r_stack import TwinRStack

app = cdk.App()
TwinRStack(app, "TwinR-MVP")
app.synth()
