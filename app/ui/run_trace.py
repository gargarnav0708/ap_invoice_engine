"""
Pure helpers that turn a persisted ``ProcessingRun.stages_log`` into rows for
the Live Processing View and decide whether the view should keep polling.

Malformed or missing trace JSON never raises: the caller gets an explanatory
``trace_error`` and whatever stages could be recovered.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

from app.workflow.stages import PIPELINE_STAGES

TERMINAL_RUN_STATUSES = frozenset({"SUCCESS", "FAILED"})
_STAGE_NUMBER = re.compile(r"^\s*(\d+)\s*[.)]\s*(.*)$")


@dataclass
class StageView:
    number: Optional[int]
    name: str
    status: str
    duration_ms: Optional[float] = None
    start_time: Optional[str] = None
    completion_time: Optional[str] = None
    details: Optional[str] = None
    error: Optional[str] = None


@dataclass
class RunTrace:
    stages: list[StageView] = field(default_factory=list)
    trace_error: Optional[str] = None

    @property
    def completed(self) -> int:
        return sum(1 for s in self.stages if s.status == "COMPLETED")

    @property
    def failed_stage(self) -> Optional[StageView]:
        return next((s for s in self.stages if s.status == "FAILED"), None)


def split_stage_label(label: str) -> tuple[Optional[int], str]:
    match = _STAGE_NUMBER.match(label or "")
    if match:
        return int(match.group(1)), match.group(2).strip() or label
    return None, (label or "Unnamed stage")


def _as_float(value: Any) -> Optional[float]:
    try:
        return float(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def _as_text(value: Any) -> Optional[str]:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def parse_stages_log(raw: Optional[str]) -> tuple[list[dict[str, Any]], Optional[str]]:
    """Return (stage dicts, error message). Never raises."""
    if raw is None or not str(raw).strip():
        return [], None
    try:
        data = json.loads(raw)
    except (TypeError, ValueError) as exc:
        return [], f"The saved stage trace could not be read ({exc.__class__.__name__}); showing run status only."
    if not isinstance(data, list):
        return [], "The saved stage trace has an unexpected format; showing run status only."
    stages = [item for item in data if isinstance(item, dict)]
    skipped = len(data) - len(stages)
    return stages, (f"{skipped} malformed stage record(s) were ignored." if skipped else None)


def build_run_trace(raw_log: Optional[str], execution_status: Optional[str]) -> RunTrace:
    """Merge persisted stage records with the canonical 19-stage list.

    * Every persisted record is kept, in order (including unknown labels).
    * While RUNNING, canonical stages not reached yet are shown as PENDING.
    * After FAILED, unreached stages are shown as NOT_RUN.
    * After SUCCESS, only the persisted records are shown.
    """
    records, error = parse_stages_log(raw_log)
    status = (execution_status or "").upper()
    stages: list[StageView] = []
    seen: set[str] = set()
    for idx, rec in enumerate(records, start=1):
        label = _as_text(rec.get("stage_name")) or f"Stage {idx}"
        number, name = split_stage_label(label)
        stages.append(StageView(
            number=number if number is not None else idx,
            name=name,
            status=(_as_text(rec.get("status")) or "UNKNOWN").upper(),
            duration_ms=_as_float(rec.get("duration_ms")),
            start_time=_as_text(rec.get("start_time")),
            completion_time=_as_text(rec.get("completion_time")),
            details=_as_text(rec.get("details")),
            error=_as_text(rec.get("error")),
        ))
        seen.add(label)

    if status in {"RUNNING", "FAILED"}:
        placeholder = "PENDING" if status == "RUNNING" else "NOT_RUN"
        for label in PIPELINE_STAGES:
            if label not in seen:
                number, name = split_stage_label(label)
                stages.append(StageView(number=number, name=name, status=placeholder))
    return RunTrace(stages=stages, trace_error=error)


def is_terminal(execution_status: Optional[str]) -> bool:
    return (execution_status or "").upper() in TERMINAL_RUN_STATUSES


def is_stale(start_time: Optional[datetime], now: datetime, stale_after_minutes: int) -> bool:
    """True when a RUNNING run has not finished within the stale window."""
    if start_time is None:
        return False
    if start_time.tzinfo is None:
        start_time = start_time.replace(tzinfo=timezone.utc)
    return now - start_time > timedelta(minutes=stale_after_minutes)


def should_poll(execution_status: Optional[str], start_time: Optional[datetime],
                now: datetime, stale_after_minutes: int) -> bool:
    """Poll only while the run is genuinely in progress."""
    if (execution_status or "").upper() != "RUNNING":
        return False
    return not is_stale(start_time, now, stale_after_minutes)


def format_duration(ms: Optional[float]) -> str:
    if ms is None:
        return "—"
    if ms >= 1000:
        return f"{ms / 1000:.2f} s"
    return f"{ms:.0f} ms"
