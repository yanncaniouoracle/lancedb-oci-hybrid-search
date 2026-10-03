"""Benchmark SIFT1M searches through the typed gRPC API using one channel."""

from __future__ import annotations

import argparse
import concurrent.futures
import json
import statistics
import struct
import time

import grpc

from . import lancedb_search_pb2, lancedb_search_pb2_grpc


def read_fvecs(path: str, count: int) -> list[list[float]]:
    vectors = []
    with open(path, "rb") as stream:
        for _ in range(count):
            raw_dimension = stream.read(4)
            if not raw_dimension:
                break
            dimension = struct.unpack("<i", raw_dimension)[0]
            vectors.append(list(struct.unpack(f"<{dimension}f", stream.read(dimension * 4))))
    return vectors


def percentile(samples: list[float], fraction: float) -> float:
    ordered = sorted(samples)
    return ordered[round((len(ordered) - 1) * fraction)]


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--endpoint", required=True, help="host:port, without a scheme")
    parser.add_argument("--queries", required=True)
    parser.add_argument("--count", type=int, default=1000)
    parser.add_argument("--concurrency", type=int, default=16)
    parser.add_argument(
        "--channels",
        type=int,
        default=1,
        help="Persistent gRPC channels; use at least the number of TCP-LB backends.",
    )
    parser.add_argument("--top-k", type=int, default=10)
    args = parser.parse_args()

    vectors = read_fvecs(args.queries, args.count)
    if len(vectors) != args.count:
        raise SystemExit(f"Expected {args.count} query vectors, found {len(vectors)}")
    if args.channels < 1:
        raise SystemExit("--channels must be at least one")

    # A channel is thread-safe. However, the feasibility deployment uses an
    # L4 TCP load balancer, which selects a backend per TCP flow. Maintain a
    # small persistent channel pool (normally one channel per backend) so a
    # single GPU/application VM can reach every replica without reconnecting
    # per request.
    channels = [grpc.insecure_channel(args.endpoint) for _ in range(args.channels)]
    stubs = [lancedb_search_pb2_grpc.LanceSearchStub(channel) for channel in channels]

    def execute(index_and_vector: tuple[int, list[float]]) -> None:
        index, vector = index_and_vector
        response = stubs[index % len(stubs)].Search(
            lancedb_search_pb2.SearchRequest(vector=vector, top_k=args.top_k),
            timeout=120,
        )
        if response.count != args.top_k:
            raise RuntimeError(f"Expected {args.top_k} results, received {response.count}")

    try:
        with concurrent.futures.ThreadPoolExecutor(max_workers=args.concurrency) as pool:
            list(pool.map(execute, enumerate([vectors[0]] * args.concurrency)))

            def timed(index_and_vector: tuple[int, list[float]]) -> float:
                started = time.perf_counter()
                execute(index_and_vector)
                return (time.perf_counter() - started) * 1000

            wall_start = time.perf_counter()
            latencies = list(pool.map(timed, enumerate(vectors)))
            wall_seconds = time.perf_counter() - wall_start
    finally:
        for channel in channels:
            channel.close()

    print(json.dumps({
        "queries": len(latencies),
        "concurrency": args.concurrency,
        "channels": args.channels,
        "top_k": args.top_k,
        "qps": round(len(latencies) / wall_seconds, 2),
        "latency_ms": {
            "mean": round(statistics.mean(latencies), 2),
            "p50": round(percentile(latencies, 0.50), 2),
            "p95": round(percentile(latencies, 0.95), 2),
            "p99": round(percentile(latencies, 0.99), 2),
        },
    }, indent=2))


if __name__ == "__main__":
    main()
