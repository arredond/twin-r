"""Write the fault list as a static `faults.json` (the `GET /faults` body).

The frontend loads every fault once, on startup, and that list only
changes when `qafi_faults.parquet` does. Served as a static file next to
the PMTiles archives (apps/web/public/data locally, the data bucket's
`tiles/` prefix when deployed), opening the app no longer waits on the
scenario Lambda, whose cold start (Init plus the first request) was
the slowest part of page load (ADR-0022). `GET /faults` stays, as the
frontend's fallback and for bin/warm-scenario-cache.

Usage:
  python -m scenario.export_faults [--faults PATH] [--out PATH]

Re-run and re-upload it whenever qafi_faults.parquet changes (see
docs/deploy-aws-setup.md).
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from .faults import faults_payload


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    parser.add_argument("--faults", default="data/faults/qafi_faults.parquet")
    parser.add_argument("--out", default="apps/web/public/data/faults.json")
    args = parser.parse_args()

    # allow_nan=False: Python would otherwise write a bare `NaN` for a
    # missing float, which the browser's JSON.parse rejects -- fail here,
    # at export, instead of on every page load.
    body = json.dumps(faults_payload(args.faults), allow_nan=False, separators=(",", ":"))
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(body)
    print(f"export_faults: wrote {len(json.loads(body)['faults'])} faults to {out}")


if __name__ == "__main__":
    main()
