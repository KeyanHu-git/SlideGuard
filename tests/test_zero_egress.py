from __future__ import annotations

import json
import socket
import subprocess
import sys
import zipfile
from pathlib import Path
from types import SimpleNamespace

import pytest

import slideguard.cli as cli
import slideguard.fixtures as fixtures
import slideguard.gui_launcher as gui_launcher
from slideguard.batch import BatchService
from slideguard.contracts import prepare_request, validated_result
from slideguard.engine import doctor
from slideguard.model import Verdict
from slideguard.offline import offline_policy
from slideguard.util import checksum_lines, sha256_file


ROOT = Path(__file__).parents[1]


def _pptx(path: Path) -> Path:
    presentation = b'''<p:presentation xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships" xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main"><p:sldIdLst><p:sldId id="256" r:id="rId1"/></p:sldIdLst><p:sldSz cx="12192000" cy="6858000"/></p:presentation>'''
    relationships = b'''<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="slide" Target="slides/slide1.xml"/></Relationships>'''
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("ppt/presentation.xml", presentation)
        archive.writestr("ppt/_rels/presentation.xml.rels", relationships)
        archive.writestr(
            "ppt/slides/slide1.xml",
            "<p:sld xmlns:p='http://schemas.openxmlformats.org/presentationml/2006/main'/>",
        )
    return path


def _install_network_trap(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    attempts: list[str] = []

    def deny(name: str):
        def blocked(*_args, **_kwargs):
            attempts.append(name)
            raise AssertionError(f"network operation is forbidden by the test harness: {name}")

        return blocked

    for name in ("connect", "connect_ex", "sendto", "sendmsg"):
        if hasattr(socket.socket, name):
            monkeypatch.setattr(socket.socket, name, deny(f"socket.{name}"))
    for name in ("create_connection", "getaddrinfo", "gethostbyname", "gethostbyname_ex"):
        monkeypatch.setattr(socket, name, deny(f"socket.{name}"))
    return attempts


def _doctor_document() -> dict:
    return {
        "platform": {"system": "Windows", "release": "11", "machine": "AMD64"},
        "executables": {},
        "svgRenderer": {"backend": "test"},
        "powerpoint": None,
        "networkPolicy": offline_policy(),
        "ok": True,
        "errors": [],
    }


def test_static_runtime_and_dependency_audit_passes() -> None:
    completed = subprocess.run(
        [sys.executable, str(ROOT / "scripts" / "audit_zero_egress.py")],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    report = json.loads(completed.stdout)

    assert completed.returncode == 0
    assert report["verdict"] == "PASS"
    assert report["findings"] == []
    assert report["policy"] == {
        "mode": "offline-only",
        "telemetryEnabled": False,
        "automaticUploadsEnabled": False,
        "updateChecksEnabled": False,
    }


def test_doctor_and_diagnostic_bundle_are_machine_readable_offline_claims(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capfd: pytest.CaptureFixture[str],
) -> None:
    attempts = _install_network_trap(monkeypatch)
    monkeypatch.setattr("slideguard.util.require_executable", lambda name: f"C:/local/{name}.exe")
    monkeypatch.setattr("slideguard.engine.svg_renderer_info", lambda: {"backend": "test"})

    result = doctor(tmp_path / "doctor-work", probe_powerpoint=False)
    assert result["networkPolicy"] == offline_policy()
    assert result["networkPolicy"]["telemetryEnabled"] is False

    doctor_path = tmp_path / "doctor.json"
    doctor_path.write_text(json.dumps(result), encoding="utf-8")
    assert cli.main(["diagnose", "--consent", "--doctor", str(doctor_path)]) == 0
    diagnostic = json.loads(capfd.readouterr().out)
    assert diagnostic["safety"]["networkPolicy"] == offline_policy()
    assert attempts == []


def test_job_batch_and_application_dry_run_make_no_network_attempt(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capfd: pytest.CaptureFixture[str],
) -> None:
    attempts = _install_network_trap(monkeypatch)
    source = _pptx(tmp_path / "figure.pptx")
    job = {
        "schemaVersion": "1.0",
        "input": source.name,
        "behavior": {"dryRun": True},
    }
    request_path = tmp_path / "request.json"
    request_path.write_text(json.dumps(job), encoding="utf-8")

    assert cli.main(["job", str(request_path)]) == 0
    assert json.loads(capfd.readouterr().out)["status"] == "validated"

    batch = {"schemaVersion": "1.0", "jobs": [job], "behavior": {"strategy": "continue"}}
    batch_result = BatchService().execute(batch, base_dir=tmp_path)
    assert batch_result["counts"]["succeeded"] == 1
    assert attempts == []


def test_remaining_cli_and_gui_dispatch_paths_make_no_network_attempt(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capfd: pytest.CaptureFixture[str],
) -> None:
    attempts = _install_network_trap(monkeypatch)
    source = _pptx(tmp_path / "figure.pptx")

    monkeypatch.setattr(cli, "doctor", lambda: _doctor_document())
    assert cli.main(["doctor", "--json"]) == 0
    assert json.loads(capfd.readouterr().out)["networkPolicy"] == offline_policy()

    class FakeExportService:
        def execute(self, document, *, base_dir, event_sink=None):
            document["behavior"]["dryRun"] = True
            return validated_result(prepare_request(document, base_dir=base_dir))

    monkeypatch.setattr(cli, "ExportService", FakeExportService)
    assert cli.main(["export", str(source), "--json"]) == 0
    assert json.loads(capfd.readouterr().out)["status"] == "validated"

    package = tmp_path / "package"
    package.mkdir()
    artifact = package / "artifact.txt"
    artifact.write_text("local-only", encoding="utf-8")
    manifest = package / "manifest.json"
    manifest.write_text(
        json.dumps({"artifacts": [{"path": artifact.name, "sha256": sha256_file(artifact)}]}),
        encoding="utf-8",
    )
    (package / "checksums.sha256").write_text(
        checksum_lines([artifact, manifest], package), encoding="utf-8",
    )
    assert cli.main(["verify", str(manifest)]) == 0
    assert json.loads(capfd.readouterr().out)["verdict"] == "PASS"

    monkeypatch.setattr(fixtures, "build_core_fixture", lambda output: {"output": str(output)})
    assert cli.main(["fixtures", "--out", str(tmp_path / "fixtures")]) == 0
    assert json.loads(capfd.readouterr().out)["output"].endswith("fixtures")

    fake_gui = SimpleNamespace(run_gui=lambda _source: 0)
    monkeypatch.setitem(sys.modules, "slideguard.gui", fake_gui)
    assert cli.main(["gui", str(source)]) == 0
    monkeypatch.setattr(gui_launcher.importlib, "import_module", lambda _name: fake_gui)
    monkeypatch.setattr(sys, "argv", ["slideguard-gui", str(source)])
    assert gui_launcher.main() == 0

    report = SimpleNamespace(verdict=Verdict.PASS)
    monkeypatch.setattr(cli, "export_job", lambda *_args, **_kwargs: (tmp_path / "out", report))
    assert cli.main(["export", str(source)]) == 0
    capfd.readouterr()
    assert attempts == []
