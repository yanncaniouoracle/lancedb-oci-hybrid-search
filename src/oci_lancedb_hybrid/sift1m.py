"""Small, reproducible helpers for the public ANN SIFT1M benchmark."""

from __future__ import annotations

from pathlib import Path

import numpy as np


DIMENSIONS = 128


def read_fvecs(path: str | Path, limit: int | None = None) -> np.ndarray:
    """Read TexMex .fvecs into a float32 array, validating its fixed width."""
    raw = np.fromfile(path, dtype=np.int32)
    if raw.size == 0 or raw.size % (DIMENSIONS + 1):
        raise ValueError(f"{path} is not a valid {DIMENSIONS}-dimensional .fvecs file")
    vectors = raw.reshape(-1, DIMENSIONS + 1)
    if not np.all(vectors[:, 0] == DIMENSIONS):
        raise ValueError(f"{path} has a vector dimension other than {DIMENSIONS}")
    values = vectors[:, 1:].view(np.float32)
    return values[:limit] if limit else values
