from __future__ import annotations

import copy
import hashlib
import json
from dataclasses import dataclass
from importlib import resources
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator, FormatChecker
from jsonschema.exceptions import SchemaError
from referencing import Registry, Resource

from . import PIPELINE_REVISION, __version__
from .engine import ExportOptions
from .errors import BudgetError, EnvironmentError, ExportError, FidelityError, InputError, SlideGuardError
from .geometry import NormalizedRect, canonical_float
from .model import JobReport
from .ooxml import PptxPackage
from .util import parse_slides, sha256_file, stable_json


SCHEMA_VERSION = "1.0"

CAPABILITIES = {
    "vectorShapes": True,
    "originalRasterPreservation": True,
    "rasterToVector": False,
    "manualCrop": True,
    "perEdgeExpansion": True,
    "transparentSvgCanvas": True,
}


def emergency_result(error: BaseException) -> dict[str, Any]:
    """Last-resort result that does not depend on bundled schemas."""
    return {
        "schemaVersion": SCHEMA_VERSION,
        "status": "failed",
        "exitCode": 70,
        "toolVersion": __version__,
        "pipelineRevision": PIPELINE_REVISION,
        "configFingerprint": None,
        "effectiveSlides": [],
        "source": None,
        "output": None,
        "jobId": None,
        "verdict": None,
        "artifacts": [],
        "capabilities": dict(CAPABILITIES),
        "error": {
            "code": "INTERNAL_ERROR",
            "message": str(error) or type(error).__name__,
            "exitCode": 70,
            "stage": "result-serialization",
            "details": {"exceptionType": type(error).__name__, "fallback": True},
        },
    }

DEFAULT_REQUEST: dict[str, Any] = {
    "schemaVersion": SCHEMA_VERSION,
    "slides": "1",
    "crop": {"mode": "auto", "expandPercent": 0.0, "paddingPx": 16},
    "quality": {
        "referenceWidth": 4000,
        "pdfMaxBytes": None,
        "pdfMaxImageDimension": None,
        "pdfJpegQuality": 95,
        "svgMaxBytes": None,
        "dpis": [72, 96, 120, 144, 192, 300, 600],
        "svgWidths": [640, 1600, 3840],
    },
    "behavior": {"strict": True, "dryRun": False, "progress": "none"},
}


@dataclass(frozen=True, slots=True)
class PreparedRequest:
    raw: dict[str, Any]
    task_id: str | None
    source: Path
    source_sha256: str
    output_root: Path | None
    effective_slides: tuple[int, ...]
    options: ExportOptions
    dry_run: bool
    progress: str
    config_fingerprint: str


def _schema_resource(name: str):
    return resources.files("slideguard").joinpath("schemas", name)


def load_schema(name: str) -> dict[str, Any]:
    try:
        return json.loads(_schema_resource(name).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"Bundled schema is unavailable or invalid: {name}") from exc


def _validator(name: str) -> Draft202012Validator:
    schema = load_schema(name)
    try:
        Draft202012Validator.check_schema(schema)
    except SchemaError as exc:
        raise RuntimeError(f"Bundled schema failed self-validation: {name}") from exc
    # Register bundled resources explicitly so public contracts remain offline.
    registry = Registry()
    schema_directory = resources.files("slideguard").joinpath("schemas")
    for item in schema_directory.iterdir():
        if not item.name.endswith(".json"):
            continue
        bundled = json.loads(item.read_text(encoding="utf-8"))
        resource = Resource.from_contents(bundled)
        if resource.id():
            registry = registry.with_resource(resource.id(), resource)
    return Draft202012Validator(schema, registry=registry, format_checker=FormatChecker())


def _json_pointer(path: Any) -> str:
    parts = []
    for item in path:
        value = str(item).replace("~", "~0").replace("/", "~1")
        parts.append(value)
    return "/" + "/".join(parts) if parts else "/"


def validate_document(document: Any, schema_name: str) -> None:
    errors = sorted(_validator(schema_name).iter_errors(document), key=lambda item: list(item.absolute_path))
    if not errors:
        return
    violations = [
        {"path": _json_pointer(item.absolute_path), "message": item.message}
        for item in errors[:50]
    ]
    raise InputError(
        f"JSON does not satisfy {schema_name}",
        stage="validation",
        details={"schema": schema_name, "violations": violations},
    )


def _deep_defaults(document: dict[str, Any]) -> dict[str, Any]:
    result = copy.deepcopy(DEFAULT_REQUEST)
    for key in ("schemaVersion", "taskId", "input", "slides", "outputRoot"):
        if key in document:
            result[key] = document[key]
    for group in ("crop", "quality", "behavior"):
        result[group].update(document.get(group, {}))
    return result


def _resolve_path(value: str, base_dir: Path) -> Path:
    path = Path(value).expanduser()
    if not path.is_absolute():
        path = base_dir / path
    return path.resolve()


def _bounds_tuple(bounds: dict[str, Any] | None) -> tuple[float, float, float, float] | None:
    if bounds is None:
        return None
    values = tuple(float(bounds[name]) for name in ("left", "top", "right", "bottom"))
    try:
        return NormalizedRect.from_percent(values).to_percent()
    except InputError as exc:
        raise InputError(
            "Crop bounds must satisfy 0 <= left < right <= 100 and 0 <= top < bottom <= 100",
            stage="validation",
            details={"path": "/crop/boundsPercent", "actual": bounds},
        ) from exc


def _edges_tuple(value: float | int | dict[str, Any]) -> tuple[float, float, float, float]:
    if isinstance(value, dict):
        return tuple(canonical_float(value[name]) for name in ("left", "top", "right", "bottom"))
    edge = canonical_float(value)
    return edge, edge, edge, edge


def _content_configuration(options: ExportOptions, effective_slides: tuple[int, ...]) -> dict[str, Any]:
    """Return only values that can change produced pixels, vectors, or validation."""
    return {
        "schemaVersion": SCHEMA_VERSION,
        "slides": list(effective_slides),
        "paddingPx": options.padding_px,
        "cropPercent": list(options.crop_percent) if options.crop_percent else None,
        "expandPercent": list(options.expand_percent),
        "referenceWidth": options.reference_width,
        "pdfMaxBytes": options.pdf_max_bytes,
        "pdfMaxImageDimension": options.pdf_max_image_dimension,
        "pdfJpegQuality": options.pdf_jpeg_quality,
        "svgMaxBytes": options.svg_max_bytes,
        "dpis": list(options.dpis),
        "svgWidths": list(options.svg_widths),
        "pipelineRevision": PIPELINE_REVISION,
    }


def prepare_request(document: Any, *, base_dir: Path | None = None) -> PreparedRequest:
    """Validate and normalize a machine request before PowerPoint is started."""
    validate_document(document, "export-request.schema.json")
    normalized = _deep_defaults(document)
    base = (base_dir or Path.cwd()).resolve()
    source = _resolve_path(normalized["input"], base)
    output_root = _resolve_path(normalized["outputRoot"], base) if normalized.get("outputRoot") else None

    package = PptxPackage.open(source)
    effective_slides = tuple(parse_slides(normalized["slides"], package.slide_count))
    crop = normalized["crop"]
    crop_percent = _bounds_tuple(crop.get("boundsPercent"))
    expand_percent = _edges_tuple(crop["expandPercent"])
    quality = normalized["quality"]
    behavior = normalized["behavior"]
    options = ExportOptions(
        slides=normalized["slides"],
        output_root=output_root,
        padding_px=int(crop["paddingPx"]),
        crop_percent=crop_percent,
        expand_percent=expand_percent,
        reference_width=int(quality["referenceWidth"]),
        pdf_max_bytes=quality["pdfMaxBytes"],
        pdf_max_image_dimension=quality["pdfMaxImageDimension"],
        pdf_jpeg_quality=int(quality["pdfJpegQuality"]),
        svg_max_bytes=quality["svgMaxBytes"],
        dpis=tuple(int(item) for item in quality["dpis"]),
        svg_widths=tuple(int(item) for item in quality["svgWidths"]),
        strict=bool(behavior["strict"]),
    )
    canonical = _content_configuration(options, effective_slides)
    fingerprint = "sha256:" + hashlib.sha256(stable_json(canonical).encode("utf-8")).hexdigest()
    return PreparedRequest(
        raw=normalized,
        task_id=normalized.get("taskId"),
        source=source,
        source_sha256=sha256_file(source),
        output_root=output_root,
        effective_slides=effective_slides,
        options=options,
        dry_run=bool(behavior["dryRun"]),
        progress=behavior["progress"],
        config_fingerprint=fingerprint,
    )


def _result_base(prepared: PreparedRequest | None, task_id: str | None = None) -> dict[str, Any]:
    result: dict[str, Any] = {
        "schemaVersion": SCHEMA_VERSION,
        "status": "failed",
        "exitCode": 70,
        "toolVersion": __version__,
        "pipelineRevision": PIPELINE_REVISION,
        "configFingerprint": prepared.config_fingerprint if prepared else None,
        "effectiveSlides": list(prepared.effective_slides) if prepared else [],
        "source": (
            {"path": str(prepared.source), "sha256": prepared.source_sha256}
            if prepared else None
        ),
        "output": None,
        "jobId": None,
        "verdict": None,
        "artifacts": [],
        "capabilities": dict(CAPABILITIES),
        "error": None,
    }
    resolved_task_id = prepared.task_id if prepared else task_id
    if resolved_task_id:
        result["taskId"] = resolved_task_id
    return result


def validated_result(prepared: PreparedRequest) -> dict[str, Any]:
    result = _result_base(prepared)
    result.update({"status": "validated", "exitCode": 0})
    validate_document(result, "export-result.schema.json")
    return result


def succeeded_result(prepared: PreparedRequest, package_path: Path, report: JobReport) -> dict[str, Any]:
    result = _result_base(prepared)
    result.update(
        {
            "status": "succeeded",
            "exitCode": 0 if report.verdict.value != "FAIL" else 50,
            "jobId": report.job_id,
            "verdict": report.verdict.value,
            "output": {
                "packagePath": str(package_path.resolve()),
                "manifestPath": "manifest.json",
                "reportPath": "qa-report.json",
            },
            "artifacts": [
                {
                    "kind": item.kind,
                    "relativePath": item.path,
                    "sha256": item.sha256,
                    "bytes": item.bytes,
                    "slide": item.slide,
                }
                for item in report.artifacts
            ],
        }
    )
    result["source"] = {"path": report.source_path, "sha256": report.source_sha256_before}
    validate_document(result, "export-result.schema.json")
    return result


def failed_result(
    error: BaseException,
    *,
    prepared: PreparedRequest | None = None,
    task_id: str | None = None,
) -> dict[str, Any]:
    result = _result_base(prepared, task_id)
    if isinstance(error, SlideGuardError):
        code = error.code
        exit_code = error.exit_code
        default_stage = "execution"
        if isinstance(error, InputError):
            default_stage = "validation"
        elif isinstance(error, EnvironmentError):
            default_stage = "environment"
        elif isinstance(error, BudgetError):
            default_stage = "budget"
        elif isinstance(error, FidelityError):
            default_stage = "fidelity"
        elif isinstance(error, ExportError):
            default_stage = "export"
        stage = error.stage or default_stage
        details = error.details
    else:
        code = "INTERNAL_ERROR"
        exit_code = 70
        stage = "internal"
        details = {"exceptionType": type(error).__name__}
    result.update(
        {
            "status": "failed",
            "exitCode": exit_code,
            "error": {
                "code": code,
                "message": str(error) or type(error).__name__,
                "exitCode": exit_code,
                "stage": stage,
                "details": details,
            },
        }
    )
    validate_document(result, "export-result.schema.json")
    return result


def load_request(stream_text: str) -> dict[str, Any]:
    def reject_constant(value: str) -> None:
        raise ValueError(f"Non-standard numeric constant is not allowed: {value}")

    def reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in pairs:
            if key in result:
                raise ValueError(f"Duplicate JSON key is not allowed: {key}")
            result[key] = value
        return result

    try:
        document = json.loads(
            stream_text,
            parse_constant=reject_constant,
            object_pairs_hook=reject_duplicate_keys,
        )
    except json.JSONDecodeError as exc:
        raise InputError(
            "Request is not valid JSON",
            stage="validation",
            details={"line": exc.lineno, "column": exc.colno, "message": exc.msg},
        ) from exc
    except ValueError as exc:
        raise InputError(
            "Request is not strict JSON",
            stage="validation",
            details={"message": str(exc)},
        ) from exc
    if not isinstance(document, dict):
        raise InputError(
            "Request root must be a JSON object",
            stage="validation",
            details={"path": "/"},
        )
    return document
