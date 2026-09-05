from __future__ import annotations

import json
from pathlib import Path

from slideguard.model import Verdict
from slideguard.util import checksum_lines, sha256_file
from slideguard.verify import verify_package


def _package(root: Path) -> Path:
    root.mkdir()
    artifact = root / "figure.svg"
    artifact.write_text("<svg/>", encoding="utf-8")
    manifest = root / "manifest.json"
    manifest.write_text(json.dumps({"artifacts": [{"path": "figure.svg", "sha256": sha256_file(artifact)}]}), encoding="utf-8")
    report = root / "qa-report.json"
    report.write_text("{}", encoding="utf-8")
    files = [artifact, manifest, report]
    (root / "checksums.sha256").write_text(checksum_lines(files, root), encoding="ascii")
    return manifest


def test_verify_requires_exact_safe_checksum_file_set(tmp_path: Path):
    manifest = _package(tmp_path / "package")
    verdict, findings = verify_package(manifest)
    assert verdict == Verdict.PASS
    assert next(item for item in findings if item.code == "PACKAGE_FILE_SET").status == Verdict.PASS


def test_verify_rejects_manifest_path_traversal_without_reading_target(tmp_path: Path):
    manifest = _package(tmp_path / "package")
    data = json.loads(manifest.read_text(encoding="utf-8"))
    data["artifacts"].append({"path": "../secret.txt", "sha256": "0" * 64})
    manifest.write_text(json.dumps(data), encoding="utf-8")
    verdict, findings = verify_package(manifest)
    assert verdict == Verdict.FAIL
    assert any(item.code == "PACKAGE_PATH_SAFE" and item.status == Verdict.FAIL for item in findings)


def test_verify_rejects_checksum_traversal_and_duplicate_records(tmp_path: Path):
    manifest = _package(tmp_path / "package")
    checksum = manifest.parent / "checksums.sha256"
    line = checksum.read_text(encoding="ascii").splitlines()[0]
    checksum.write_text(f"{line}\n{line}\n{'0' * 64}  ../outside.txt\n", encoding="ascii")
    verdict, findings = verify_package(manifest)
    assert verdict == Verdict.FAIL
    assert any(item.code == "PACKAGE_CHECKSUM_MANIFEST" and item.status == Verdict.FAIL for item in findings)


def test_verify_fails_when_package_has_unlisted_file(tmp_path: Path):
    manifest = _package(tmp_path / "package")
    (manifest.parent / "unlisted.bin").write_bytes(b"x")
    verdict, findings = verify_package(manifest)
    assert verdict == Verdict.FAIL
    assert any(item.code == "PACKAGE_FILE_SET" and item.status == Verdict.FAIL for item in findings)
