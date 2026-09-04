from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import threading
import time
import uuid
from importlib.resources import files
from pathlib import Path
from typing import Any

from .cancellation import CancellationToken
from .errors import CancelledError, EnvironmentError, ExportError
from .util import write_json


def _powershell() -> str:
    executable = shutil.which("pwsh") or shutil.which("powershell")
    if not executable:
        raise EnvironmentError("PowerShell is required")
    return executable


def _worker() -> Path:
    return Path(str(files("slideguard").joinpath("resources/powerpoint_worker.ps1")))


def _pid_cleanup_worker() -> Path:
    return Path(str(files("slideguard").joinpath("resources/powerpoint_pid_cleanup.ps1")))


def _read_json(path: Path) -> dict[str, Any] | None:
    try:
        value = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return None
    return value if isinstance(value, dict) else None


def _request_cancel(path: Path, nonce: str) -> None:
    write_json(path, {"schemaVersion": "1.0", "nonce": nonce, "requestedAtUnix": time.time()})


def _proven_owned_process(
    state: dict[str, Any] | None,
    *,
    nonce: str,
    worker_pid: int,
) -> dict[str, Any] | None:
    if not state or state.get("nonce") != nonce or state.get("workerPid") != worker_pid:
        return None
    powerpoint = state.get("powerpoint")
    if not isinstance(powerpoint, dict) or powerpoint.get("ownership") != "slideguard-owned":
        return None
    pid = powerpoint.get("pid")
    parent_pid = powerpoint.get("parentPid")
    start_time = powerpoint.get("startTimeUtc")
    proof = powerpoint.get("proof")
    identity_method = proof.get("identityMethod") if isinstance(proof, dict) else None
    identity_proven = (
        (
            identity_method == "unique-automation-activation"
            and proof.get("automationCommandLine") is True
            and proof.get("uniqueActivationCandidate") is True
        )
        or (identity_method == "application-window" and proof.get("windowPidMatches") is True)
    )
    if (
        not isinstance(pid, int) or pid <= 0
        or not isinstance(start_time, str) or not start_time
        or not isinstance(proof, dict)
        or proof.get("absentBeforeActivation") is not True
        or proof.get("startedDuringActivation") is not True
        or not identity_proven
    ):
        return None
    return powerpoint


def _terminate_proven_owned_powerpoint(powerpoint: dict[str, Any]) -> dict[str, Any]:
    command = [
        _powershell(), "-NoLogo", "-NoProfile", "-NonInteractive",
        "-ExecutionPolicy", "Bypass", "-File", str(_pid_cleanup_worker()),
        "-ProcessId", str(powerpoint["pid"]),
        "-ExpectedParentPid", str(powerpoint["parentPid"]),
        "-ExpectedStartTimeUtc", str(powerpoint["startTimeUtc"]),
        "-IdentityMethod", str(powerpoint["proof"]["identityMethod"]),
    ]
    try:
        process = subprocess.run(command, capture_output=True, text=True, timeout=20, check=False)
    except Exception as exc:
        return {"status": "cleanup-check-failed", "reason": type(exc).__name__}
    try:
        result = json.loads(process.stdout.strip()) if process.stdout.strip() else {}
    except json.JSONDecodeError:
        result = {"status": "cleanup-check-failed", "reason": "invalid-cleanup-response"}
    if not isinstance(result, dict):
        result = {"status": "cleanup-check-failed", "reason": "invalid-cleanup-response"}
    result.setdefault("exitCode", process.returncode)
    return result


def _reap_in_background(process: subprocess.Popen[str]) -> None:
    def reap() -> None:
        try:
            process.communicate()
        except Exception:
            pass

    threading.Thread(target=reap, name="slideguard-powerpoint-reaper", daemon=True).start()


def _remove_owned_scratch(state: dict[str, Any] | None) -> dict[str, Any]:
    value = state.get("comScratch") if isinstance(state, dict) else None
    if not isinstance(value, str) or not value:
        return {"status": "not-recorded"}
    scratch = Path(value).resolve()
    temporary_value = os.environ.get("TEMP") or os.environ.get("TMP")
    if not temporary_value:
        return {"status": "not-removed-temp-unavailable"}
    temporary_root = Path(temporary_value).resolve()
    if (
        not temporary_root.is_dir()
        or scratch.parent != temporary_root
        or re.fullmatch(r"sg-[0-9a-fA-F]{8}", scratch.name) is None
    ):
        return {"status": "not-removed-unverified-path"}
    try:
        shutil.rmtree(scratch, ignore_errors=False) if scratch.exists() else None
    except OSError as exc:
        return {"status": "remove-failed", "reason": type(exc).__name__}
    return {"status": "removed" if not scratch.exists() else "remove-failed"}


def _timeout_details(
    process: subprocess.Popen[str],
    *,
    status_path: Path,
    cancel_path: Path,
    nonce: str,
    grace_seconds: float,
) -> dict[str, Any]:
    _request_cancel(cancel_path, nonce)
    cooperative_exit = False
    try:
        process.communicate(timeout=grace_seconds)
        cooperative_exit = True
    except subprocess.TimeoutExpired:
        pass

    state = _read_json(status_path)
    proven = _proven_owned_process(state, nonce=nonce, worker_pid=process.pid)
    cleanup: dict[str, Any]
    worker_disposition: str
    forced_cleanup_incomplete = False
    if proven is not None and not cooperative_exit:
        # The PowerShell worker is ours. Stop it first so it cannot issue another
        # COM call, then re-check every ownership fact before stopping one PID.
        try:
            process.terminate()
        except OSError:
            pass
        try:
            process.wait(timeout=3)
        except subprocess.TimeoutExpired:
            try:
                process.kill()
                process.wait(timeout=3)
            except OSError:
                pass
        worker_disposition = "terminated-owned-worker"
        cleanup = _terminate_proven_owned_powerpoint(proven)
        process_gone = cleanup.get("stopped") is True or cleanup.get("status") == "not-found"
        forced_cleanup_incomplete = not process_gone
        scratch_cleanup = _remove_owned_scratch(state) if process_gone else {"status": "not-attempted"}
    elif cooperative_exit:
        worker_disposition = "cooperative-exit"
        cleanup = {
            "status": "worker-cleanup-complete" if state and state.get("cleanupComplete") is True
            else "worker-cleanup-incomplete"
        }
        scratch_cleanup = {"status": "worker-cleanup"}
    else:
        # The COM server may be a user's existing PowerPoint process. Do not
        # terminate either it or its COM client; the worker will close only the
        # hidden read-only presentation after the blocking COM call returns.
        worker_disposition = "left-running-for-cooperative-cleanup"
        cleanup = {"status": "not-attempted-unproven-ownership"}
        scratch_cleanup = {"status": "deferred-to-worker"}
        _reap_in_background(process)

    powerpoint = state.get("powerpoint") if isinstance(state, dict) else None
    residual_risk = (
        worker_disposition == "left-running-for-cooperative-cleanup"
        or (cooperative_exit and (not state or state.get("cleanupComplete") is not True))
        or forced_cleanup_incomplete
    )
    return {
        "graceSeconds": grace_seconds,
        "cancelRequested": True,
        "workerPid": process.pid,
        "workerDisposition": worker_disposition,
        "powerpointOwnership": (
            powerpoint.get("ownership") if isinstance(powerpoint, dict) else "unknown"
        ),
        "powerpointCleanup": cleanup,
        "scratchCleanup": scratch_cleanup,
        "workerState": {
            "phase": state.get("phase"),
            "cleanupComplete": state.get("cleanupComplete", False),
            "cleanupErrors": state.get("cleanupErrors", []),
        } if isinstance(state, dict) else None,
        "residualRisk": residual_risk,
    }


def invoke(
    job: dict,
    work_dir: Path,
    timeout: int = 300,
    cancel_token: CancellationToken | None = None,
) -> dict:
    work_dir.mkdir(parents=True, exist_ok=True)
    job_path = work_dir / "powerpoint-job.json"
    result_path = work_dir / "powerpoint-result.json"
    status_path = work_dir / "powerpoint-worker-status.json"
    cancel_path = work_dir / "powerpoint-cancel.json"
    nonce = uuid.uuid4().hex
    job = dict(job)
    job["resultPath"] = str(result_path)
    job["statusPath"] = str(status_path)
    job["cancelPath"] = str(cancel_path)
    job["nonce"] = nonce
    write_json(job_path, job)
    command = [
        _powershell(), "-NoLogo", "-NoProfile", "-NonInteractive",
        "-ExecutionPolicy", "Bypass", "-File", str(_worker()), "-JobJson", str(job_path),
    ]
    process = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    deadline = time.monotonic() + timeout
    stdout = ""
    stderr = ""
    communicated = False
    while True:
        if cancel_token and cancel_token.is_cancelled:
            details = _timeout_details(
                process,
                status_path=status_path,
                cancel_path=cancel_path,
                nonce=nonce,
                grace_seconds=3.0,
            )
            details["requestedBy"] = "caller"
            raise CancelledError(
                "The PowerPoint operation was cancelled",
                stage="cancellation",
                details=details,
            )
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            break
        try:
            stdout, stderr = process.communicate(timeout=min(0.25, remaining))
            communicated = True
            break
        except subprocess.TimeoutExpired:
            continue
    if not communicated and process.poll() is None:
        details = _timeout_details(
            process,
            status_path=status_path,
            cancel_path=cancel_path,
            nonce=nonce,
            grace_seconds=3.0,
        )
        details["timeoutSeconds"] = timeout
        raise ExportError(
            f"PowerPoint worker timed out after {timeout}s",
            stage="export",
            details=details,
        )
    if not communicated:
        stdout, stderr = process.communicate()
    if not result_path.exists():
        detail = stderr.strip() or stdout.strip()
        raise ExportError(f"PowerPoint worker returned no result: {detail}", stage="export")
    result = json.loads(result_path.read_text(encoding="utf-8-sig"))
    if process.returncode or not result.get("ok"):
        error = result.get("error") or {}
        raise ExportError(error.get("message") or "PowerPoint export failed", stage="export")
    return result


def probe(work_dir: Path, cancel_token: CancellationToken | None = None) -> dict:
    return invoke(
        {"mode": "probe"}, work_dir, timeout=60, cancel_token=cancel_token,
    )["powerpoint"]


def export_reference(
    pptx: Path,
    slide: int,
    work_dir: Path,
    reference_width: int = 4000,
    timeout: int = 300,
    cancel_token: CancellationToken | None = None,
) -> dict:
    result = invoke(
        {
            "mode": "export",
            "pptxPath": str(pptx.resolve()),
            "slide": slide,
            "referenceWidth": reference_width,
            "nativePdf": str(work_dir / "powerpoint-native.pdf"),
            "referencePng": str(work_dir / "powerpoint-reference.png"),
        },
        work_dir,
        timeout=timeout,
        cancel_token=cancel_token,
    )
    return {**result["export"], "powerpoint": result["powerpoint"]}


def preview_reference(
    pptx: Path,
    slide: int,
    work_dir: Path,
    preview_width: int = 1600,
    timeout: int = 180,
) -> dict:
    """Render one PowerPoint-authored PNG without creating a PDF."""
    result = invoke(
        {
            "mode": "preview",
            "pptxPath": str(pptx.resolve()),
            "slide": slide,
            "referenceWidth": preview_width,
            "referencePng": str(work_dir / "powerpoint-preview.png"),
        },
        work_dir,
        timeout=timeout,
    )
    return {**result["export"], "powerpoint": result["powerpoint"]}
