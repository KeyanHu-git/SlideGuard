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


def test_export_preflight_can_defer_powerpoint_to_the_real_export(tmp_path, monkeypatch):
    monkeypatch.setattr("slideguard.util.require_executable", lambda name: f"C:/tools/{name}.exe")
    monkeypatch.setattr("slideguard.engine.svg_renderer_info", lambda: {"backend": "test"})

    def unexpected_probe(_root):
        raise AssertionError("the export preflight must not cold-start PowerPoint")

    monkeypatch.setattr("slideguard.engine.probe", unexpected_probe)

    result = doctor(tmp_path, probe_powerpoint=False)

    assert result["ok"] is True
    assert result["powerpoint"] is None
    assert result["powerpointProbe"] == "deferred-to-export"
