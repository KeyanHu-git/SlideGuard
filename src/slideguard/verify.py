from __future__ import annotations

import json
import os
from pathlib import Path

from .model import Finding, Severity, Verdict
from .util import native_long_path, sha256_file


def verify_package(manifest_path: Path) -> tuple[Verdict, list[Finding]]:
    manifest_path = manifest_path.resolve()
    root = manifest_path.parent
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    findings = []
    for artifact in manifest.get("artifacts", []):
        path = root / artifact["path"]
        exists = os.path.isfile(native_long_path(path))
        findings.append(Finding(
            code="PACKAGE_ARTIFACT_EXISTS", status=Verdict.PASS if exists else Verdict.FAIL,
            severity=Severity.INFO if exists else Severity.ERROR,
            message=f"Artifact exists: {artifact['path']}" if exists else f"Artifact is missing: {artifact['path']}",
            validator="package-integrity@1.0", actual=exists, expected=True,
        ))
        if exists:
            actual = sha256_file(path)
            expected = artifact["sha256"]
            findings.append(Finding(
                code="PACKAGE_CHECKSUM", status=Verdict.PASS if actual == expected else Verdict.FAIL,
                severity=Severity.INFO if actual == expected else Severity.ERROR,
                message=f"Checksum verified: {artifact['path']}" if actual == expected else f"Checksum mismatch: {artifact['path']}",
                validator="package-integrity@1.0", actual=actual, expected=expected,
            ))
    verdict = Verdict.FAIL if any(item.status == Verdict.FAIL for item in findings) else Verdict.PASS
    return verdict, findings
