"""Streaming helpers for the public BigANN SIFT-style binary vectors."""

from __future__ import annotations

import gzip
import urllib.request
from collections.abc import Iterator
from pathlib import Path

import numpy as np

DIMENSIONS = 128
# fvecs and bvecs both encode the dimension as a little-endian int32. BigANN
# then stores 128 unsigned-byte values, making each record 132 bytes.
HEADER_BYTES = 4
RECORD_BYTES = HEADER_BYTES + DIMENSIONS
BASE_URL = "ftp://ftp.irisa.fr/local/texmex/corpus/bigann_base.bvecs.gz"
QUERY_URL = "ftp://ftp.irisa.fr/local/texmex/corpus/bigann_query.bvecs.gz"


def iter_bvec_batches(
    url: str, count: int, batch_rows: int
) -> Iterator[np.ndarray]:
    """Yield at most ``count`` BigANN vectors as float32 batches.

    The corpus is gzip-compressed.  Reading only the requested prefix permits
    a 10M or 100M benchmark without downloading the full one-billion-vector
    source file or materialising it in RAM.
    """
    remaining = count
    with urllib.request.urlopen(url) as response, gzip.GzipFile(fileobj=response) as stream:
        while remaining:
            rows = min(batch_rows, remaining)
            payload = stream.read(rows * RECORD_BYTES)
            if len(payload) != rows * RECORD_BYTES:
                raise RuntimeError(
                    f"BigANN source ended after {count - remaining:,} vectors; expected {count:,}"
                )
            encoded = np.frombuffer(payload, dtype=np.uint8).reshape(rows, RECORD_BYTES)
            dimensions = np.frombuffer(payload, dtype="<i4").reshape(rows, 33)[:, 0]
            if not np.all(dimensions == DIMENSIONS):
                raise ValueError("BigANN source contains a non-128-dimensional bvec record")
            yield encoded[:, HEADER_BYTES:].astype(np.float32)
            remaining -= rows


def download_queries(destination: Path, url: str = QUERY_URL) -> Path:
    """Decompress the small query set locally and return its bvecs path."""
    destination.parent.mkdir(parents=True, exist_ok=True)
    if not destination.exists():
        with urllib.request.urlopen(url) as response, gzip.GzipFile(fileobj=response) as source:
            with destination.open("wb") as target:
                while chunk := source.read(1024 * 1024):
                    target.write(chunk)
    return destination


def read_bvecs(path: str | Path, limit: int | None = None) -> np.ndarray:
    """Read local fixed-width BigANN bvecs as float32."""
    encoded = np.fromfile(path, dtype=np.uint8)
    if encoded.size == 0 or encoded.size % RECORD_BYTES:
        raise ValueError(f"{path} is not a valid {DIMENSIONS}-dimensional bvecs file")
    rows = encoded.reshape(-1, RECORD_BYTES)
    dimensions = np.fromfile(path, dtype="<i4").reshape(-1, 33)[:, 0]
    if not np.all(dimensions == DIMENSIONS):
        raise ValueError(f"{path} has a vector dimension other than {DIMENSIONS}")
    values = rows[:, HEADER_BYTES:].astype(np.float32)
    return values[:limit] if limit else values
