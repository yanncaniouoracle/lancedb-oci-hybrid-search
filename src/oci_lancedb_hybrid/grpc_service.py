"""Typed gRPC boundary for hot-tier LanceDB vector searches.

The HTTP API remains available for compatibility.  This server uses a
long-lived HTTP/2 gRPC channel, avoiding JSON vector conversion and allowing
GPU clients to reuse one connection to the private load balancer.
"""

from __future__ import annotations

import os
from concurrent import futures

import grpc
import lancedb

from . import lancedb_search_pb2, lancedb_search_pb2_grpc

DB_URI = os.environ.get("LANCEDB_URI", "/mnt/lancedb-hot/lancedb-hot")
TABLE_NAME = os.environ.get("LANCEDB_TABLE", "mnist_image_vectors")
VECTOR_DIMENSIONS = int(os.environ.get("LANCEDB_VECTOR_DIMENSIONS", "784"))
GRPC_BIND_HOST = os.environ.get("LANCEDB_GRPC_BIND_HOST", "0.0.0.0")
GRPC_PORT = int(os.environ.get("LANCEDB_GRPC_PORT", "50051"))
GRPC_MAX_WORKERS = int(os.environ.get("LANCEDB_GRPC_MAX_WORKERS", "64"))


class LanceSearchServicer(lancedb_search_pb2_grpc.LanceSearchServicer):
    """Searches the local Block-Volume table on behalf of gRPC clients."""

    def __init__(self) -> None:
        self.database = lancedb.connect(DB_URI)
        if TABLE_NAME not in self.database.table_names():
            raise RuntimeError(f"Table {TABLE_NAME!r} not found at {DB_URI}")

    def Search(self, request, context):  # noqa: N802 - generated gRPC contract
        if len(request.vector) != VECTOR_DIMENSIONS:
            context.abort(
                grpc.StatusCode.INVALID_ARGUMENT,
                f"vector must contain exactly {VECTOR_DIMENSIONS} values",
            )
        if request.top_k < 1 or request.top_k > 100:
            context.abort(grpc.StatusCode.INVALID_ARGUMENT, "top_k must be 1..100")

        # Reopen deliberately: event-driven ingestion commits local Lance
        # versions, and this preserves the HTTP service's immediate manifest
        # visibility semantics. A revision-aware cache can be introduced later.
        try:
            table = self.database.open_table(TABLE_NAME)
            query = table.search(list(request.vector)).limit(request.top_k)
            if request.where:
                query = query.where(request.where)
            rows = query.select(["id", "source_uri"]).to_list()
        except Exception as exc:
            context.abort(grpc.StatusCode.UNAVAILABLE, f"LanceDB search unavailable: {exc}")

        results = [
            lancedb_search_pb2.SearchResult(
                id=int(row["id"]), source_uri=str(row.get("source_uri", ""))
            )
            for row in rows
        ]
        return lancedb_search_pb2.SearchResponse(results=results, count=len(results))


def run() -> None:
    if GRPC_MAX_WORKERS < 1:
        raise RuntimeError("LANCEDB_GRPC_MAX_WORKERS must be at least 1")
    server = grpc.server(futures.ThreadPoolExecutor(max_workers=GRPC_MAX_WORKERS))
    lancedb_search_pb2_grpc.add_LanceSearchServicer_to_server(
        LanceSearchServicer(), server
    )
    server.add_insecure_port(f"{GRPC_BIND_HOST}:{GRPC_PORT}")
    server.start()
    server.wait_for_termination()
