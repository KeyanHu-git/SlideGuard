from __future__ import annotations

from pathlib import Path
from typing import Any, Callable

from .contracts import (
    emergency_result,
    failed_result,
    prepare_request,
    succeeded_result,
    validate_document,
    validated_result,
)
from .engine import export_job
from .util import utc_now


EventSink = Callable[[dict[str, Any]], None]


def progress_event(
    task_id: str | None,
    sequence: int,
    phase: str,
    state: str,
    message: str,
    *,
    completed: int,
    total: int,
) -> dict[str, Any]:
    event: dict[str, Any] = {
        "schemaVersion": "1.0",
        "event": "progress",
        "sequence": sequence,
        "timestamp": utc_now(),
        "phase": phase,
        "state": state,
        "completed": completed,
        "total": total,
        "message": message,
    }
    if task_id:
        event["taskId"] = task_id
    validate_document(event, "progress-event.schema.json")
    return event


class ExportService:
    """Public application boundary shared by machine and future desktop callers."""

    def execute(
        self,
        document: dict[str, Any],
        *,
        base_dir: Path,
        event_sink: EventSink | None = None,
    ) -> dict[str, Any]:
        prepared = None
        task_id = document.get("taskId") if isinstance(document.get("taskId"), str) else None
        sequence = 0

        def emit(phase: str, state: str, message: str, completed: int, total: int) -> None:
            nonlocal sequence
            if event_sink is not None:
                event_sink(
                    progress_event(
                        task_id, sequence, phase, state, message,
                        completed=completed, total=total,
                    )
                )
            sequence += 1

        try:
            emit("validation", "start", "Validating request", 0, 1)
            prepared = prepare_request(document, base_dir=base_dir)
            emit("validation", "complete", "Request is valid", 1, 1)
            if prepared.dry_run:
                return validated_result(prepared)

            total_slides = len(prepared.effective_slides)
            emit("export", "start", "Starting export and verification", 0, total_slides)
            output, report = export_job(prepared.source, prepared.options)
            emit("export", "complete", "Export and verification completed", total_slides, total_slides)
            return succeeded_result(prepared, output, report)
        except Exception as exc:
            try:
                return failed_result(exc, prepared=prepared, task_id=task_id)
            except Exception as result_error:
                return emergency_result(result_error)
