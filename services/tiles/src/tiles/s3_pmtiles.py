"""An S3-backed byte source for `pmtiles.reader.Reader`, mirroring the
`MmapSource` the pmtiles package ships for local files (see
services/scenario/tile_join.py's own use of it). Range-GETs the requested
byte span from S3 instead of mmap'ing a local file -- buildings.pmtiles
lives in the data bucket in the cloud, never on this Lambda's local disk
(and at multi-GB, downloading the whole archive per cold start would
defeat the point of PMTiles' range-addressable design in the first place).

`boto3` isn't a project dependency on purpose -- see results_store.py's
own comment.
"""

from __future__ import annotations

import boto3  # pyrefly: ignore -- Lambda-runtime-provided, unresolvable for local type checking


def s3_source(bucket: str, key: str):
    """Returns a `get_bytes(offset, length)` callable, the shape
    `pmtiles.reader.Reader.__init__` expects."""
    s3 = boto3.client("s3")

    def get_bytes(offset: int, length: int) -> bytes:
        range_header = f"bytes={offset}-{offset + length - 1}"
        resp = s3.get_object(Bucket=bucket, Key=key, Range=range_header)
        return resp["Body"].read()

    return get_bytes
