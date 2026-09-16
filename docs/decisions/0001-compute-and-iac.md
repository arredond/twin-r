# ADR-0001: Serverless compute on AWS Lambda + CDK, with a Fargate/ECS escape hatch

Status: accepted

## Context

The scenario function (rupture → GMPE → damage) needs to run both in the
cloud and locally for development (milestone-1-plan.md §1), without a
persistent server or RDBMS. We also need an IaC tool to manage the AWS
resources (Lambda, S3 buckets, API layer) as code, per the "every decision
documented" and "cloud-native" project constraints.

## Decision

- **AWS Lambda** is the compute target for the scenario function.
- **AWS CDK** is the IaC tool, in the same language as the scenario function
  (Python) to keep the stack single-language where practical.
- The scenario function's business logic (rupture handling, DuckDB queries,
  GMPE/fragility evaluation) lives in a plain Python package with a thin
  handler adapter for Lambda, and a separate thin adapter (e.g. a local
  `uvicorn`/CLI wrapper) for local dev — the adapters are the only
  Lambda-specific code, so switching compute targets later doesn't touch the
  domain logic.

## Alternatives considered

- **Fargate/ECS from day one**: more appropriate for long-running or
  memory-heavy jobs (e.g. a future national-scale batch run), but adds
  container-lifecycle overhead we don't need for a single-city, sub-second-ish
  scenario calculation. Deferred.
- **Terraform**: viable, but CDK's typed constructs and same-language-as-app
  fit was preferred for a small team; revisit if the stack grows multi-cloud
  or the team gains Terraform-specific needs.

## Consequences

- Lambda has a 15-minute hard timeout and limited memory/CPU ceiling — fine
  for Lorca-scale scenarios, a real constraint once milestone 2 goes
  national. We are explicitly keeping the domain logic decoupled from the
  Lambda handler so that a future move to Fargate/ECS (for long-running,
  whole-country batch jobs) is an adapter swap, not a rewrite.
- CDK app(s) live alongside `services/scenario` in the repo; infra changes go
  through the same review process as application code.
