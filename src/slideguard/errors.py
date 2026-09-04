from __future__ import annotations

from typing import Any


class SlideGuardError(RuntimeError):
    """Base error that maps to a stable CLI exit code and machine error body."""

    exit_code = 10
    code = "SLIDEGUARD_ERROR"

    def __init__(
        self,
        message: str,
        *,
        stage: str | None = None,
        details: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.stage = stage
        self.details = details or {}


class EnvironmentError(SlideGuardError):
    exit_code = 20
    code = "ENV_UNSATISFIED"


class InputError(SlideGuardError):
    exit_code = 30
    code = "INPUT_INVALID"


class ExportError(SlideGuardError):
    exit_code = 40
    code = "EXPORT_FAILED"


class FidelityError(SlideGuardError):
    exit_code = 50
    code = "FIDELITY_FAILED"


class BudgetError(FidelityError):
    exit_code = 51
    code = "BUDGET_UNSATISFIABLE"
