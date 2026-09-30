"""MNIST feasibility ingestion: cold PNG objects and a local hot LanceDB table."""

from __future__ import annotations

import argparse
import io
import os
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import lancedb
import numpy as np
import pyarrow as pa
from PIL import Image

from .object_store import s3_client_from_environment

MNIST_URL = "https://storage.googleapis.com/tensorflow/tf-keras-datasets/mnist.npz"
DIMENSIONS = 784


def png_bytes(image: np.ndarray) -> bytes:
    buffer = io.BytesIO()
    Image.fromarray(image, mode="L").save(buffer, format="PNG", optimize=True)
    return buffer.getvalue()


def table_data(images: np.ndarray, labels: np.ndarray, bucket: str, prefix: str) -> pa.Table:
    values = pa.array(images.reshape(-1).astype(np.float32) / 255.0, type=pa.float32())
    vectors = pa.FixedSizeListArray.from_arrays(values, DIMENSIONS)
    return pa.table({
        "id": pa.array(np.arange(len(images), dtype=np.int64)),
        "label": pa.array(labels.astype(np.int8)),
        "source_uri": pa.array([f"s3://{bucket}/{prefix}{i:05d}.png" for i in range(len(images))]),
        "vector": vectors,
    })


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", default="/mnt/lancedb-hot/datasets/mnist.npz")
    parser.add_argument("--db-uri", default="/mnt/lancedb-hot/lancedb-hot")
    parser.add_argument("--table", default="mnist_image_vectors")
    parser.add_argument("--bucket", default=os.environ.get("OCI_BUCKET", "lancedb-cold"))
    parser.add_argument("--prefix", default="datasets/mnist/train/")
    parser.add_argument("--workers", type=int, default=16)
    args = parser.parse_args()

    source = Path(args.source)
    source.parent.mkdir(parents=True, exist_ok=True)
    if not source.exists():
        urllib.request.urlretrieve(MNIST_URL, source)
    with np.load(source) as dataset:
        images, labels = dataset["x_train"], dataset["y_train"]

    client = s3_client_from_environment()  # Ingestion needs an explicitly authorized writer.
    present: set[str] = set()
    for page in client.get_paginator("list_objects_v2").paginate(Bucket=args.bucket, Prefix=args.prefix):
        present.update(item["Key"] for item in page.get("Contents", []))
    pending = [(i, image) for i, image in enumerate(images) if f"{args.prefix}{i:05d}.png" not in present]
    print(f"Cold objects: {len(present)} present; {len(pending)} to upload")

    def upload(item: tuple[int, np.ndarray]) -> None:
        index, image = item
        client.put_object(
            Bucket=args.bucket,
            Key=f"{args.prefix}{index:05d}.png",
            Body=png_bytes(image),
            ContentType="image/png",
            Metadata={"dataset": "mnist", "image-id": str(index)},
        )

    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        futures = [pool.submit(upload, item) for item in pending]
        for completed, future in enumerate(as_completed(futures), start=1):
            future.result()
            if completed % 1000 == 0 or completed == len(pending):
                print(f"Uploaded {completed}/{len(pending)} PNG objects")

    database = lancedb.connect(args.db_uri)
    if args.table in database.table_names():
        print(f"Reusing existing table {args.table!r}")
        return
    table = database.create_table(args.table, table_data(images, labels, args.bucket, args.prefix))
    table.create_index(metric="l2", vector_column_name="vector", index_type="IVF_PQ")
    print(f"Created and indexed {args.table!r}: {table.count_rows()} rows")
