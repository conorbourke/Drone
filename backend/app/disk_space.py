"""Free-space guard for the data volume (3 GB on the server).

The database, its daily backups, uploaded images, flight logs (up to 200 MB each) and the
generated file and mould sets all share one volume. Nothing else limits how much of it they
use, and a full volume breaks every database write, so the routes that add large files
check that enough room is left first and answer 507 with a plain message otherwise.
"""

from __future__ import annotations

import shutil
from pathlib import Path

from fastapi import HTTPException, status

#: Kept free for the database, its backups and the files being written.
RESERVE_BYTES = 300 * 1024 * 1024
#: A file or mould set is not measured until it has been made; this covers the largest.
JOB_OUTPUT_BYTES = 300 * 1024 * 1024


def free_bytes(path: Path) -> int | None:
    """Free bytes on the file system holding ``path`` (or its nearest existing parent);
    None when it cannot be measured."""
    probe = path
    while not probe.exists() and probe != probe.parent:
        probe = probe.parent
    try:
        return int(shutil.disk_usage(probe).free)
    except OSError:
        return None


def ensure_free_space(path: Path, needed_bytes: int, what: str) -> None:
    """Raise 507 when storing ``needed_bytes`` more under ``path`` would leave less than
    :data:`RESERVE_BYTES` free. ``what`` completes "There is not enough free space to ..."."""
    free = free_bytes(path)
    if free is None or free - max(0, needed_bytes) >= RESERVE_BYTES:
        return
    raise HTTPException(
        status.HTTP_507_INSUFFICIENT_STORAGE,
        detail=f"There is not enough free space on the server to {what} "
        f"({max(0, free) // (1024 * 1024)} MB free). Delete old flight logs, file sets or "
        "mould sets you no longer need, then try again.",
    )
