from __future__ import annotations

import json
import shutil
import subprocess
from importlib.resources import files
from pathlib import Path

from .errors import EnvironmentError, ExportError
from .util import write_json


def _powershell() -> str:
    executable = shutil.which("pwsh") or shutil.which("powershell")
    if not executable:
        raise EnvironmentError("PowerShell is required")
    return executable


def _worker() -> Path:
    return Path(str(files("slideguard").joinpath("resources/powerpoint_worker.ps1")))


def invoke(job: dict, work_dir: Path, timeout: int = 300) -> dict:
    work_dir.mkdir(parents=True, exist_ok=True)
    job_path = work_dir / "powerpoint-job.json"
    result_path = work_dir / "powerpoint-result.json"
    job = dict(job)
    job["resultPath"] = str(result_path)
    write_json(job_path, job)
    command = [
        _powershell(), "-NoLogo", "-NoProfile", "-NonInteractive",
        "-ExecutionPolicy", "Bypass", "-File", str(_worker()), "-JobJson", str(job_path),
    ]
    try:
        process = subprocess.run(command, capture_output=True, text=True, timeout=timeout, check=False)
    except subprocess.TimeoutExpired as exc:
        raise ExportError(f"PowerPoint worker timed out after {timeout}s") from exc
    if not result_path.exists():
        detail = process.stderr.strip() or process.stdout.strip()
        raise ExportError(f"PowerPoint worker returned no result: {detail}")
    result = json.loads(result_path.read_text(encoding="utf-8-sig"))
    if process.returncode or not result.get("ok"):
        error = result.get("error") or {}
        raise ExportError(error.get("message") or "PowerPoint export failed")
    return result


def probe(work_dir: Path) -> dict:
    return invoke({"mode": "probe"}, work_dir, timeout=60)["powerpoint"]


def export_reference(
    pptx: Path,
    slide: int,
    work_dir: Path,
    reference_width: int = 4000,
    timeout: int = 300,
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
    )
    return {**result["export"], "powerpoint": result["powerpoint"]}

