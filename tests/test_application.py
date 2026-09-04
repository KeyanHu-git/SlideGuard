from __future__ import annotations

import zipfile
from pathlib import Path

from slideguard.application import ExportService
from slideguard.cancellation import CancellationToken


def _pptx(path: Path) -> Path:
    presentation = b'''<p:presentation xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships" xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main"><p:sldIdLst><p:sldId id="256" r:id="rId1"/></p:sldIdLst><p:sldSz cx="12192000" cy="6858000"/></p:presentation>'''
    rels = b'''<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="slide" Target="slides/slide1.xml"/></Relationships>'''
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("ppt/presentation.xml", presentation)
        archive.writestr("ppt/_rels/presentation.xml.rels", rels)
        archive.writestr("ppt/slides/slide1.xml", "<p:sld xmlns:p='http://schemas.openxmlformats.org/presentationml/2006/main'/>")
    return path


def test_service_is_independent_of_cli_and_emits_validated_progress(tmp_path: Path):
    source = _pptx(tmp_path / "one.pptx")
    events = []
    result = ExportService().execute(
        {
            "schemaVersion": "1.0",
            "input": source.name,
            "behavior": {"dryRun": True, "progress": "jsonl"},
        },
        base_dir=tmp_path,
        event_sink=events.append,
    )

    assert result["status"] == "validated"
    assert [event["phase"] for event in events] == ["validation", "validation"]
    assert [event["sequence"] for event in events] == [0, 1]


def test_service_maps_unexpected_failures_without_tracebacks(tmp_path: Path, monkeypatch):
    source = _pptx(tmp_path / "one.pptx")

    def broken_export(*_args, **_kwargs):
        raise RuntimeError("synthetic secret-free failure")

    monkeypatch.setattr("slideguard.application.export_job", broken_export)
    result = ExportService().execute(
        {"schemaVersion": "1.0", "input": source.name},
        base_dir=tmp_path,
    )

    assert result["status"] == "failed"
    assert result["error"]["code"] == "INTERNAL_ERROR"
    assert result["error"]["details"] == {"exceptionType": "RuntimeError"}


def test_service_has_schema_independent_emergency_result(tmp_path: Path, monkeypatch):
    monkeypatch.setattr("slideguard.contracts.load_schema", lambda _name: (_ for _ in ()).throw(RuntimeError("schema gone")))
    result = ExportService().execute(
        {"schemaVersion": "1.0", "input": "missing.pptx"},
        base_dir=tmp_path,
    )

    assert result["status"] == "failed"
    assert result["exitCode"] == 70
    assert result["error"]["stage"] == "result-serialization"


def test_service_returns_stable_cancelled_result_before_export(tmp_path: Path, monkeypatch):
    source = _pptx(tmp_path / "cancel.pptx")
    token = CancellationToken()
    token.cancel()
    called = False

    def unexpected(*_args, **_kwargs):
        nonlocal called
        called = True

    monkeypatch.setattr("slideguard.application.export_job", unexpected)
    result = ExportService().execute(
        {"schemaVersion": "1.0", "input": source.name},
        base_dir=tmp_path,
        cancel_token=token,
    )

    assert called is False
    assert result["status"] == "failed"
    assert result["exitCode"] == 60
    assert result["error"]["code"] == "CANCELLED"
