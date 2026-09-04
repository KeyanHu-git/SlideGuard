from __future__ import annotations

from slideguard.engine import doctor


def test_doctor_reports_contract_and_qa_dependency_versions(tmp_path, monkeypatch):
    monkeypatch.setattr("slideguard.util.require_executable", lambda name: f"C:/tools/{name}.exe")
    monkeypatch.setattr("slideguard.engine.svg_renderer_info", lambda: {"backend": "test"})
    monkeypatch.setattr("slideguard.engine.probe", lambda _root: {"version": "16.0", "build": "test"})

    result = doctor(tmp_path)

    assert result["ok"] is True
    assert result["pythonPackages"]["jsonschema"] != ""
    assert result["pythonPackages"]["skimage"] != ""
