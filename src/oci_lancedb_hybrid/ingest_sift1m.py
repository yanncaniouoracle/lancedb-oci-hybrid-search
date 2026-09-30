"""Ingest the public SIFT1M ANN vectors into a local LanceDB hot tier.

SIFT1M is a vector-search benchmark.  It does not include customer media, so
source_uri deliberately points to the benchmark base-vector object rather than
claiming that one Object Storage object exists for every vector.
"""

from __future__ import annotations

import argparse
import tarfile
import urllib.request
from pathlib import Path

import lancedb
import numpy as np
import pyarrow as pa

from .sift1m import DIMENSIONS, read_fvecs

# The maintainer currently publishes the corpus through FTP, not the old HTTP
# URL.  The value remains configurable with --dataset-url for mirrored copies.
SIFT1M_ARCHIVE_URL = "ftp://ftp.irisa.fr/local/texmex/corpus/sift.tar.gz"
REQUIRED_FILES = ("sift_base.fvecs", "sift_query.fvecs")


def ensure_dataset(dataset_dir: Path, url: str) -> None:
    if all((dataset_dir / name).exists() for name in REQUIRED_FILES):
        return
    dataset_dir.mkdir(parents=True, exist_ok=True)
    archive = dataset_dir / "sift.tar.gz"
    if not archive.exists():
        print(f"Downloading SIFT1M archive from {url}")
        urllib.request.urlretrieve(url, archive)
    with tarfile.open(archive, "r:gz") as contents:
        wanted = [member for member in contents.getmembers() if Path(member.name).name in REQUIRED_FILES]
        if len(wanted) != len(REQUIRED_FILES):
            raise RuntimeError("SIFT1M archive does not contain the expected .fvecs files")
        for member in wanted:
            member.name = Path(member.name).name
            # Only the two explicitly allow-listed basenames above are extracted.
            contents.extract(member, dataset_dir)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dataset-dir", default="/mnt/lancedb-hot/datasets/sift1m")
    parser.add_argument("--dataset-url", default=SIFT1M_ARCHIVE_URL)
    parser.add_argument("--db-uri", default="/mnt/lancedb-hot/lancedb-hot")
    parser.add_argument("--table", default="sift1m_vectors")
    parser.add_argument("--bucket", required=True)
    parser.add_argument("--prefix", default="benchmarks/sift1m/")
    parser.add_argument("--index-type", default="IVF_PQ")
    args = parser.parse_args()

    dataset_dir = Path(args.dataset_dir)
    ensure_dataset(dataset_dir, args.dataset_url)
    vectors = read_fvecs(dataset_dir / "sift_base.fvecs")
    if len(vectors) != 1_000_000:
        raise RuntimeError(f"Expected 1,000,000 SIFT base vectors, found {len(vectors):,}")

    database = lancedb.connect(args.db_uri)
    if args.table in database.table_names():
        print(f"Reusing existing table {args.table!r}")
        return
    arrow_vectors = pa.FixedSizeListArray.from_arrays(
        pa.array(vectors.reshape(-1), type=pa.float32()), DIMENSIONS
    )
    source = f"s3://{args.bucket}/{args.prefix}sift_base.fvecs"
    records = pa.table({
        "id": pa.array(np.arange(len(vectors), dtype=np.int64)),
        "source_uri": pa.array([source] * len(vectors)),
        "vector": arrow_vectors,
    })
    table = database.create_table(args.table, records)
    table.create_index(metric="l2", vector_column_name="vector", index_type=args.index_type)
    print(f"Created and indexed {args.table!r}: {table.count_rows():,} vectors")
