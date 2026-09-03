class SlideGuardError(RuntimeError):
    """Base error that maps to a stable CLI exit code."""

    exit_code = 10
    code = "SLIDEGUARD_ERROR"


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

