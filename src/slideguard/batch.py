from __future__ import annotations

import copy
import hashlib
import json
import os
import time
import uuid
from pathlib import Path
from typing import Any, Callable

from . import PIPELINE_REVISION, __version__
from .application import EventSink, ExportService
from .contracts import (
    PreparedRequest,
    emergency_result,
    failed_result,
    prepare_request,
    validate_document,
)
from .errors import InputError
from .model import Verdict
from .util import ensure_within, native_long_path, sha256_file, stable_json
from .verify import verify_package


BATCH_DEFAULTS = {
    "strategy": "continue",
    "maxAttempts": 1,
    "retryDelayMs": 0,
    "reuseExisting": True,
}


class IdempotencyConflictError(InputError):
    code = "IDEMPOTENCY_CONFLICT"
    exit_code = 31


class PublishedPackageError(InputError):
    code = "PUBLISHED_PACKAGE_INVALID"
    exit_code = 32


def batch_emergency_result(error: BaseException) -> dict[str, Any]:
    """Small fallback that does not depend on a bundled schema."""
    return {
        "schemaVersion": "1.0",
        "status": "failed",
        "exitCode": 70,
        "toolVersion": __version__,
        "pipelineRevision": PIPELINE_REVISION,
        "strategy": "continue",
        "total": 0,
        "counts": {"succeeded": 0, "failed": 0, "skipped": 0, "reused": 0},
        "results": [],
        "error": {
            "code": "INTERNAL_ERROR",
            "message": str(error) or type(error).__name__,
            "exitCode": 70,
            "stage": "batch-result-serialization",
            "details": {"exceptionType": type(error).__name__, "fallback": True},
        },
    }


def batch_failed_result(
    error: BaseException,
    *,
    batch_id: str | None = None,
    strategy: str = "continue",
) -> dict[str, Any]:
    try:
        export_failure = failed_result(error)
        result: dict[str, Any] = {
            "schemaVersion": "1.0",
            "status": "failed",
            "exitCode": export_failure["exitCode"],
            "toolVersion": __version__,
            "pipelineRevision": PIPELINE_REVISION,
            "strategy": strategy,
            "total": 0,
            "counts": {"succeeded": 0, "failed": 0, "skipped": 0, "reused": 0},
            "results": [],
            "error": export_failure["error"],
        }
        if batch_id:
            result["batchId"] = batch_id
        validate_document(result, "batch-result.schema.json")
        return result
    except Exception as fallback_error:
        return batch_emergency_result(fallback_error)


def _behavior(document: dict[str, Any]) -> dict[str, Any]:
    value = dict(BATCH_DEFAULTS)
    supplied = document.get("behavior")
    if isinstance(supplied, dict):
        value.update(supplied)
    return value


def _identity(prepared: PreparedRequest) -> dict[str, str]:
    return {
        "sourceSha256": prepared.source_sha256,
        "configFingerprint": prepared.config_fingerprint,
        "pipelineRevision": PIPELINE_REVISION,
    }


def _idempotency_key(prepared: PreparedRequest) -> str:
    supplied = prepared.raw.get("idempotencyKey")
    if supplied:
        return str(supplied)
    digest = hashlib.sha256(stable_json(_identity(prepared)).encode("utf-8")).hexdigest()
    return f"sha256:{digest}"


def _output_root(prepared: PreparedRequest) -> Path:
    return (prepared.output_root or (prepared.source.parent / "slideguard-output")).resolve()


def _record_path(output_root: Path, key: str) -> Path:
    safe_name = hashlib.sha256(key.encode("utf-8")).hexdigest()
    return output_root / ".slideguard-cache" / f"{safe_name}.json"


def _load_record(path: Path) -> dict[str, Any] | None:
    try:
        with open(native_long_path(path), "r", encoding="utf-8") as stream:
            value = json.load(stream)
    except (OSError, UnicodeError, json.JSONDecodeError):
        return None
    return value if isinstance(value, dict) else None


def _path_inside(path: Path, root: Path) -> bool:
    try:
        ensure_within(path, root)
    except InputError:
        return False
    return True


def _verify_published_result(
    result: dict[str, Any],
    prepared: PreparedRequest,
    output_root: Path,
) -> bool:
    if result.get("status") != "succeeded" or result.get("exitCode") != 0:
        return False
    if result.get("configFingerprint") != prepared.config_fingerprint:
        return False
    source = result.get("source") or {}
    if source.get("sha256") != prepared.source_sha256:
        return False
    output = result.get("output") or {}
    try:
        package = Path(output["packagePath"]).resolve()
    except (KeyError, TypeError, OSError):
        return False
    if not os.path.isdir(native_long_path(package)) or not _path_inside(package, output_root):
        return False
    manifest_relative = output.get("manifestPath")
    if not isinstance(manifest_relative, str):
        return False
    manifest_path = (package / manifest_relative).resolve()
    if not _path_inside(manifest_path, package):
        return False
    try:
        with open(native_long_path(manifest_path), "r", encoding="utf-8") as stream:
            manifest = json.load(stream)
    except (OSError, UnicodeError, json.JSONDecodeError):
        return False
    if not isinstance(manifest, dict):
        return False
    if manifest.get("pipelineRevision") != PIPELINE_REVISION:
        return False
    if manifest.get("jobId") != result.get("jobId"):
        return False
    if (manifest.get("source") or {}).get("sha256") != prepared.source_sha256:
        return False
    for artifact in result.get("artifacts") or []:
        try:
            candidate = (package / artifact["relativePath"]).resolve()
            if not _path_inside(candidate, package) or not os.path.isfile(native_long_path(candidate)):
                return False
            if os.path.getsize(native_long_path(candidate)) != artifact["bytes"]:
                return False
            if sha256_file(candidate) != artifact["sha256"]:
                return False
        except (KeyError, TypeError, OSError):
            return False
    try:
        verdict, _findings = verify_package(manifest_path)
    except (InputError, OSError):
        return False
    return verdict == Verdict.PASS


def _cache_state(
    prepared: PreparedRequest,
    key: str,
) -> tuple[str, dict[str, Any] | None]:
    output_root = _output_root(prepared)
    record_path = _record_path(output_root, key)
    if not os.path.exists(native_long_path(record_path)):
        return "miss", None
    record = _load_record(record_path)
    if record is None:
        return "damaged", None
    if record.get("idempotencyKey") != key or record.get("identity") != _identity(prepared):
        return "conflict", record
    cached_result = record.get("result")
    if not isinstance(cached_result, dict):
        return "damaged", record
    if not _verify_published_result(cached_result, prepared, output_root):
        return "damaged", record
    try:
        if sha256_file(prepared.source) != prepared.source_sha256:
            return "damaged", record
    except OSError:
        return "damaged", record
    return "hit", cached_result


def _store_record(prepared: PreparedRequest, key: str, result: dict[str, Any]) -> None:
    output_root = _output_root(prepared)
    record_path = _record_path(output_root, key)
    os.makedirs(native_long_path(record_path.parent), exist_ok=True)
    payload = {
        "schemaVersion": "1.0",
        "idempotencyKey": key,
        "identity": _identity(prepared),
        "result": result,
    }
    temporary = record_path.with_name(f".sg-{uuid.uuid4().hex[:8]}.tmp")
    with open(native_long_path(temporary), "w", encoding="utf-8") as stream:
        json.dump(payload, stream, ensure_ascii=False, indent=2)
    os.replace(native_long_path(temporary), native_long_path(record_path))


def _current_task_result(result: dict[str, Any], task_id: str | None) -> dict[str, Any]:
    copied = copy.deepcopy(result)
    if task_id:
        copied["taskId"] = task_id
    else:
        copied.pop("taskId", None)
    validate_document(copied, "export-result.schema.json")
    return copied


def is_retryable_result(result: dict[str, Any]) -> bool:
    if result.get("status") != "failed":
        return False
    error = result.get("error") or {}
    code = error.get("code")
    if code in {
        "INPUT_INVALID",
        "IDEMPOTENCY_CONFLICT",
        "PUBLISHED_PACKAGE_INVALID",
        "BUDGET_UNSATISFIABLE",
        "FIDELITY_FAILED",
        "INTERNAL_ERROR",
    }:
        return False
    details = error.get("details") or {}
    if details.get("transient") is True and code in {"ENV_UNSATISFIED", "EXPORT_FAILED"}:
        return True
    return error.get("stage") in {"powerpoint-timeout", "external-process-timeout"}


def _attempt_entry(attempt: int, result: dict[str, Any], retryable: bool) -> dict[str, Any]:
    error = result.get("error") or {}
    return {
        "attempt": attempt,
        "status": result["status"],
        "exitCode": int(result["exitCode"]),
        "errorCode": error.get("code"),
        "retryable": retryable,
    }


class BatchService:
    """Sequential batch runner with per-item isolation and checked package reuse."""

    def __init__(
        self,
        export_service: ExportService | None = None,
        *,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self.export_service = export_service or ExportService()
        self.sleep = sleep

    def _run_item(
        self,
        document: dict[str, Any],
        *,
        base_dir: Path,
        behavior: dict[str, Any],
        event_sink: EventSink | None,
    ) -> dict[str, Any]:
        prepared: PreparedRequest | None = None
        key: str | None = None
        cache_state = "miss"
        try:
            prepared = prepare_request(document, base_dir=base_dir)
            key = _idempotency_key(prepared)
            if behavior["reuseExisting"] and not prepared.dry_run:
                cache_state, cached_result = _cache_state(prepared, key)
                if cache_state == "conflict":
                    conflict = IdempotencyConflictError(
                        "Idempotency key is already bound to different input content or export settings",
                        stage="idempotency",
                        details={"idempotencyKey": key},
                    )
                    result = failed_result(conflict, prepared=prepared)
                    return self._completed_item(document, key, result, [], False)
                if cache_state == "hit" and cached_result is not None:
                    result = _current_task_result(cached_result, prepared.task_id)
                    return self._completed_item(document, key, result, [], True)
        except Exception:
            # ExportService returns the public failure shape, including validation errors.
            prepared = None

        run_document = copy.deepcopy(document)
        if cache_state == "damaged" and prepared is not None:
            rerun_root = _output_root(prepared) / f".slideguard-rerun-{hashlib.sha256(key.encode('utf-8')).hexdigest()[:8]}-{uuid.uuid4().hex[:6]}"
            run_document["outputRoot"] = str(rerun_root)

        attempt_log = []
        result: dict[str, Any] | None = None
        retryable = False
        for attempt in range(1, int(behavior["maxAttempts"]) + 1):
            try:
                sink = event_sink if (document.get("behavior") or {}).get("progress") == "jsonl" else None
                result = self.export_service.execute(run_document, base_dir=base_dir, event_sink=sink)
            except Exception as exc:
                try:
                    result = failed_result(exc, prepared=prepared)
                except Exception as result_error:
                    result = emergency_result(result_error)
            retryable = is_retryable_result(result)
            attempt_log.append(_attempt_entry(attempt, result, retryable))
            if result["exitCode"] == 0 or not retryable or attempt >= int(behavior["maxAttempts"]):
                break
            delay_seconds = (int(behavior["retryDelayMs"]) * attempt) / 1000.0
            if delay_seconds:
                self.sleep(delay_seconds)

        assert result is not None
        if (
            behavior["reuseExisting"]
            and prepared is not None
            and key is not None
            and result.get("status") == "succeeded"
            and result.get("exitCode") == 0
        ):
            run_prepared = prepare_request(run_document, base_dir=base_dir)
            if not _verify_published_result(result, run_prepared, _output_root(run_prepared)):
                invalid = PublishedPackageError(
                    "Published package failed idempotency integrity checks",
                    stage="idempotency",
                    details={"idempotencyKey": key},
                )
                result = failed_result(invalid, prepared=prepared)
                retryable = False
                attempt_log[-1] = _attempt_entry(attempt_log[-1]["attempt"], result, False)
            else:
                _store_record(prepared, key, result)
        return self._completed_item(document, key, result, attempt_log, False, retryable)

    @staticmethod
    def _completed_item(
        document: dict[str, Any],
        key: str | None,
        result: dict[str, Any],
        attempt_log: list[dict[str, Any]],
        reused: bool,
        retryable: bool = False,
    ) -> dict[str, Any]:
        item: dict[str, Any] = {
            "itemIndex": 0,
            "status": "completed",
            "attempts": len(attempt_log),
            "attemptLog": attempt_log,
            "retryable": retryable,
            "reused": reused,
            "idempotencyKey": key,
            "result": result,
            "skipReason": None,
        }
        task_id = document.get("taskId")
        if isinstance(task_id, str):
            item["taskId"] = task_id
        return item

    def execute(
        self,
        document: dict[str, Any],
        *,
        base_dir: Path,
        event_sink: EventSink | None = None,
    ) -> dict[str, Any]:
        batch_id = document.get("batchId") if isinstance(document.get("batchId"), str) else None
        behavior = _behavior(document)
        try:
            validate_document(document, "batch-request.schema.json")
        except Exception as exc:
            return batch_failed_result(
                exc,
                batch_id=batch_id,
                strategy=str(behavior.get("strategy", "continue")),
            )

        jobs = document["jobs"]
        results: list[dict[str, Any]] = []
        stopped = False
        for index, job in enumerate(jobs):
            if stopped:
                item: dict[str, Any] = {
                    "itemIndex": index,
                    "status": "skipped",
                    "attempts": 0,
                    "attemptLog": [],
                    "retryable": False,
                    "reused": False,
                    "idempotencyKey": None,
                    "result": None,
                    "skipReason": "Skipped after an earlier item failed under fail-fast strategy",
                }
                if isinstance(job.get("taskId"), str):
                    item["taskId"] = job["taskId"]
            else:
                try:
                    item = self._run_item(
                        job,
                        base_dir=base_dir,
                        behavior=behavior,
                        event_sink=event_sink,
                    )
                except Exception as exc:
                    item_result = emergency_result(exc)
                    item = self._completed_item(job, None, item_result, [_attempt_entry(1, item_result, False)], False)
                item["itemIndex"] = index
                if behavior["strategy"] == "fail-fast" and item["result"]["exitCode"] != 0:
                    stopped = True
            results.append(item)

        completed = [item for item in results if item["status"] == "completed"]
        succeeded = sum(item["result"]["exitCode"] == 0 for item in completed)
        failed = len(completed) - succeeded
        skipped = len(results) - len(completed)
        reused = sum(bool(item["reused"]) for item in completed)
        if failed == 0 and skipped == 0:
            status = "succeeded"
        elif succeeded > 0:
            status = "partial"
        else:
            status = "failed"
        exit_code = max((int(item["result"]["exitCode"]) for item in completed), default=0)
        result = {
            "schemaVersion": "1.0",
            "status": status,
            "exitCode": exit_code,
            "toolVersion": __version__,
            "pipelineRevision": PIPELINE_REVISION,
            "strategy": behavior["strategy"],
            "total": len(jobs),
            "counts": {
                "succeeded": succeeded,
                "failed": failed,
                "skipped": skipped,
                "reused": reused,
            },
            "results": results,
            "error": None,
        }
        if batch_id:
            result["batchId"] = batch_id
        try:
            validate_document(result, "batch-result.schema.json")
            return result
        except Exception as exc:
            return batch_emergency_result(exc)
