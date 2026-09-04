from __future__ import annotations

import importlib.util
import json
import os
import re
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def load_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


portable_build = load_module("slideguard_portable_build", ROOT / "scripts" / "portable_build.py")
portable_entry = load_module("slideguard_portable_entry", ROOT / "packaging" / "portable_entry.py")


def test_manifest_and_checksum_rows_are_sorted_and_detect_changes(tmp_path):
    package = tmp_path / "SlideGuard"
    (package / "_internal").mkdir(parents=True)
    (package / "SlideGuard.exe").write_bytes(b"exe")
    (package / "_internal" / "z.dll").write_bytes(b"z")
    (package / "_internal" / "a.dll").write_bytes(b"a")

    first = portable_build.write_manifest(package, "0.test").read_bytes()
    second = portable_build.write_manifest(package, "0.test").read_bytes()
    sums = portable_build.write_sha256sums(package)

    assert first == second
    document = json.loads(first)
    assert [item["path"] for item in document["files"]] == [
        "_internal/a.dll",
        "_internal/z.dll",
        "SlideGuard.exe",
    ]
    assert "*MANIFEST.json" in sums.read_text(encoding="utf-8")
    assert portable_build.verify_sha256sums(package) == []

    (package / "_internal" / "a.dll").write_bytes(b"changed")
    assert portable_build.verify_sha256sums(package) == ["hash mismatch: _internal/a.dll"]

    (package / "unexpected.dll").write_bytes(b"extra")
    assert portable_build.verify_sha256sums(package) == [
        "hash mismatch: _internal/a.dll",
        "unlisted: unexpected.dll",
    ]


def test_checksum_verifier_rejects_paths_outside_package(tmp_path):
    package = tmp_path / "SlideGuard"
    package.mkdir()
    (package / "SHA256SUMS").write_text("0" * 64 + " *../outside.txt\n", encoding="utf-8")

    assert portable_build.verify_sha256sums(package) == ["unsafe path: ../outside.txt"]


def test_component_documents_separate_runtime_build_and_external(tmp_path):
    boundaries = {
        "bundled": {"runtimeDistributionRoots": ["Pillow"]},
        "buildOnlyDistributionRoots": [],
        "external": [
            {
                "name": "Microsoft PowerPoint",
                "required": True,
                "owner": "user-or-organization",
                "reason": "not bundled",
            }
        ],
    }

    sbom_path, notices_path = portable_build.write_component_documents(tmp_path, boundaries)

    sbom = json.loads(sbom_path.read_text(encoding="utf-8"))
    assert sbom["bomFormat"] == "CycloneDX"
    assert sbom["specVersion"] == "1.5"
    assert any(item["name"] == "Python" and item["scope"] == "required" for item in sbom["components"])
    assert any(item["name"].casefold() == "pillow" and item["scope"] == "required" for item in sbom["components"])
    assert any(
        item["name"] == "Microsoft PowerPoint"
        and item["scope"] == "required"
        and {prop["name"]: prop["value"] for prop in item["properties"]}["slideguard:bundled"] == "false"
        for item in sbom["components"]
    )
    assert "pillow" in notices_path.read_text(encoding="utf-8").casefold()


def test_portable_entry_adds_only_existing_private_tool_directories(tmp_path, monkeypatch):
    poppler = tmp_path / "external" / "poppler" / "Library" / "bin"
    chromium = tmp_path / "external" / "chromium"
    poppler.mkdir(parents=True)
    chromium.mkdir(parents=True)
    monkeypatch.setenv("PATH", "inherited")

    added = portable_entry.add_private_tool_paths(tmp_path)

    assert added == [poppler, chromium]
    assert os.environ["PATH"].split(os.pathsep) == [str(poppler), str(chromium), "inherited"]


def test_spec_collects_gui_dynamic_import_and_slideguard_resources():
    spec = (ROOT / "packaging" / "slideguard.spec").read_text(encoding="utf-8")
    assert '"slideguard.gui"' in spec
    assert '"resources/*.ps1"' in spec
    assert '"schemas/*.json"' in spec
    assert "copy_metadata(distribution)" in spec
    assert 'name="SlideGuard"' in spec


def test_portable_extra_pins_qt_and_build_script_runs_two_gui_checks():
    project = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    build_script = (ROOT / "scripts" / "portable-build.ps1").read_text(encoding="utf-8")
    assert '"PySide6==6.9.2"' in project
    assert "from PySide6 import QtCore" in build_script
    assert 'ArgumentList @("gui")' in build_script


def test_boundary_file_keeps_office_poppler_and_browser_external():
    boundaries = json.loads((ROOT / "packaging" / "component-boundaries.json").read_text(encoding="utf-8"))
    external = {item["name"]: item for item in boundaries["external"]}
    assert external["Microsoft PowerPoint"]["required"] is True
    assert external["Poppler command-line tools"]["required"] is True
    assert external["Chromium-family browser"]["required"] is False
    assert boundaries["bundled"]["pythonRuntime"] is True
    assert boundaries["bundled"]["embeddedBuildArtifacts"] == [
        {"distribution": "PyInstaller", "role": "bootloader embedded in SlideGuard.exe"}
    ]


def test_offline_smoke_script_never_invokes_development_tools():
    script = (ROOT / "scripts" / "portable-smoke.ps1").read_text(encoding="utf-8")
    executable_commands = [line.strip() for line in script.splitlines() if line.strip() and not line.lstrip().startswith("#")]
    forbidden = re.compile(r"(?:^|[;&|]\s*)(?:python|python3|py|pip|git)(?:\.exe)?(?:\s|$)", re.IGNORECASE)
    assert not any(forbidden.search(line) for line in executable_commands)


def test_portable_document_does_not_claim_clean_machine_completion():
    document = (ROOT / "docs" / "portable-package.md").read_text(encoding="utf-8")
    assert "尚未声称通过上述干净机器检查" in document
