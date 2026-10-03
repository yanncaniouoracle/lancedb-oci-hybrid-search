"""HTTP boundary that keeps LanceDB reads on the search tier's Block Volume."""

from __future__ import annotations

import os
from contextlib import asynccontextmanager

import lancedb
import uvicorn
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field

DB_URI = os.environ.get("LANCEDB_URI", "/mnt/lancedb-hot/lancedb-hot")
TABLE_NAME = os.environ.get("LANCEDB_TABLE", "mnist_image_vectors")
VECTOR_DIMENSIONS = int(os.environ.get("LANCEDB_VECTOR_DIMENSIONS", "784"))
WORKERS = int(os.environ.get("LANCEDB_WORKERS", "1"))


class SearchRequest(BaseModel):
    vector: list[float] = Field(min_length=VECTOR_DIMENSIONS, max_length=VECTOR_DIMENSIONS)
    top_k: int = Field(default=10, ge=1, le=100)
    where: str | None = Field(default=None, max_length=500)


@asynccontextmanager
async def lifespan(app: FastAPI):
    database = lancedb.connect(DB_URI)
    if TABLE_NAME not in database.table_names():
        raise RuntimeError(f"Table {TABLE_NAME!r} not found at {DB_URI}")
    app.state.database = database
    yield


app = FastAPI(title="OCI LanceDB Hybrid Search", version="0.1.0", lifespan=lifespan)


@app.get("/healthz")
def healthz() -> dict[str, str]:
    return {"status": "ok", "table": TABLE_NAME, "db_uri": DB_URI}


@app.post("/v1/search")
def search(request: SearchRequest) -> dict[str, object]:
    # Workers commit new local LanceDB versions. Reopen per request so a serving
    # process sees the latest manifest without a restart.
    table = app.state.database.open_table(TABLE_NAME)
    query = table.search(request.vector).limit(request.top_k)
    if request.where:
        query = query.where(request.where)
    try:
        rows = query.select(["id", "source_uri"]).to_list()
    except Exception as exc:
        raise HTTPException(status_code=503, detail="LanceDB search unavailable") from exc
    return {"results": rows, "count": len(rows)}


def run() -> None:
    if WORKERS < 1:
        raise RuntimeError("LANCEDB_WORKERS must be at least 1")
    uvicorn.run(
        "oci_lancedb_hybrid.service:app",
        host=os.environ.get("LANCEDB_BIND_HOST", "127.0.0.1"),
        port=int(os.environ.get("LANCEDB_PORT", "8080")),
        workers=WORKERS,
    )
