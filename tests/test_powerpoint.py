from pathlib import Path

from slideguard.powerpoint import preview_reference


def test_preview_reference_requests_png_only(tmp_path: Path, monkeypatch):
    captured = {}

    def fake_invoke(job, work_dir, timeout):
        captured.update(job)
        return {
            "export": {
                "referencePng": str(work_dir / "powerpoint-preview.png"),
                "referenceWidth": 1600,
                "referenceHeight": 900,
            },
            "powerpoint": {"version": "test"},
        }

    monkeypatch.setattr("slideguard.powerpoint.invoke", fake_invoke)
    result = preview_reference(tmp_path / "figure.pptx", 2, tmp_path / "preview")

    assert captured["mode"] == "preview"
    assert captured["slide"] == 2
    assert "nativePdf" not in captured
    assert result["referenceWidth"] == 1600
