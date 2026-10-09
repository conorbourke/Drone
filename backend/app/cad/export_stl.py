"""Binary STL writer (deterministic: fixed header, little-endian float32, input face order)."""

from __future__ import annotations

import struct
from pathlib import Path

import numpy as np


def write_binary_stl(path: Path, vertices: np.ndarray, faces: np.ndarray, name: str) -> int:
    """Write a binary STL; returns the file size in bytes."""
    v = np.asarray(vertices, dtype=np.float64)
    f = np.asarray(faces, dtype=np.int64)
    tri = v[f]
    n = np.cross(tri[:, 1] - tri[:, 0], tri[:, 2] - tri[:, 0])
    norm = np.linalg.norm(n, axis=1, keepdims=True)
    n = np.divide(n, norm, out=np.zeros_like(n), where=norm > 0)
    rec = np.zeros(
        len(f),
        dtype=np.dtype([("n", "<f4", (3,)), ("v", "<f4", (3, 3)), ("attr", "<u2")]),
    )
    rec["n"] = n.astype("<f4")
    rec["v"] = tri.astype("<f4")
    header = f"VTOL designer piece: {name}".encode("ascii", "replace")[:80].ljust(80, b" ")
    data = header + struct.pack("<I", len(f)) + rec.tobytes()
    path.write_bytes(data)
    return len(data)


def read_binary_stl(path: Path) -> tuple[np.ndarray, np.ndarray]:
    """Triangles (N, 3, 3) and normals (N, 3) of a binary STL (for tests)."""
    data = path.read_bytes()
    (count,) = struct.unpack_from("<I", data, 80)
    rec = np.frombuffer(
        data,
        dtype=np.dtype([("n", "<f4", (3,)), ("v", "<f4", (3, 3)), ("attr", "<u2")]),
        count=count,
        offset=84,
    )
    return rec["v"].astype(np.float64), rec["n"].astype(np.float64)
