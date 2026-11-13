"""
Execution logger for tracking workflow stage progression, durations, and statuses.
"""
from datetime import datetime, timezone
import time
from typing import List, Optional
import json

from app.core.schemas import StageStatus, StageExecutionRecord


class ExecutionLogger:
    def __init__(self):
        self.records: List[StageExecutionRecord] = []
        self._current_stage_name: Optional[str] = None
        self._stage_start_perf: Optional[float] = None
        self._stage_start_dt: Optional[str] = None

    def start_stage(self, stage_name: str) -> None:
        """Marks a stage as started."""
        self._current_stage_name = stage_name
        self._stage_start_perf = time.perf_counter()
        self._stage_start_dt = datetime.now(timezone.utc).isoformat()

    def complete_stage(self, details: Optional[str] = None) -> None:
        """Marks the current stage as completed."""
        if not self._current_stage_name:
            return
        duration_ms = (time.perf_counter() - self._stage_start_perf) * 1000.0 if self._stage_start_perf else 0.0
        self.records.append(
            StageExecutionRecord(
                stage_name=self._current_stage_name,
                status=StageStatus.COMPLETED,
                start_time=self._stage_start_dt or datetime.now(timezone.utc).isoformat(),
                completion_time=datetime.now(timezone.utc).isoformat(),
                duration_ms=round(duration_ms, 2),
                details=details
            )
        )
        self._current_stage_name = None

    def fail_stage(self, error_message: str) -> None:
        """Marks the current stage as failed."""
        if not self._current_stage_name:
            return
        duration_ms = (time.perf_counter() - self._stage_start_perf) * 1000.0 if self._stage_start_perf else 0.0
        self.records.append(
            StageExecutionRecord(
                stage_name=self._current_stage_name,
                status=StageStatus.FAILED,
                start_time=self._stage_start_dt or datetime.now(timezone.utc).isoformat(),
                completion_time=datetime.now(timezone.utc).isoformat(),
                duration_ms=round(duration_ms, 2),
                error=error_message
            )
        )
        self._current_stage_name = None

    def skip_stage(self, stage_name: str, reason: str) -> None:
        """Records a skipped stage."""
        self.records.append(
            StageExecutionRecord(
                stage_name=stage_name,
                status=StageStatus.SKIPPED,
                start_time=datetime.now(timezone.utc).isoformat(),
                completion_time=datetime.now(timezone.utc).isoformat(),
                duration_ms=0.0,
                details=reason
            )
        )

    def to_json(self) -> str:
        """Serializes stage records to JSON string."""
        return json.dumps([r.model_dump() for r in self.records])
