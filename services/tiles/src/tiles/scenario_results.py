"""The per-building scenario results file the tile joins read (ADR-0023).

One file per scenario, `<scenario_id>/buildings.columns.v1.json.gz`, written
once by the scenario function after computing a scenario and read by every
tile request for it: the tiles Lambda in the cloud
(`tiles.results_store`), local dev's tile workers otherwise
(`scenario.tile_join`). This module is the only place that writes or reads
it, and it's stdlib only, because the tiles Lambda's zip package can't
carry pandas/pyarrow (see `tiles.results_store`).

Format (version 1)
------------------
A gzipped JSON object with one array per column, all the same length, one
entry per listed building:

    {
      "format": "twiner.scenario-results",
      "version": 1,
      "columns": {
        "building_id":       ["ES.AFA.BU.08030097000001", ...],  // sorted, unique
        "damage_state_code": [1, ...],        // int, index into DAMAGE_STATES
        "prob_none":         [0.3218, ...],   // float, 4 decimals
        "prob_slight":       [0.5651, ...],
        "prob_moderate":     [0.0933, ...],
        "prob_extensive":    [0.0152, ...],
        "prob_complete":     [0.0046, ...]
      }
    }

- Which buildings: only those the scenario function ships, i.e. damaged or
  uncertain ones (`scenario.response.shipped_mask`). A building absent from
  the file was either evaluated as confidently undamaged or never
  evaluated; the frontend tells those apart by `evaluated_region`.
- `building_id` is **sorted** (plain string order) and **unique**. The
  reader relies on both: it binary-searches the list instead of building a
  hash map. `encode` enforces them, keeping the *last* row for a repeated
  id: `building_id` isn't always unique in the pipeline output
  (DATA-SOURCES.md), and keep-last is what the previous format's dict
  build did.
- Every column in `COLUMNS` is required, and no others are allowed. A reader
  gives each listed building exactly these fields, `building_id` aside, as
  its extra tile properties, so the column names *are* the tile property
  names the frontend reads (DamageMap.tsx).
- `format`/`version` are checked on read; a mismatch is an error, not a
  best-effort parse. The version is also in the file name (`FILENAME`), so
  a reader never even opens another version's file: a tile request for a
  scenario stored in an older format finds no results (a 404), rather than
  a file it can't read. Bump `FORMAT_VERSION` on any incompatible change,
  together with `scenario.scenario_id.API_VERSION`, since stored results
  change shape.

Why this shape
--------------
The previous format was a JSON list with one object per building, parsed
into a dict of dicts. For an M9 manual scenario on Madrid (448,557 listed
buildings) that took 0.42s and **+379MB** RSS per scenario on a laptop.
In the 512MB tiles Lambda (~0.3 vCPU), each container's first tile for a
scenario took 8.7-10s, some hit the 10s timeout, and containers were
OOM-killed (2026-09-24). Measured on that same file (laptop; load time,
RSS held after load, lookup time for a 3,000-building tile):

    one object per row, dict (previous)  2.19MB  418ms  +379MB  0.2ms
    one array per column, bisect         2.84MB   94ms   +74MB  1.6ms
    protobuf, bisect                     1.85MB  108ms   +42MB  2.4ms
    parquet via pyarrow, bisect          3.59MB   34ms   +46MB  3.2ms
    bespoke binary, bisect               1.84MB   18ms   +26MB  1.3ms

The benchmark stored probabilities as integers x10000 in every columnar
variant. This format keeps them as the 4-decimal floats they already are,
so values are self-explanatory. As implemented, on the same file: 3.02MB,
129ms to load, 1.0ms per 3,000-building tile, 0.84s to encode, and every
one of its 448,545 distinct buildings reads back identical to the
previous format (448,557 rows; 12 ids repeated).

RSS in the table is measured in-process after encoding, which understates
both formats alike. Loaded in a fresh process, as a new tiles Lambda
container does: this format +186MB, the previous one +568MB, more than the
old 512MB function had in total.

Almost all of the gain is from two things any of these formats could do:
storing columns rather than one object per row (no per-building dict with
repeated keys), and looking ids up by binary search in one sorted list
rather than a 448k-entry hash map. The encoding itself matters much less:

- Protobuf loads no faster than JSON here. Converting the ids to Python
  strings dominates both. It would also add a schema and generated code.
- Parquet is the best standard option, but pyarrow adds 157MB unzipped to
  the tiles package (87MB today), leaving ~18MB under Lambda's 262MB zip
  limit. The other way around the limit, a container image, gives up this
  function's ~0.85s zip cold start for the multi-second image-fetch cold
  starts the scenario function sees.
- A bespoke binary layout is ~5x faster to load again (18ms vs 94ms, once
  per container per scenario), not worth a custom format to maintain.

JSON arrays stay readable with any tool and add no dependency, schema or
build step.
"""

from __future__ import annotations

import bisect
import gzip
import io
import json
from collections.abc import Mapping, Sequence
from itertools import pairwise
from typing import Any

FORMAT_NAME = "twiner.scenario-results"
FORMAT_VERSION = 1
FILENAME = f"buildings.columns.v{FORMAT_VERSION}.json.gz"

ID_COLUMN = "building_id"
PROBABILITY_COLUMNS = (
    "prob_none",
    "prob_slight",
    "prob_moderate",
    "prob_extensive",
    "prob_complete",
)
# Everything but the id is a per-building tile property, in this order.
PROPERTY_COLUMNS = ("damage_state_code", *PROBABILITY_COLUMNS)
COLUMNS = (ID_COLUMN, *PROPERTY_COLUMNS)


# Rows per chunk when writing (`encode_sorted_unique`): only one chunk of
# one column is ever held as Python objects at a time.
_CHUNK_ROWS = 100_000


def encode(columns: Mapping[str, Sequence]) -> bytes:
    """The gzipped file for these columns (exactly `COLUMNS`, equal
    lengths, any row order, ids possibly repeated): sorted by id, one row
    per id (the last one given), as the format requires.

    Builds the sorted, deduplicated columns as Python lists first, so memory
    grows with the row count: fine for tests and small scenarios. The
    scenario function sorts and dedupes in Arrow and calls
    `encode_sorted_unique` directly instead."""
    _check_columns(columns)
    ids = columns[ID_COLUMN]
    last_row = {building_id: row for row, building_id in enumerate(ids)}  # keep-last
    order = [last_row[building_id] for building_id in sorted(last_row)]
    return encode_sorted_unique({name: [columns[name][row] for row in order] for name in COLUMNS})


def encode_sorted_unique(columns: Mapping[str, Any]) -> bytes:
    """The same file as `encode`, for columns whose ids are already sorted
    and unique (checked while writing: a ValueError otherwise), written
    column by column in `_CHUNK_ROWS`-row chunks straight into a gzip
    stream, so memory stays bounded by one chunk plus the compressed
    output however many rows there are.

    Each column only needs `len()` and slicing, with a slice that is either
    a list or has `to_pylist()`. So a pyarrow Array/ChunkedArray works as is
    without this module importing pyarrow (see the module docstring on why
    it can't).

    Why (2026-09-26): an M9 "very_low" scenario on Madrid lists 3.4M
    buildings. Converting them to Python objects all at once, as `encode`
    does, took the scenario function from 1.43GB to a 2.6GB peak locally
    and OOM-killed it at 3008MB in prod."""
    _check_columns(columns)
    n_rows = len(columns[ID_COLUMN])
    buffer = io.BytesIO()
    # gzip level 6 (zlib's own default), not gzip.compress's 9: on the M9
    # Madrid "high" file (24MB of JSON), level 9 took 2.72s, inside the
    # scenario request, for a file 2% smaller than level 6's 0.46s (2.97MB
    # vs 3.02MB). Readers decompress either in ~13ms.
    with gzip.GzipFile(fileobj=buffer, mode="wb", compresslevel=6) as out:
        # Same document as json.dumps({"format", "version", "columns": {...}})
        # with compact separators, written a piece at a time.
        out.write(
            f'{{"format":{json.dumps(FORMAT_NAME)},"version":{FORMAT_VERSION},"columns":{{'.encode()
        )
        for column_index, name in enumerate(COLUMNS):
            out.write(f"{json.dumps(name)}:[".encode())
            previous_id = None
            for start in range(0, n_rows, _CHUNK_ROWS):
                chunk = _as_list(columns[name][start : start + _CHUNK_ROWS])
                if name == ID_COLUMN:
                    previous_id = _check_sorted_unique(chunk, previous_id)
                if start:
                    out.write(b",")
                out.write(json.dumps(chunk, separators=(",", ":"))[1:-1].encode("utf-8"))
            out.write(b"]" if column_index == len(COLUMNS) - 1 else b"],")
        out.write(b"}}")
    return buffer.getvalue()


def _check_columns(columns: Mapping[str, Any]) -> None:
    if set(columns) != set(COLUMNS):
        raise ValueError(f"expected columns {COLUMNS}, got {tuple(columns)}")
    lengths = {name: len(values) for name, values in columns.items()}
    if len(set(lengths.values())) != 1:
        raise ValueError(f"columns differ in length: {lengths}")


def _as_list(chunk: Any) -> list:
    return chunk.to_pylist() if hasattr(chunk, "to_pylist") else list(chunk)


def _check_sorted_unique(ids: list, previous: str | None) -> str | None:
    """The last id of this chunk, after checking the chunk continues a
    strictly increasing sequence -- the order `ScenarioResults.get`'s
    binary search relies on."""
    if not ids:
        return previous
    if previous is not None and not previous < ids[0]:
        raise ValueError(f"building_id not sorted/unique: {previous!r} then {ids[0]!r}")
    for a, b in pairwise(ids):
        if not a < b:
            raise ValueError(f"building_id not sorted/unique: {a!r} then {b!r}")
    return ids[-1]


class ScenarioResults:
    """A decoded results file: `.get(building_id)` gives that building's
    tile properties (`PROPERTY_COLUMNS` -> value), or None if it isn't
    listed. Holds the column lists as decoded; no per-building objects."""

    def __init__(self, columns: Mapping[str, list]):
        self._ids: list[str] = columns[ID_COLUMN]
        self._properties = [columns[name] for name in PROPERTY_COLUMNS]

    @classmethod
    def from_bytes(cls, blob: bytes) -> ScenarioResults:
        document = json.loads(gzip.decompress(blob))
        if document.get("format") != FORMAT_NAME or document.get("version") != FORMAT_VERSION:
            raise ValueError(
                f"not a {FORMAT_NAME} v{FORMAT_VERSION} file: "
                f"format={document.get('format')!r}, version={document.get('version')!r}"
            )
        columns = document["columns"]
        if set(columns) != set(COLUMNS):
            raise ValueError(f"expected columns {COLUMNS}, got {tuple(columns)}")
        return cls(columns)

    def __len__(self) -> int:
        return len(self._ids)

    def get(self, building_id: str) -> dict | None:
        row = bisect.bisect_left(self._ids, building_id)
        if row == len(self._ids) or self._ids[row] != building_id:
            return None
        return {
            name: values[row]
            for name, values in zip(PROPERTY_COLUMNS, self._properties, strict=True)
        }
