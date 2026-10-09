"""The analysis worker: one thread that runs engine jobs strictly one at a time.

The machine has one CPU, and a full analysis (AVL and XFOIL in the solver subprocess) keeps it
busy for seconds, so engine jobs never run on request threads and never run in parallel. One
daemon thread drains a priority queue:

* ``analysis`` jobs (priority 0): rows of the ``analyses`` table, kind ``full`` (the analysis,
  then the recommendation sweep) or ``scale`` (scale to a target take-off mass). Progress is
  written to the row, throttled to one write every :data:`PROGRESS_WRITE_INTERVAL_S` (a stage
  label change is written at once when the last write is older than a quarter of that).
* ``validation`` (priority 1): the validation suite (:mod:`app.validation`); its state lives in
  memory and its report in ``{APP_DATA_DIR}/validation/latest.json``. Analyses queued while a
  validation is waiting go first.
* ``refresh`` (priority 2, Phase 4): one part's supplier-listing refresh (Claude with web
  search, :func:`app.suppliers.refresh_part`), queued by "refresh all in this parts list". One
  item per part, so an analysis queued meanwhile runs before the next part; the state lives
  on the part row (``listings_refresh_status`` / ``_message``).
* ``export`` (priority 2, Phase 5, first come first served with refreshes): one "Generate
  files" row of the ``exports`` table. The CAD kernel runs in a child process
  (:func:`app.exports.run_export_job`) so OpenCascade's memory goes back to the OS when it
  ends; progress, the time limit and the memory guard are handled there.

Image readings keep their own single-worker executor (``app.routers.readings``), so a long
analysis never holds up a reading for more than the shared CPU does.

Full-analysis result and recommendations (design choice, documented in docs/phases/PHASE3.md):
the job stores the analysis result on the row as soon as it is ready, with
``result["recommendations"] = None`` and the stage "Finding improvements", while the status is
still ``running``; the sweep then fills ``result["recommendations"]`` and the status becomes
``done``. The UI shows results first and recommendations when they arrive.

Lifecycle: :meth:`AnalysisWorker.start` at application start (after
:func:`recover_analyses`, which marks rows left ``running`` by a crashed process as ``error`` and
re-queues rows still ``queued``); :meth:`AnalysisWorker.stop` at shutdown asks the running job
to stop at its next progress report (the row goes back to ``queued`` so it runs again after the
restart), then stops the AVL/XFOIL subprocess with ``shutdown_solver_worker()``.
"""

from __future__ import annotations

import itertools
import json
import logging
import queue
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from sqlalchemy import select, update
from sqlalchemy.orm import Session, sessionmaker

from app.config import Settings
from app.db import utcnow
from app.engine.avl_model import shutdown_solver_worker
from app.models import Analysis

log = logging.getLogger("app.jobs")

PROGRESS_WRITE_INTERVAL_S = 1.0
STOP_JOIN_TIMEOUT_S = 20.0

INTERRUPTED_MESSAGE = (
    "The analysis was interrupted because the server stopped unexpectedly. Run it again."
)
UNEXPECTED_MESSAGE = (
    "Something went wrong in the analysis engine. Nothing was changed. Try again; if it keeps "
    "happening, undo the last change to the design."
)
REQUEUED_STAGE = "Waiting (the server restarted)"

#: Bumped when the composition of a job (not the engine) changes, so ``inputs_hash`` reuse
#: never hands back a result made by an older job shape.
JOB_VERSION = "jobs-2"  # jobs-2: Phase 4 selected parts in the inputs


class JobCancelled(BaseException):
    """Raised from a progress callback when the worker is stopping.

    A ``BaseException`` so the engine's own ``except Exception`` guards (for example the
    validation runner recording a failed case group) let it through.
    """


def polar_cache_dir(settings: Settings) -> Path:
    return settings.app_data_dir / "cache" / "polars"


def validation_report_path(settings: Settings) -> Path:
    return settings.app_data_dir / "validation" / "latest.json"


# ---------------------------------------------------------------------------
# Validation state (in memory; the report itself is a file)
# ---------------------------------------------------------------------------


@dataclass
class ValidationJob:
    status: str = "idle"  # idle | queued | running | done | error
    progress: float = 0.0
    stage: str = ""
    queued_at: datetime | None = None
    started_at: datetime | None = None
    finished_at: datetime | None = None
    error: str | None = None
    trigger: str | None = None  # "startup" | "owner"
    lock: threading.Lock = field(default_factory=threading.Lock, repr=False)

    def snapshot(self) -> dict[str, Any]:
        with self.lock:
            return {
                "status": self.status,
                "progress": round(self.progress, 3),
                "stage": self.stage,
                "queued_at": self.queued_at,
                "started_at": self.started_at,
                "finished_at": self.finished_at,
                "error": self.error,
                "trigger": self.trigger,
            }

    def set(self, **values: Any) -> None:
        with self.lock:
            for key, value in values.items():
                setattr(self, key, value)


# ---------------------------------------------------------------------------
# Progress writer
# ---------------------------------------------------------------------------


class _Progress:
    """Maps an engine's 0-1 progress into a window of the job's progress and writes it to the
    row, throttled. Raises :class:`JobCancelled` when the worker is stopping."""

    def __init__(
        self,
        session_factory: sessionmaker[Session],
        analysis_id: int,
        stop: threading.Event,
        model: Any = Analysis,
    ) -> None:
        self.session_factory = session_factory
        self.analysis_id = analysis_id  # the row id (an analysis, or an export)
        self.stop = stop
        self.model = model
        self.last_write = 0.0
        self.last_stage = ""
        self.progress = 0.0

    def window(self, lo: float, hi: float) -> Any:
        def report(frac: float, label: str) -> None:
            if label == "Done":  # an inner engine finished; the job itself goes on
                label = self.last_stage or "Working"
            self.update(lo + (hi - lo) * max(0.0, min(1.0, float(frac))), label)

        return report

    def update(self, progress: float, stage: str, *, force: bool = False) -> None:
        if self.stop.is_set():
            raise JobCancelled()
        self.progress = max(self.progress, progress)  # never move backwards
        now = time.monotonic()
        elapsed = now - self.last_write
        stage_changed = stage != self.last_stage
        if not (
            force
            or elapsed >= PROGRESS_WRITE_INTERVAL_S
            or (stage_changed and elapsed >= PROGRESS_WRITE_INTERVAL_S / 4)
        ):
            return
        self.last_write = now
        self.last_stage = stage
        self.write(progress=round(self.progress, 4), stage=stage[:200])

    def write(self, **values: Any) -> None:
        with self.session_factory() as db:
            db.execute(update(self.model).where(self.model.id == self.analysis_id).values(**values))
            db.commit()


# ---------------------------------------------------------------------------
# Job bodies
# ---------------------------------------------------------------------------


def _run_full(inputs: dict[str, Any], cache_dir: str, prog: _Progress) -> dict[str, Any]:
    from app.engine.analysis import run_analysis
    from app.engine.recommend import run_recommendations

    meta = inputs.get("settings_meta")
    result = run_analysis(
        inputs["parameters"],
        inputs["mission"],
        inputs["settings"],
        mode="full",
        cache_dir=cache_dir,
        progress=prog.window(0.02, 0.55),
        settings_meta=meta,
        parts=inputs.get("parts"),
    )
    if prog.stop.is_set():  # the solver may have been stopped under the analysis
        raise JobCancelled()
    result["recommendations"] = None
    if not result.get("valid"):
        result["recommendations"] = {
            "valid": False,
            "recommendations": [],
            "fixes": [],
            "rejected": [],
            "message": "No recommendations: the design could not be analysed. Fix the failed "
            "checks first.",
        }
        return result
    # Results first: the UI can show them while the sweep runs.
    prog.write(result=result, progress=0.56, stage="Finding improvements")
    prog.last_write = time.monotonic()
    prog.last_stage = "Finding improvements"
    prog.progress = 0.56
    try:
        recs = run_recommendations(
            inputs["parameters"],
            inputs["mission"],
            inputs["settings"],
            cache_dir=cache_dir,
            progress=prog.window(0.56, 0.99),
            settings_meta=meta,
            parts=inputs.get("parts"),
        )
    except JobCancelled:
        raise
    except Exception:
        log.exception("Recommendation sweep failed for analysis %s", prog.analysis_id)
        recs = {
            "valid": False,
            "recommendations": [],
            "fixes": [],
            "rejected": [],
            "message": "The recommendation sweep failed; the analysis above is still valid.",
        }
    if prog.stop.is_set():
        raise JobCancelled()
    result["recommendations"] = recs
    return result


def _run_scale(inputs: dict[str, Any], cache_dir: str, prog: _Progress) -> dict[str, Any]:
    from app.engine.scale import run_scale

    result = run_scale(
        inputs["parameters"],
        inputs["mission"],
        inputs["settings"],
        float(inputs["target_takeoff_mass_kg"]),
        mode="full",
        cache_dir=cache_dir,
        progress=prog.window(0.02, 0.99),
        settings_meta=inputs.get("settings_meta"),
    )
    if prog.stop.is_set():
        raise JobCancelled()
    return result


def run_analysis_job(
    session_factory: sessionmaker[Session],
    settings: Settings,
    analysis_id: int,
    stop: threading.Event,
) -> None:
    """Run one queued analysis row to completion. Never raises (except :class:`JobCancelled`
    handling, which puts the row back in the queue for the next start)."""
    with session_factory() as db:
        row = db.get(Analysis, analysis_id)
        if row is None or row.status != "queued":
            return
        inputs = dict(row.inputs)
        kind = row.kind
        row.status = "running"
        row.started_at = utcnow()
        row.progress = 0.01
        row.stage = "Starting"
        row.error = None
        db.commit()
    prog = _Progress(session_factory, analysis_id, stop)
    t0 = time.monotonic()
    cache_dir = str(polar_cache_dir(settings))
    try:
        if kind == "scale":
            result = _run_scale(inputs, cache_dir, prog)
        else:
            result = _run_full(inputs, cache_dir, prog)
    except JobCancelled:
        log.info("Analysis %s interrupted by shutdown; re-queued", analysis_id)
        _safe_write(
            prog,
            status="queued",
            progress=0.0,
            stage=REQUEUED_STAGE,
            result=None,
            started_at=None,
        )
        return
    except Exception:
        log.exception("Analysis %s failed", analysis_id)
        _safe_write(
            prog,
            status="error",
            error=UNEXPECTED_MESSAGE,
            stage="Failed",
            finished_at=utcnow(),
            duration_s=round(time.monotonic() - t0, 2),
        )
        return
    _safe_write(
        prog,
        status="done",
        progress=1.0,
        stage="Done",
        result=result,
        finished_at=utcnow(),
        duration_s=round(time.monotonic() - t0, 2),
    )


def _safe_write(prog: _Progress, **values: Any) -> None:
    try:
        prog.write(**values)
    except Exception:
        log.exception("Could not store the outcome of analysis %s", prog.analysis_id)


def run_refresh_job(
    settings: Settings, session_factory: sessionmaker[Session], part_id: int
) -> None:
    from app.suppliers import SupplierError, refresh_part

    try:
        refresh_part(session_factory, settings, part_id)
    except SupplierError as exc:  # stored on the part by refresh_part
        log.info("Listing refresh of part %s failed: %s", part_id, exc)


def recover_refreshes(session_factory: sessionmaker[Session]) -> int:
    """At startup: listing refreshes left queued or running by a stopped process are marked
    interrupted (the owner can queue them again; the hourly limit is lifted for them)."""
    from app.models import Part

    with session_factory() as db:
        result = db.execute(
            update(Part)
            .where(Part.listings_refresh_status.in_(("queued", "running")))
            .values(
                listings_refresh_status="error",
                listings_refresh_message="Interrupted because the server restarted. Refresh again.",
                listings_refreshed_at=None,
            )
        )
        db.commit()
    return int(getattr(result, "rowcount", 0) or 0)


def run_validation_job(settings: Settings, state: ValidationJob, stop: threading.Event) -> None:
    from app.validation import run_validation

    state.set(status="running", progress=0.0, stage="Starting", started_at=utcnow(), error=None)

    def progress(frac: float, label: str) -> None:
        if stop.is_set():
            raise JobCancelled()
        state.set(progress=float(frac), stage=label)

    try:
        report = run_validation(
            str(validation_report_path(settings)),
            cache_dir=str(polar_cache_dir(settings)),
            progress=progress,
        )
    except JobCancelled:
        state.set(status="idle", stage="Interrupted (the server stopped)", progress=0.0)
        return
    except Exception:
        log.exception("Validation run failed")
        state.set(
            status="error",
            error="The validation suite could not run. Try again.",
            finished_at=utcnow(),
        )
        return
    summary = report.get("summary", {})
    state.set(
        status="done",
        progress=1.0,
        stage=f"{summary.get('pass', 0)} passed, {summary.get('fail', 0)} failed",
        finished_at=utcnow(),
    )


# ---------------------------------------------------------------------------
# The worker
# ---------------------------------------------------------------------------

_STOP = (99, -1, "stop", 0)


class AnalysisWorker:
    """One thread, one queue, one job at a time."""

    def __init__(self, session_factory: sessionmaker[Session], settings: Settings) -> None:
        self.session_factory = session_factory
        self.settings = settings
        self.validation = ValidationJob()
        self._queue: queue.PriorityQueue[tuple[int, int, str, int]] = queue.PriorityQueue()
        self._seq = itertools.count()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._current: tuple[str, int] | None = None
        self._cancel = threading.Event()  # cancels the running export (its row was deleted)

    # -- control -------------------------------------------------------------------------

    def start(self) -> None:
        self._thread = threading.Thread(target=self._loop, name="analysis-worker", daemon=True)
        self._thread.start()

    @property
    def running(self) -> bool:
        return self._thread is not None and self._thread.is_alive() and not self._stop.is_set()

    def stop(self, timeout: float = STOP_JOIN_TIMEOUT_S) -> None:
        """Ask the current job to stop, drop the queue, stop the solver subprocess."""
        self._stop.set()
        self._queue.put(_STOP)
        thread = self._thread
        if thread is not None:
            thread.join(timeout)
        # Stopping the solver also unblocks a job waiting on an AVL/XFOIL answer.
        shutdown_solver_worker()
        if thread is not None and thread.is_alive():
            thread.join(5.0)
            if thread.is_alive():
                log.warning("The analysis worker did not stop in time")

    # -- submission ----------------------------------------------------------------------

    def submit_analysis(self, analysis_id: int) -> None:
        if self._stop.is_set():
            raise RuntimeError("worker stopped")
        self._queue.put((0, next(self._seq), "analysis", analysis_id))

    def submit_validation(self, trigger: str) -> bool:
        """Queue a validation run unless one is already queued or running."""
        if self._stop.is_set():
            raise RuntimeError("worker stopped")
        with self.validation.lock:
            if self.validation.status in ("queued", "running"):
                return False
            self.validation.status = "queued"
            self.validation.progress = 0.0
            self.validation.stage = "Waiting for the analysis worker"
            self.validation.queued_at = utcnow()
            self.validation.error = None
            self.validation.trigger = trigger
        self._queue.put((1, next(self._seq), "validation", 0))
        return True

    def submit_refresh(self, part_id: int) -> None:
        if self._stop.is_set():
            raise RuntimeError("worker stopped")
        self._queue.put((2, next(self._seq), "refresh", part_id))

    def submit_export(self, export_id: int) -> None:
        if self._stop.is_set():
            raise RuntimeError("worker stopped")
        self._queue.put((2, next(self._seq), "export", export_id))

    def cancel_export(self, export_id: int) -> None:
        """Stop the export if it is the running job (its row is being deleted). A queued one
        is skipped when its turn comes, because the row is gone."""
        if self._current == ("export", export_id):
            self._cancel.set()

    def queue_position(self, ident: int, kind: str = "analysis") -> int | None:
        """How many jobs run before this one (0 = it is next or running)."""
        with self._queue.mutex:
            items = sorted(self._queue.queue)
        keys = [(k, i) for (_p, _s, k, i) in items if k != "stop"]
        if (kind, ident) in keys:
            ahead = keys.index((kind, ident))
            return ahead + (1 if self._current is not None else 0)
        return None

    # -- loop ----------------------------------------------------------------------------

    def _loop(self) -> None:
        while not self._stop.is_set():
            _prio, _seq, kind, ident = self._queue.get()
            if kind == "stop" or self._stop.is_set():
                break
            self._cancel.clear()
            self._current = (kind, ident)
            try:
                if kind == "analysis":
                    run_analysis_job(self.session_factory, self.settings, ident, self._stop)
                elif kind == "validation":
                    run_validation_job(self.settings, self.validation, self._stop)
                elif kind == "refresh":
                    run_refresh_job(self.settings, self.session_factory, ident)
                elif kind == "export":
                    from app.exports import run_export_job

                    run_export_job(
                        self.session_factory, self.settings, ident, self._stop, self._cancel
                    )
            except BaseException:  # never let the worker thread die
                log.exception("Job %s %s crashed the worker loop", kind, ident)
            finally:
                self._current = None


def recover_analyses(session_factory: sessionmaker[Session]) -> tuple[int, list[int]]:
    """At startup: rows left ``running`` by a process that died can never finish (and might
    have killed it), so they become ``error``; rows still ``queued`` (including those a clean
    shutdown put back) are returned, oldest first, to be queued again."""
    with session_factory() as db:
        result = db.execute(
            update(Analysis)
            .where(Analysis.status == "running")
            .values(status="error", error=INTERRUPTED_MESSAGE, stage="Interrupted")
        )
        queued = list(
            db.scalars(
                select(Analysis.id).where(Analysis.status == "queued").order_by(Analysis.id)
            ).all()
        )
        db.commit()
    return int(getattr(result, "rowcount", 0) or 0), queued


def canonical_json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
