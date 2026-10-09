"""Child process of a file export: ``python -m app.export_child`` (see :mod:`app.exports`).

Reads one JSON job from stdin and runs :func:`app.cad.generate_files` (or, outside
production, the generator named by the ``EXPORT_FAKE_GENERATOR`` test seam). Protocol: JSON
lines on the original stdout, which is kept for the protocol only; anything the libraries print
goes to stderr (the server log).

* ``{"type": "progress", "progress": 0-1, "stage": "..."}``
* ``{"type": "done", "peak_rss_mb": n, "files": n}``
* ``{"type": "error", "code": "cad" | "envelope" | "internal", "message": "..."}``

The exit code is 0 after ``done`` and 1 after ``error``. ``manifest.json`` in ``out_dir`` holds
the manifest (written by the generator, or here when a generator did not write it).
"""

from __future__ import annotations

import importlib
import json
import os
import resource
import sys
import traceback
from collections.abc import Callable
from pathlib import Path
from typing import Any, TextIO

DEFAULT_GENERATOR = "app.cad:generate_files"


def _generator(spec: str | None) -> Callable[..., dict[str, Any]]:
    module, _, name = (spec or DEFAULT_GENERATOR).partition(":")
    return getattr(importlib.import_module(module), name or "generate_files")


def _peak_rss_mb() -> float:
    return round(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024.0, 1)


def run(job: dict[str, Any], proto: TextIO) -> int:
    def emit(message: dict[str, Any]) -> None:
        proto.write(json.dumps(message, allow_nan=False) + "\n")
        proto.flush()

    def progress(frac: float, stage: str) -> None:
        emit({"type": "progress", "progress": float(frac), "stage": str(stage)[:200]})

    from app.cad.model import CadError, EnvelopeError

    out = Path(job["out_dir"])
    try:
        generate = _generator(job.get("generator"))
        manifest = generate(
            job["parameters"],
            job["mission"],
            job.get("settings"),
            analysis=job.get("analysis"),
            parts_selection=job.get("parts_selection"),
            out_dir=out,
            progress=progress,
            project=job.get("project"),
            mesh_tolerance_mm=float(job.get("mesh_tolerance_mm") or 0.05),
        )
        path = out / "manifest.json"
        if not path.is_file():
            path.write_text(json.dumps(manifest, indent=1), encoding="utf-8")
    except EnvelopeError as exc:
        emit({"type": "error", "code": "envelope", "message": str(exc)})
        return 1
    except CadError as exc:
        emit({"type": "error", "code": "cad", "message": str(exc)})
        return 1
    except Exception as exc:
        traceback.print_exc(file=sys.stderr)
        emit({"type": "error", "code": "internal", "message": f"{type(exc).__name__}: {exc}"})
        return 1
    emit({"type": "done", "peak_rss_mb": _peak_rss_mb(), "files": len(manifest.get("files", []))})
    return 0


def main() -> int:
    # Keep the real stdout for the protocol; route fd 1 (C libraries, print) to stderr.
    proto_fd = os.dup(1)
    os.dup2(2, 1)
    sys.stdout = sys.stderr
    proto = os.fdopen(proto_fd, "w", encoding="utf-8", buffering=1)
    job = json.loads(sys.stdin.buffer.read().decode("utf-8"))
    return run(job, proto)


if __name__ == "__main__":
    sys.exit(main())
