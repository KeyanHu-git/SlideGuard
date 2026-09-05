from __future__ import annotations

import copy
import json
import re
from collections import Counter
from dataclasses import dataclass
from typing import Any, Callable, Iterable

from . import PIPELINE_REVISION, __version__
from .offline import offline_policy
from .contracts import validate_document
from .errors import InputError


DIAGNOSTIC_SCHEMA_VERSION = "1.0"
MAX_DIAGNOSTIC_BYTES = 256 * 1024

Redactor = Callable[[Any], Any]
SecretScanner = Callable[[Any], Iterable[str]]


class DiagnosticSafetyError(InputError):
    code = "DIAGNOSTIC_SAFETY_REJECTED"


class DiagnosticSizeError(InputError):
    code = "DIAGNOSTIC_SIZE_EXCEEDED"


@dataclass(frozen=True, slots=True)
class PrivacyHooks:
    redact: Redactor
    scan_categories: SecretScanner


def _default_privacy_hooks() -> PrivacyHooks:
    try:
        from . import privacy
    except ImportError as exc:
        raise DiagnosticSafetyError(
            "Diagnostic privacy service is unavailable",
            stage="diagnosis",
            details={"categories": ["PRIVACY_SERVICE_UNAVAILABLE"]},
        ) from exc
    redact = getattr(privacy, "redact_for_sharing", None)
    scan = getattr(privacy, "scan_secret_categories", None)
    if not callable(redact) or not callable(scan):
        raise DiagnosticSafetyError(
            "Diagnostic privacy service does not provide the required interface",
            stage="diagnosis",
            details={"categories": ["PRIVACY_INTERFACE_UNAVAILABLE"]},
        )
    return PrivacyHooks(redact=redact, scan_categories=scan)


def _privacy_hooks(redactor: Redactor | None, scanner: SecretScanner | None) -> PrivacyHooks:
    if redactor is None and scanner is None:
        return _default_privacy_hooks()
    if not callable(redactor) or not callable(scanner):
        raise DiagnosticSafetyError(
            "A redactor and a secret scanner must be supplied together",
            stage="diagnosis",
            details={"categories": ["PRIVACY_INTERFACE_INCOMPLETE"]},
        )
    return PrivacyHooks(redact=redactor, scan_categories=scanner)


def _category_names(values: Iterable[str]) -> list[str]:
    categories = set()
    for value in values:
        text = str(value).upper()
        categories.add(text if re.fullmatch(r"[A-Z][A-Z0-9_-]{0,63}", text) else "UNCLASSIFIED_SECRET")
    return sorted(categories)


def _reject_secret_hits(values: Iterable[str], *, pass_name: str) -> None:
    categories = _category_names(values)
    if categories:
        raise DiagnosticSafetyError(
            "Diagnostic data failed the secret scan",
            stage="diagnosis",
            details={"scanPass": pass_name, "categories": categories},
        )


def _scan_and_reject(scanner: SecretScanner, value: Any, *, pass_name: str) -> None:
    try:
        matches = scanner(value)
        _reject_secret_hits(matches, pass_name=pass_name)
    except DiagnosticSafetyError:
        raise
    except Exception as exc:
        raise DiagnosticSafetyError(
            "Diagnostic secret scanner failed closed",
            stage="diagnosis",
            details={"scanPass": pass_name, "categories": ["SECRET_SCANNER_FAILED"]},
        ) from exc


def _redact_or_reject(redactor: Redactor, value: Any, *, pass_name: str) -> Any:
    try:
        return redactor(value)
    except Exception as exc:
        raise DiagnosticSafetyError(
            "Diagnostic privacy redaction failed closed",
            stage="diagnosis",
            details={"scanPass": pass_name, "categories": ["PRIVACY_REDACTION_FAILED"]},
        ) from exc


def _safe_token(value: Any, *, fallback: str, pattern: str, maximum: int = 80) -> str:
    text = str(value or "")[:maximum]
    return text if re.fullmatch(pattern, text) else fallback


def _version(value: Any) -> str | None:
    text = str(value or "")
    return text if re.fullmatch(r"[0-9]+(?:\.[0-9]+){0,5}", text) else None


def _doctor_summary(doctor: dict[str, Any]) -> tuple[dict[str, Any], set[str]]:
    executables = doctor.get("executables") if isinstance(doctor.get("executables"), dict) else {}
    availability = (
        doctor.get("dependencyAvailability")
        if isinstance(doctor.get("dependencyAvailability"), dict)
        else {}
    )
    powerpoint = doctor.get("powerpoint") if isinstance(doctor.get("powerpoint"), dict) else None
    platform = doctor.get("platform") if isinstance(doctor.get("platform"), dict) else {}
    errors = [str(item).casefold() for item in doctor.get("errors", []) if isinstance(item, str)]
    dependencies = {
        "powerShell": bool(availability.get("powerShell", executables.get("powershell"))),
        "pdfToCairo": bool(availability.get("pdfToCairo", executables.get("pdftocairo"))),
        "pdfToPpm": bool(availability.get("pdfToPpm", executables.get("pdftoppm"))),
        "pdfInfo": bool(availability.get("pdfInfo", executables.get("pdfinfo"))),
        "svgRenderer": bool(availability.get("svgRenderer", doctor.get("svgRenderer"))),
    }
    issues: set[str] = set()
    if powerpoint is None:
        issues.add("POWERPOINT_UNAVAILABLE")
    if not all(dependencies[name] for name in ("pdfToCairo", "pdfToPpm", "pdfInfo")):
        issues.add("POPPLER_UNAVAILABLE")
    if not dependencies["powerShell"]:
        issues.add("POWERSHELL_UNAVAILABLE")
    compatibility = doctor.get("compatibility")
    if isinstance(compatibility, dict) and compatibility.get("powerpoint") == "unsupported":
        issues.add("POWERPOINT_VERSION_UNSUPPORTED")
    joined_errors = " ".join(errors)
    if "permission" in joined_errors or "access is denied" in joined_errors:
        issues.add("PERMISSION_DENIED")
    return (
        {
            "ok": bool(doctor.get("ok")),
            "platform": {
                "system": _safe_token(platform.get("system"), fallback="unknown", pattern=r"[A-Za-z0-9_.-]+"),
                "release": _safe_token(platform.get("release"), fallback="unknown", pattern=r"[A-Za-z0-9_.-]+"),
                "machine": _safe_token(platform.get("machine"), fallback="unknown", pattern=r"[A-Za-z0-9_.-]+"),
            },
            "powerpoint": {
                "available": powerpoint is not None,
                "version": _version(powerpoint.get("version")) if powerpoint else None,
                "build": _version(powerpoint.get("build")) if powerpoint else None,
            },
            "dependencies": dependencies,
            "issues": sorted(issues),
        },
        issues,
    )


def _error_event(error: dict[str, Any] | None) -> tuple[list[dict[str, Any]], set[str]]:
    if not error:
        return [], set()
    body = error.get("error") if isinstance(error.get("error"), dict) else error
    if not isinstance(body, dict):
        return [], set()
    code = _safe_token(body.get("code"), fallback="UNKNOWN_ERROR", pattern=r"[A-Z][A-Z0-9_]{0,63}")
    stage = _safe_token(body.get("stage"), fallback="execution", pattern=r"[a-z][a-z0-9_-]{0,63}")
    exit_code = body.get("exitCode")
    details = body.get("details") if isinstance(body.get("details"), dict) else {}
    event = {
        "code": code,
        "stage": stage,
        "exitCode": int(exit_code) if isinstance(exit_code, int) and 0 <= exit_code <= 255 else None,
        "transient": bool(details.get("transient")),
        "residualRisk": bool(details.get("residualRisk")),
    }
    issues = {code}
    if event["residualRisk"]:
        issues.add("POWERPOINT_CLEANUP_PENDING")
    return [event], issues


def _report_summary(report: dict[str, Any] | None) -> tuple[dict[str, Any] | None, str | None]:
    if not report:
        return None, None
    findings = report.get("findings") if isinstance(report.get("findings"), list) else []
    counts = Counter()
    codes = set()
    for finding in findings[:10000]:
        if not isinstance(finding, dict):
            continue
        status = _safe_token(
            finding.get("status"), fallback="UNKNOWN", pattern=r"[A-Z][A-Z0-9_]{0,63}",
        )
        counts[status] += 1
        code = _safe_token(
            finding.get("code"), fallback="UNKNOWN_FINDING", pattern=r"[A-Z][A-Z0-9_]{0,63}",
        )
        codes.add(code)
    artifacts = report.get("artifacts") if isinstance(report.get("artifacts"), list) else []
    artifact_kinds = {
        _safe_token(item.get("kind"), fallback="unknown", pattern=r"[a-z][a-z0-9_-]{0,31}", maximum=32)
        for item in artifacts[:10000] if isinstance(item, dict)
    }
    verdict = _safe_token(
        report.get("verdict"), fallback="UNKNOWN", pattern=r"(?:PASS|PASS_WITH_SOURCE_WARNINGS|FAIL|N/A|UNKNOWN)",
    )
    fingerprint = report.get("configFingerprint") or report.get("config_fingerprint")
    if not isinstance(fingerprint, str) or re.fullmatch(r"sha256:[0-9a-f]{64}", fingerprint) is None:
        fingerprint = None
    return (
        {
            "verdict": verdict,
            "findingCounts": dict(sorted(counts.items())),
            "findingCodes": sorted(codes)[:256],
            "artifactKinds": sorted(artifact_kinds)[:32],
        },
        fingerprint,
    )


_RECOMMENDATIONS: dict[str, tuple[str, tuple[str, ...]]] = {
    "POWERPOINT_UNAVAILABLE": (
        "Install or repair desktop PowerPoint",
        ("Confirm that desktop Microsoft PowerPoint is installed for the current user.", "Run SlideGuard doctor again."),
    ),
    "POPPLER_UNAVAILABLE": (
        "Repair the packaged PDF tools",
        ("Repair or reinstall SlideGuard's packaged PDF tools.", "Run SlideGuard doctor again."),
    ),
    "POWERSHELL_UNAVAILABLE": (
        "Restore Windows PowerShell",
        ("Confirm that Windows PowerShell is available in the supported Windows installation.", "Run SlideGuard doctor again."),
    ),
    "POWERPOINT_VERSION_UNSUPPORTED": (
        "Use a supported PowerPoint build",
        ("Check the supported Windows and Office matrix.", "Update or repair PowerPoint, then run doctor again."),
    ),
    "PERMISSION_DENIED": (
        "Choose readable input and writable output folders",
        ("Check read access to the PPTX and write access to the selected output folder.", "Retry without administrator elevation."),
    ),
    "INPUT_INVALID": (
        "Repair the source presentation",
        ("Open the presentation in PowerPoint and save a new PPTX copy.", "Check the requested slide numbers, then retry."),
    ),
    "POWERPOINT_CLEANUP_PENDING": (
        "Wait for the PowerPoint worker to finish safely",
        ("Leave PowerPoint open while the blocking call returns.", "Run doctor again after the worker status reports cleanup complete."),
    ),
    "EXPORT_FAILED": (
        "Check PowerPoint for a blocking dialog",
        ("Close any modal PowerPoint dialog without ending the process.", "Retry the same request once."),
    ),
}


def recommendations_for(issues: Iterable[str]) -> list[dict[str, Any]]:
    result = []
    for issue in sorted(set(issues)):
        recommendation = _RECOMMENDATIONS.get(issue)
        if recommendation is None:
            continue
        title, steps = recommendation
        result.append({"id": issue, "title": title, "steps": list(steps)})
    return result


_ABSOLUTE_PATH = re.compile(
    r"(?i)(?:^|[\s\"'])(?:[a-z]:[\\/]|\\\\|file:///|/(?!/)[^\s\"'])"
)
_FORBIDDEN_FILE = re.compile(
    r"(?i)\.(?:pptx?|pdf|svg|png|jpe?g|gif|bmp|webp|tiff?)(?:$|[?#\s\"'])"
)
_FORBIDDEN_KEYS = {"environment", "environmentvariables", "sourcepath", "inputpath", "image", "imagedata", "pptx", "binary", "base64"}


def _content_policy_categories(value: Any) -> list[str]:
    categories: set[str] = set()

    def visit(item: Any) -> None:
        if isinstance(item, dict):
            for key, child in item.items():
                normalized = re.sub(r"[^a-z]", "", str(key).casefold())
                if normalized in _FORBIDDEN_KEYS:
                    categories.add("FORBIDDEN_FIELD")
                visit(child)
        elif isinstance(item, list):
            for child in item:
                visit(child)
        elif isinstance(item, str):
            if _ABSOLUTE_PATH.search(item):
                categories.add("ABSOLUTE_PATH")
            if _FORBIDDEN_FILE.search(item):
                categories.add("SOURCE_OR_IMAGE_FILE")

    visit(value)
    return sorted(categories)


def validate_diagnostic_bundle(
    bundle: dict[str, Any],
    *,
    scanner: SecretScanner,
    max_bytes: int = MAX_DIAGNOSTIC_BYTES,
) -> int:
    policy_hits = _content_policy_categories(bundle)
    if policy_hits:
        raise DiagnosticSafetyError(
            "Diagnostic bundle violates the metadata-only policy",
            stage="diagnosis",
            details={"categories": policy_hits},
        )
    _scan_and_reject(scanner, bundle, pass_name="post-package")
    try:
        encoded = json.dumps(
            bundle, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False,
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise DiagnosticSafetyError(
            "Diagnostic bundle is not strict JSON",
            stage="diagnosis",
            details={"categories": ["NON_JSON_VALUE"]},
        ) from exc
    if len(encoded) > max_bytes:
        raise DiagnosticSizeError(
            "Diagnostic bundle exceeds the configured size limit",
            stage="diagnosis",
            details={"actualBytes": len(encoded), "maxBytes": max_bytes},
        )
    validate_document(bundle, "diagnostic-bundle.schema.json")
    return len(encoded)


def build_diagnostic_bundle(
    doctor: dict[str, Any],
    error: dict[str, Any] | None = None,
    report: dict[str, Any] | None = None,
    *,
    include_report: bool = False,
    redactor: Redactor | None = None,
    scanner: SecretScanner | None = None,
    max_bytes: int = MAX_DIAGNOSTIC_BYTES,
) -> dict[str, Any]:
    """Build a deterministic, metadata-only diagnostic document without I/O."""
    if not isinstance(doctor, dict) or (error is not None and not isinstance(error, dict)):
        raise InputError("Diagnostic inputs must be JSON objects", stage="diagnosis")
    if report is not None and not isinstance(report, dict):
        raise InputError("Diagnostic report must be a JSON object", stage="diagnosis")
    if max_bytes < 1024 or max_bytes > MAX_DIAGNOSTIC_BYTES:
        raise InputError(
            "Diagnostic size limit must be between 1024 and 262144 bytes",
            stage="diagnosis",
            details={"maxBytes": max_bytes},
        )

    hooks = _privacy_hooks(redactor, scanner)
    # The sharing redactor deliberately removes executable paths.  Preserve only
    # their boolean availability first; no path text crosses this projection.
    doctor_input = copy.deepcopy(doctor)
    executables = doctor.get("executables") if isinstance(doctor.get("executables"), dict) else {}
    doctor_input["dependencyAvailability"] = {
        "powerShell": bool(executables.get("powershell")),
        "pdfToCairo": bool(executables.get("pdftocairo")),
        "pdfToPpm": bool(executables.get("pdftoppm")),
        "pdfInfo": bool(executables.get("pdfinfo")),
        "svgRenderer": bool(doctor.get("svgRenderer")),
    }
    selected_input = {
        "doctor": doctor_input,
        "error": error,
        "report": report if include_report else None,
    }
    sanitized_input = _redact_or_reject(
        hooks.redact, copy.deepcopy(selected_input), pass_name="pre-package",
    )
    if not isinstance(sanitized_input, dict):
        raise DiagnosticSafetyError(
            "Privacy redaction returned an invalid structure",
            stage="diagnosis",
            details={"categories": ["PRIVACY_OUTPUT_INVALID"]},
        )
    _scan_and_reject(hooks.scan_categories, sanitized_input, pass_name="pre-package")

    doctor_summary, issues = _doctor_summary(sanitized_input.get("doctor") or {})
    events, error_issues = _error_event(sanitized_input.get("error"))
    issues.update(error_issues)
    report_summary, fingerprint = _report_summary(sanitized_input.get("report"))
    bundle: dict[str, Any] = {
        "schemaVersion": DIAGNOSTIC_SCHEMA_VERSION,
        "tool": {"version": __version__, "pipelineRevision": PIPELINE_REVISION},
        "doctor": doctor_summary,
        "events": events,
        "configFingerprint": fingerprint,
        "report": report_summary,
        "recommendations": recommendations_for(issues),
        "safety": {
            "contentPolicy": "metadata-only",
            "privacyPasses": 2,
            "secretScan": "passed",
            "maxBytes": max_bytes,
            "networkPolicy": offline_policy(),
        },
    }
    final_bundle = _redact_or_reject(hooks.redact, bundle, pass_name="post-package")
    if not isinstance(final_bundle, dict):
        raise DiagnosticSafetyError(
            "Final privacy redaction returned an invalid structure",
            stage="diagnosis",
            details={"categories": ["PRIVACY_OUTPUT_INVALID"]},
        )
    validate_diagnostic_bundle(final_bundle, scanner=hooks.scan_categories, max_bytes=max_bytes)
    return final_bundle
