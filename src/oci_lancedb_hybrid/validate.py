"""GPU-side end-to-end validation for the hybrid retrieval path."""

from __future__ import annotations

import io
import json
import os
import time
import urllib.request

import numpy as np

from .object_store import reader_from_environment


def read_object(reader, uri: str) -> tuple[bytes, float]:
    started = time.perf_counter()
    payload = reader.get(uri)
    return payload, (time.perf_counter() - started) * 1000


def main() -> None:
    reader = reader_from_environment()
    archive, bootstrap_ms = read_object(reader, "s3://lancedb-cold/datasets/mnist/mnist.npz")
    with np.load(io.BytesIO(archive)) as mnist:
        vector = (mnist["x_train"][0].astype(np.float32).reshape(-1) / 255.0).tolist()

    payload = json.dumps({"vector": vector, "top_k": 3}).encode()
    request = urllib.request.Request(
        f"{os.environ.get('LANCEDB_SEARCH_URL', 'http://10.0.0.49:8080').rstrip('/')}/v1/search",
        data=payload,
        headers={"Content-Type": "application/json"},
    )
    started = time.perf_counter()
    with urllib.request.urlopen(request, timeout=30) as response:
        result = json.loads(response.read())
    search_ms = (time.perf_counter() - started) * 1000

    selected, fetch_ms = read_object(reader, result["results"][0]["source_uri"])
    print(json.dumps({
        "bootstrap_archive_ms": round(bootstrap_ms, 2),
        "search_ms": round(search_ms, 2),
        "top_result": result["results"][0],
        "selected_payload_bytes": len(selected),
        "selected_payload_fetch_ms": round(fetch_ms, 2),
    }, indent=2))
