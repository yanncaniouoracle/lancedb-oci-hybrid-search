"""Create a batched 10M or 100M BigANN/SIFT-style LanceDB table."""

from __future__ import annotations

import argparse
from pathlib import Path

import lancedb
import numpy as np
import pyarrow as pa

from .bigann import BASE_URL, DIMENSIONS, QUERY_URL, download_queries, iter_bvec_batches


def arrow_batch(vectors: np.ndarray, start_id: int, source_uri: str) -> pa.Table:
    values = pa.FixedSizeListArray.from_arrays(
        pa.array(vectors.reshape(-1), type=pa.float32()), DIMENSIONS
    )
    return pa.table({
        "id": pa.array(np.arange(start_id, start_id + len(vectors), dtype=np.int64)),
        "source_uri": pa.array([source_uri] * len(vectors)),
        "vector": values,
    })


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--vectors", type=int, choices=(10_000_000, 100_000_000), required=True)
    parser.add_argument("--db-uri", required=True)
    parser.add_argument("--table", required=True)
    parser.add_argument("--dataset-dir", required=True)
    parser.add_argument("--base-url", default=BASE_URL)
    parser.add_argument("--query-url", default=QUERY_URL)
    parser.add_argument("--batch-rows", type=int, default=100_000)
    parser.add_argument("--index-type", default="IVF_PQ")
    parser.add_argument("--source-uri", default="bigann://base.bvecs.gz")
    args = parser.parse_args()

    database = lancedb.connect(args.db_uri)
    if args.table in database.table_names():
        raise RuntimeError(f"Table {args.table!r} already exists; refusing to overwrite it")

    dataset_dir = Path(args.dataset_dir)
    query_path = download_queries(dataset_dir / "bigann_query.bvecs", args.query_url)
    print(f"Saved BigANN queries to {query_path}")

    table = None
    written = 0
    for vectors in iter_bvec_batches(args.base_url, args.vectors, args.batch_rows):
        batch = arrow_batch(vectors, written, args.source_uri)
        if table is None:
            table = database.create_table(args.table, batch)
        else:
            table.add(batch)
        written += len(vectors)
        print(f"Loaded {written:,}/{args.vectors:,} vectors", flush=True)

    if table is None or written != args.vectors:
        raise RuntimeError("No BigANN vectors were loaded")
    table.create_index(metric="l2", vector_column_name="vector", index_type=args.index_type)
    print(f"Created and indexed {args.table!r}: {table.count_rows():,} vectors")
