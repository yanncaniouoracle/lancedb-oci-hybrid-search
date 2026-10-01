"""Private per-node worker that applies Object Storage deltas to local LanceDB."""

from __future__ import annotations

import hashlib
import math
import os
from contextlib import asynccontextmanager
from typing import Any

import lancedb
import uvicorn
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field

from .ingestion_events import ObjectEvent
from .object_store import reader_from_environment

DB_URI = os.environ.get("LANCEDB_URI", "/mnt/lancedb-hot/lancedb-hot")
VECTOR_DIMENSIONS = int(os.environ.get("LANCEDB_VECTOR_DIMENSIONS", "768"))


class IngestionRequest(BaseModel):
    event_id: str = Field(min_length=1)
    operation: str = Field(pattern="^(upsert|delete)$")
    bucket: str = Field(min_length=1)
    object_key: str = Field(min_length=1)
    object_version: str | None = None
    event_time: str | None = None
    target_table: str = Field(min_length=1)


def deterministic_test_embedding(payload: bytes, dimensions: int) -> list[float]:
    """Stable test-only embedding; production must replace this provider."""
    digest = hashlib.shake_256(payload).digest(dimensions * 4)
    values = [int.from_bytes(digest[i:i + 4], "little") / 2**32 for i in range(0, len(digest), 4)]
    norm = math.sqrt(sum(value * value for value in values)) or 1.0
    return [value / norm for value in values]


def sql_string(value: str) -> str:
    return value.replace("'", "''")


def apply_delta(database: lancedb.DBConnection, request: IngestionRequest) -> dict[str, str]:
    event = ObjectEvent(
        event_id=request.event_id,
        operation=request.operation,
        bucket=request.bucket,
        object_key=request.object_key,
        object_version=request.object_version,
        event_time=request.event_time,
    )
    if request.target_table not in database.table_names():
        if event.operation == "delete":
            return {"status": "noop", "reason": "table_not_found"}
        payload = reader_from_environment().get(event.source_uri)
        database.create_table(request.target_table, data=[{
            "id": event.asset_id,
            "source_uri": event.source_uri,
            "object_version": event.object_version or "",
            "event_time": event.event_time or "",
            "vector": deterministic_test_embedding(payload, VECTOR_DIMENSIONS),
        }])
        return {"status": "created"}

    table = database.open_table(request.target_table)
    if event.operation == "delete":
        table.delete(f"id = '{sql_string(event.asset_id)}'")
        return {"status": "deleted"}

    payload = reader_from_environment().get(event.source_uri)
    row = {
        "id": event.asset_id,
        "source_uri": event.source_uri,
        "object_version": event.object_version or "",
        "event_time": event.event_time or "",
        "vector": deterministic_test_embedding(payload, VECTOR_DIMENSIONS),
    }
    try:
        table.merge_insert("id").when_matched_update_all().when_not_matched_insert_all().execute([row])
    except Exception as exc:
        raise RuntimeError(
            "Target table schema must include id, source_uri, object_version, event_time, and vector. "
            "Replace the deterministic test embedding provider before production use."
        ) from exc
    return {"status": "upserted"}


@asynccontextmanager
async def lifespan(app: FastAPI):
    app.state.database = lancedb.connect(DB_URI)
    yield


app = FastAPI(title="OCI LanceDB ingestion worker", version="0.1.0", lifespan=lifespan)


@app.get("/healthz")
def healthz() -> dict[str, str]:
    return {"status": "ok", "db_uri": DB_URI}


@app.post("/v1/ingest")
def ingest(request: IngestionRequest) -> dict[str, str]:
    try:
        return apply_delta(app.state.database, request)
    except Exception as exc:
        raise HTTPException(status_code=503, detail="ingestion failed") from exc


def run() -> None:
    uvicorn.run(
        "oci_lancedb_hybrid.ingestion_worker:app",
        host=os.environ.get("LANCEDB_INGESTION_BIND_HOST", "127.0.0.1"),
        port=int(os.environ.get("LANCEDB_INGESTION_WORKER_PORT", "8090")),
    )
