"""The validation report (docs/phases/PHASE3.md section 5): read it, and re-run the suite on
the analysis worker."""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import JSONResponse

from app.deps import AppSettings, CurrentUser, current_user
from app.jobs import AnalysisWorker, validation_report_path
from app.schemas.analysis import ValidationOut

log = logging.getLogger("app.validation")

router = APIRouter(
    prefix="/api/validation", tags=["validation"], dependencies=[Depends(current_user)]
)

#: The committed snapshot (written by a test run, docs/validation/latest.json). Served until
#: this server has produced its own report; present in a source checkout, not in the image.
SNAPSHOT_PATH = Path(__file__).resolve().parents[3] / "docs" / "validation" / "latest.json"


def _read(path: Path) -> dict[str, Any] | None:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def _job(request: Request) -> dict[str, Any]:
    worker: AnalysisWorker | None = getattr(request.app.state, "analysis_worker", None)
    if worker is None:
        return {"status": "idle"}
    return worker.validation.snapshot()


def _body(request: Request, settings: AppSettings) -> ValidationOut:
    report = _read(validation_report_path(settings))
    source: Any = "app" if report is not None else None
    if report is None:
        report = _read(SNAPSHOT_PATH)
        source = "snapshot" if report is not None else None
    return ValidationOut.model_validate({"report": report, "source": source, "job": _job(request)})


@router.get("", response_model=ValidationOut)
def get_validation(request: Request, settings: AppSettings) -> ValidationOut:
    """``{report, source, job}``: the latest report (``source: "app"``, written by this server;
    or ``"snapshot"``, the committed one, until the first run here finishes; ``report: null``
    when neither exists) and the state of the current or last run."""
    return _body(request, settings)


@router.post("/run", status_code=status.HTTP_202_ACCEPTED, response_model=ValidationOut)
def run_validation(request: Request, settings: AppSettings, user: CurrentUser) -> Any:
    """Queue a run on the analysis worker (owner only). A run already queued or running is
    returned as is."""
    if not user.is_owner:
        raise HTTPException(status.HTTP_403_FORBIDDEN, detail="Only the owner can do that.")
    worker: AnalysisWorker | None = getattr(request.app.state, "analysis_worker", None)
    if worker is None or not worker.running:
        return JSONResponse(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            content={"detail": "The analysis engine is starting up. Try again in a moment."},
        )
    try:
        worker.submit_validation("owner")
    except RuntimeError:
        return JSONResponse(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            content={"detail": "The server is restarting. Try again in a moment."},
        )
    return _body(request, settings)
