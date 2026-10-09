"""Validation suite for the Phase 3 engine (docs/phases/PHASE3.md section 5).

``run_validation(path=None, *, cache_dir=None, progress=None) -> dict`` runs every case and
returns the report; ``default_report_path()`` is ``{APP_DATA_DIR}/validation/latest.json``.
"""

from __future__ import annotations

from app.validation.report import default_report_path, text_summary, write_report
from app.validation.runner import run_validation

__all__ = ["default_report_path", "run_validation", "text_summary", "write_report"]
