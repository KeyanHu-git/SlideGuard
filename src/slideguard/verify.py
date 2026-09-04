from __future__ import annotations

import json
import os
import re
from pathlib import Path, PurePosixPath

from .errors import InputError
from .model import Finding, Severity, Verdict
from .util import native_long_path, sha256_file


MAX_MANIFEST_BYTES = 8 * 1024 * 1024
MAX_PACKAGE_FILES = 100_000
CHECKSUM_LINE = re.compile(r"^([0-9a-f]{64})  ([^\r\n]+)$")


def _finding(code: str, ok: bool, message: str, *, actual=None, expected=None) -> Finding:
    return Finding(
        code=code,
        status=Verdict.PASS if ok else Verdict.FAIL,
        severity=Severity.INFO if ok else Severity.ERROR,
        message=message,
        validator="package-integrity@1.1",
        actual=actual,
        expected=expected,
    )


def _safe_package_path(root: Path, value: object) -> tuple[Path, str]:
    if not isinstance(value, str) or not value or "\\" in value or ":" in value:
        raise InputError("Package paths must be non-empty POSIX relative paths")
    logical = PurePosixPath(value)
    if logical.is_absolute() or any(part in {"", ".", ".."} for part in logical.parts):
        raise InputError("Package path escapes its root")
    canonical = logical.as_posix().casefold()
    target = (root / Path(*logical.parts)).resolve()
    if target == root or root not in target.parents:
        raise InputError("Package path escapes its root")
    return target, canonical


def _package_files(root: Path) -> tuple[dict[str, Path], bool]:
    files: dict[str, Path] = {}
    unsafe_link = False
    for current, directories, names in os.walk(root, followlinks=False):
        current_path = Path(current)
        kept = []
        for name in directories:
            candidate = current_path / name
            if candidate.is_symlink():
                unsafe_link = True
            else:
                kept.append(name)
        directories[:] = kept
        for name in names:
            candidate = current_path / name
            if candidate.is_symlink():
                unsafe_link = True
                continue
            relative = candidate.relative_to(root).as_posix()
            files[relative.casefold()] = candidate
            if len(files) > MAX_PACKAGE_FILES:
                raise InputError(f"Package contains more than {MAX_PACKAGE_FILES} files")
    return files, unsafe_link


def verify_package(manifest_path: Path) -> tuple[Verdict, list[Finding]]:
    manifest_path = manifest_path.resolve()
    root = manifest_path.parent.resolve()
    if not manifest_path.is_file() or manifest_path.stat().st_size > MAX_MANIFEST_BYTES:
        raise InputError("Manifest is missing or exceeds the size limit")
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise InputError("Manifest is not valid UTF-8 JSON") from exc
    artifacts = manifest.get("artifacts") if isinstance(manifest, dict) else None
    if not isinstance(artifacts, list):
        raise InputError("Manifest artifacts must be an array")

    findings: list[Finding] = []
    seen_artifacts: set[str] = set()
    for artifact in artifacts:
        if not isinstance(artifact, dict):
            findings.append(_finding("PACKAGE_MANIFEST_ENTRY", False, "Artifact entry is not an object"))
            continue
        display = artifact.get("path")
        try:
            path, canonical = _safe_package_path(root, display)
        except InputError:
            findings.append(_finding("PACKAGE_PATH_SAFE", False, "Artifact path is unsafe"))
            continue
        if canonical in seen_artifacts:
            findings.append(_finding("PACKAGE_PATH_UNIQUE", False, f"Duplicate artifact path: {display}"))
            continue
        seen_artifacts.add(canonical)
        exists = os.path.isfile(native_long_path(path)) and not path.is_symlink()
        findings.append(_finding(
            "PACKAGE_ARTIFACT_EXISTS", exists,
            f"Artifact exists: {display}" if exists else f"Artifact is missing: {display}",
            actual=exists, expected=True,
        ))
        if exists:
            actual = sha256_file(path)
            expected = artifact.get("sha256")
            findings.append(_finding(
                "PACKAGE_CHECKSUM", actual == expected,
                f"Checksum verified: {display}" if actual == expected else f"Checksum mismatch: {display}",
                actual=actual, expected=expected,
            ))

    checksum_path = root / "checksums.sha256"
    if not checksum_path.is_file() or checksum_path.is_symlink():
        findings.append(_finding("PACKAGE_CHECKSUM_MANIFEST", False, "checksums.sha256 is missing or unsafe"))
    else:
        expected_files: dict[str, tuple[Path, str]] = {}
        malformed = False
        try:
            lines = checksum_path.read_text(encoding="ascii", errors="strict").splitlines()
        except (OSError, UnicodeError):
            lines = []
            malformed = True
        for line in lines:
            match = CHECKSUM_LINE.fullmatch(line)
            if not match:
                malformed = True
                continue
            digest, value = match.groups()
            try:
                target, canonical = _safe_package_path(root, value)
            except InputError:
                malformed = True
                continue
            if canonical in expected_files or canonical == "checksums.sha256":
                malformed = True
                continue
            expected_files[canonical] = (target, digest)
        findings.append(_finding(
            "PACKAGE_CHECKSUM_MANIFEST", not malformed,
            "Checksum manifest is well formed" if not malformed else "Checksum manifest contains an unsafe, duplicate or malformed entry",
        ))
        actual_files, unsafe_link = _package_files(root)
        actual_files.pop("checksums.sha256", None)
        findings.append(_finding(
            "PACKAGE_NO_LINKS", not unsafe_link,
            "Package contains no symbolic-link escape" if not unsafe_link else "Package contains a symbolic link or junction entry",
        ))
        exact_set = set(expected_files) == set(actual_files)
        findings.append(_finding(
            "PACKAGE_FILE_SET", exact_set,
            "Checksum manifest covers every package file" if exact_set else "Checksum manifest does not exactly match the package file set",
            actual=len(actual_files), expected=len(expected_files),
        ))
        for canonical, (path, expected) in expected_files.items():
            if canonical not in actual_files or path.is_symlink() or not path.is_file():
                continue
            actual = sha256_file(path)
            findings.append(_finding(
                "PACKAGE_FILE_CHECKSUM", actual == expected,
                f"Package checksum verified: {path.relative_to(root).as_posix()}" if actual == expected else "Package file checksum mismatch",
                actual=actual, expected=expected,
            ))

    verdict = Verdict.FAIL if any(item.status == Verdict.FAIL for item in findings) else Verdict.PASS
    return verdict, findings
