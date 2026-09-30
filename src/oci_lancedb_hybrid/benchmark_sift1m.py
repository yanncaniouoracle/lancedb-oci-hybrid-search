"""Drive the search-service HTTP API with standard SIFT1M query vectors."""

from __future__ import annotations

import argparse
import concurrent.futures
import json
import statistics
import time
import urllib.request
from pathlib import Path

from .sift1m import read_fvecs


def one_query(endpoint: str, vector: list[float], top_k: int) -> tuple[float, int]:
    started = time.perf_counter()
    request = urllib.request.Request(
        f"{endpoint.rstrip('/')}/v1/search",
        data=json.dumps({"vector": vector, "top_k": top_k}).encode(),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=60) as response:
        payload = json.load(response)
    return time.perf_counter() - started, int(payload["count"])


def percentile(samples: list[float], value: float) -> float:
    samples = sorted(samples)
    return samples[max(0, min(len(samples) - 1, round((len(samples) - 1) * value)))]


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--endpoint", required=True)
    parser.add_argument("--queries", default="/mnt/lancedb-hot/datasets/sift1m/sift_query.fvecs")
    parser.add_argument("--count", type=int, default=1_000)
    parser.add_argument("--concurrency", type=int, default=16)
    parser.add_argument("--top-k", type=int, default=10)
    args = parser.parse_args()
    vectors = read_fvecs(Path(args.queries), limit=args.count)
    if not len(vectors):
        raise RuntimeError("No query vectors found")
    wall_start = time.perf_counter()
    with concurrent.futures.ThreadPoolExecutor(max_workers=args.concurrency) as executor:
        results = list(executor.map(lambda vector: one_query(args.endpoint, vector.tolist(), args.top_k), vectors))
    wall_seconds = time.perf_counter() - wall_start
    latencies = [elapsed for elapsed, returned in results]
    if any(returned != args.top_k for _, returned in results):
        raise RuntimeError("At least one response did not return the requested top-k")
    print(json.dumps({
        "endpoint": args.endpoint,
        "queries": len(results),
        "concurrency": args.concurrency,
        "top_k": args.top_k,
        "qps": round(len(results) / wall_seconds, 2),
        "latency_ms": {
            "mean": round(statistics.mean(latencies) * 1000, 2),
            "p50": round(percentile(latencies, 0.50) * 1000, 2),
            "p95": round(percentile(latencies, 0.95) * 1000, 2),
            "p99": round(percentile(latencies, 0.99) * 1000, 2),
        },
    }, indent=2))
