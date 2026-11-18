"""
Execution logger for tracking workflow stage progression, durations, and statuses.
"""
import time
from typing import List, Optional, Callable
import json

from app.core.schemas import StageStatus, StageExecutionRecord
from app.utils.time import now_utc


class ExecutionLogger:
    def __init__(self, on_event: Optional[Callable[[dict], None]] = None):
        """
        ``on_event`` is an optional callback fired whenever a stage starts,
        completes, or fails. It receives a small dict describing the event so a
        UI can render the pipeline live as it runs. Exceptions raised by the
        callback are swallowed so instrumentation never breaks a run.
        """
        self.records: List[StageExecutionRecord] = []
        self._current_stage_name: Optional[str] = None
        self._stage_start_perf: Optional[float] = None
        self._stage_start_dt: Optional[str] = None
        self._on_event = on_event

    def _emit(self, event: dict) -> None:
        if not self._on_event:
            return
        try:
            self._on_event(event)
        except Exception:
            pass

    def start_stage(self, stage_name: str) -> None:
        """Marks a stage as started."""
        self._current_stage_name = stage_name
        self._stage_start_perf = time.perf_counter()
        self._stage_start_dt = now_utc().isoformat()
        self._emit({
            "type": "start", "stage_name": stage_name,
            "start_time": self._stage_start_dt,
        })

    def complete_stage(self, details: Optional[str] = None) -> None:
        """Marks the current stage as completed."""
        if not self._current_stage_name:
            return
        duration_ms = (time.perf_counter() - self._stage_start_perf) * 1000.0 if self._stage_start_perf else 0.0
        stage_name = self._current_stage_name
        self.records.append(
            StageExecutionRecord(
                stage_name=stage_name,
                status=StageStatus.COMPLETED,
                start_time=self._stage_start_dt or now_utc().isoformat(),
                completion_time=now_utc().isoformat(),
                duration_ms=round(duration_ms, 2),
                details=details
            )
        )
        self._current_stage_name = None
        self._emit({
            "type": "complete", "stage_name": stage_name,
            "details": details, "duration_ms": round(duration_ms, 2)
        })

    def fail_stage(self, error_message: str) -> None:
        """Marks the current stage as failed."""
        if not self._current_stage_name:
            return
        duration_ms = (time.perf_counter() - self._stage_start_perf) * 1000.0 if self._stage_start_perf else 0.0
        stage_name = self._current_stage_name
        self.records.append(
            StageExecutionRecord(
                stage_name=stage_name,
                status=StageStatus.FAILED,
                start_time=self._stage_start_dt or now_utc().isoformat(),
                completion_time=now_utc().isoformat(),
                duration_ms=round(duration_ms, 2),
                error=error_message
            )
        )
        self._current_stage_name = None
        self._emit({
            "type": "fail", "stage_name": stage_name,
            "error": error_message, "duration_ms": round(duration_ms, 2)
        })

    def skip_stage(self, stage_name: str, reason: str) -> None:
        """Records a skipped stage."""
        self.records.append(
            StageExecutionRecord(
                stage_name=stage_name,
                status=StageStatus.SKIPPED,
                start_time=now_utc().isoformat(),
                completion_time=now_utc().isoformat(),
                duration_ms=0.0,
                details=reason
            )
        )

    def to_json(self) -> str:
        """Serializes stage records to JSON string."""
        return json.dumps([r.model_dump() for r in self.records])
