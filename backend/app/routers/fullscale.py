"""Full-scale checks (Phase 7): the Phase 3 checks plus the 24 kg checks (MTOW thresholds,
motor-out hover, spar and boom tubes, landing gear, battery current, composite layup) of the
draft or a saved version, from :func:`app.engine.fullscale.run_fullscale_checks`.

Synchronous: the checks take about a second on top of an analysis. The latest finished, valid
full analysis of the same source made from the same parameters, mission and settings is
reused (so its Phase 4 parts and Phase 6 calibration carry through); without one a quick
generic-parts analysis runs here. The tube candidates come from the parts catalogue in the
database. Results are kept in a small in-memory cache keyed by everything that changes them;
one computation runs at a time so a burst of requests never runs several engines at once on
the single CPU.
"""

from __future__ import annotations

import hashlib
import threading
from collections import OrderedDict
from typing import Any

from fastapi import APIRouter, Depends, Request
from sqlalchemy import select
from sqlalchemy.orm import Session

from app import parts_service
from app.db import utcnow
from app.deps import AppSettings, CurrentUser, DbSession, current_user
from app.engine.analysis import ENGINE_VERSION
from app.engine.fullscale import SCHEMA, run_fullscale_checks
from app.jobs import canonical_json, polar_cache_dir
from app.models import Analysis, DesignVersion, Project
from app.routers.analyses import resolve_source
from app.routers.common import owned_project
from app.routers.settings import effective_settings
from app.schemas.moulds import FullscaleCreate, FullscaleOut

router = APIRouter(prefix="/api", tags=["fullscale"], dependencies=[Depends(current_user)])

_CACHE_SIZE = 16
_cache: OrderedDict[str, dict[str, Any]] = OrderedDict()
_cache_lock = threading.Lock()
_run_lock = threading.Lock()


def matching_full_analysis(
    db: Session,
    project: Project,
    version: DesignVersion | None,
    parameters: dict[str, Any],
    mission: dict[str, Any],
    settings_doc: dict[str, Any],
) -> Analysis | None:
    """The latest finished, valid full analysis of the same source made from the same
    parameters, mission and settings (thresholds change the checks), or None."""
    q = (
        select(Analysis)
        .where(
            Analysis.project_id == project.id,
            Analysis.kind == "full",
            Analysis.status == "done",
            Analysis.result.is_not(None),
            (
                Analysis.version_id.is_(None)
                if version is None
                else Analysis.version_id == version.id
            ),
        )
        .order_by(Analysis.id.desc())
        .limit(20)
    )
    want = canonical_json([parameters, mission, settings_doc])
    for row in db.scalars(q):
        inputs = row.inputs or {}
        got = canonical_json(
            [inputs.get("parameters"), inputs.get("mission"), inputs.get("settings")]
        )
        if got != want:
            continue
        result = row.result or {}
        if not result.get("valid") or "inputs" not in result:
            continue
        return row
    return None


@router.post("/projects/{project_id}/fullscale", response_model=FullscaleOut)
def run_checks(
    project_id: int,
    body: FullscaleCreate,
    request: Request,
    db: DbSession,
    user: CurrentUser,
    settings: AppSettings,
) -> dict[str, Any]:
    project = owned_project(db, user, project_id)
    version, parameters, mission = resolve_source(db, project, body.source)
    settings_doc, meta = effective_settings(db, user.id)
    row = matching_full_analysis(db, project, version, parameters, mission, settings_doc)
    catalogue = parts_service.load_catalogue(db)
    key = hashlib.sha256(
        canonical_json(
            {
                "parameters": parameters,
                "mission": mission,
                "settings": settings_doc,
                "analysis_id": row.id if row else None,
                "catalogue": catalogue,
                "engine": ENGINE_VERSION,
                "schema": SCHEMA,
            }
        ).encode("ascii")
    ).hexdigest()
    with _cache_lock:
        result = _cache.get(key)
        if result is not None:
            _cache.move_to_end(key)
    if result is None:
        with _run_lock:
            result = run_fullscale_checks(
                parameters,
                mission,
                settings_doc,
                analysis=row.result if row else None,
                catalogue=catalogue,
                cache_dir=str(polar_cache_dir(settings)),
                mode="fast",
                settings_meta=meta,
            )
        # The analysis is the client's own (Design tab); only its id travels with the checks.
        result = {k: v for k, v in result.items() if k != "analysis"}
        with _cache_lock:
            _cache[key] = result
            while len(_cache) > _CACHE_SIZE:
                _cache.popitem(last=False)
    return {
        **result,
        "source": "version" if version else "draft",
        "version_id": version.id if version else None,
        "version_number": version.number if version else None,
        "analysis_id": row.id if row else None,
        "analysis_source": "reused" if row else "quick",
        "parts": "selected" if row and (row.inputs or {}).get("parts") else "generic",
        "catalogue_size": len(catalogue),
        "computed_at": utcnow(),
    }
