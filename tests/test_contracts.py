from __future__ import annotations

import json
import zipfile
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator

from slideguard.cli import main
from slideguard.contracts import failed_result, load_request, load_schema, prepare_request, validate_document, validated_result
from slideguard.errors import EnvironmentError, InputError


PRESENTATION_XML = b'''<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<p:presentation xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main"
 xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"
 xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main">
  <p:sldIdLst><p:sldId id="256" r:id="rId1"/><p:sldId id="257" r:id="rId2"/></p:sldIdLst>
  <p:sldSz cx="12192000" cy="6858000"/>
</p:presentation>'''

RELS_XML = b'''<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
  <Relationship Id="rId1" Type="slide" Target="slides/slide1.xml"/>
  <Relationship Id="rId2" Type="slide" Target="slides/slide2.xml"/>
</Relationships>'''


def _minimal_pptx(path: Path) -> Path:
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("ppt/presentation.xml", PRESENTATION_XML)
        archive.writestr("ppt/_rels/presentation.xml.rels", RELS_XML)
        archive.writestr("ppt/slides/slide1.xml", "<p:sld xmlns:p='http://schemas.openxmlformats.org/presentationml/2006/main'/>")
        archive.writestr("ppt/slides/slide2.xml", "<p:sld xmlns:p='http://schemas.openxmlformats.org/presentationml/2006/main'/>")
    return path


def _request(input_name: str) -> dict:
    return {
        "schemaVersion": "1.0",
        "taskId": "test-task",
        "input": input_name,
        "slides": "all",
        "crop": {
            "mode": "manual",
            "boundsPercent": {"left": 5, "top": 3, "right": 95, "bottom": 97},
            "expandPercent": {"left": 1, "top": 2, "right": 3, "bottom": 4},
            "paddingPx": 0,
        },
        "quality": {"pdfMaxBytes": 2500000, "svgMaxBytes": 2500000},
        "behavior": {"dryRun": True},
    }


def test_prepare_request_applies_defaults_and_resolves_file_relative_paths(tmp_path: Path):
    source = _minimal_pptx(tmp_path / "figure.pptx")
    prepared = prepare_request(_request(source.name), base_dir=tmp_path)

    assert prepared.source == source.resolve()
    assert prepared.effective_slides == (1, 2)
    assert prepared.options.crop_percent == (5.0, 3.0, 95.0, 97.0)
    assert prepared.options.expand_percent == (1.0, 2.0, 3.0, 4.0)
    assert prepared.options.reference_width == 4000
    assert prepared.dry_run is True
    assert prepared.config_fingerprint.startswith("sha256:")


def test_task_id_and_output_root_do_not_change_content_fingerprint(tmp_path: Path):
    source = _minimal_pptx(tmp_path / "figure.pptx")
    first = _request(source.name)
    second = json.loads(json.dumps(first))
    second["taskId"] = "another-task"
    first["outputRoot"] = "first-output"
    second["outputRoot"] = "second-output"

    assert prepare_request(first, base_dir=tmp_path).config_fingerprint == prepare_request(
        second, base_dir=tmp_path
    ).config_fingerprint


def test_invalid_manual_bounds_fail_before_export(tmp_path: Path):
    source = _minimal_pptx(tmp_path / "figure.pptx")
    document = _request(source.name)
    document["crop"]["boundsPercent"] = {"left": 90, "top": 3, "right": 10, "bottom": 97}

    with pytest.raises(InputError) as error:
        prepare_request(document, base_dir=tmp_path)

    assert error.value.stage == "validation"
    assert error.value.details["path"] == "/crop/boundsPercent"


def test_unknown_fields_are_rejected(tmp_path: Path):
    source = _minimal_pptx(tmp_path / "figure.pptx")
    document = _request(source.name)
    document["quality"]["jpegMagic"] = True

    with pytest.raises(InputError) as error:
        prepare_request(document, base_dir=tmp_path)

    assert error.value.details["schema"] == "export-request.schema.json"


def test_dry_run_and_error_results_validate_against_public_schema(tmp_path: Path):
    source = _minimal_pptx(tmp_path / "figure.pptx")
    prepared = prepare_request(_request(source.name), base_dir=tmp_path)
    success = validated_result(prepared)
    failure = failed_result(InputError("bad request", stage="validation", details={"path": "/input"}))

    validate_document(success, "export-result.schema.json")
    validate_document(failure, "export-result.schema.json")
    assert success["capabilities"]["rasterToVector"] is False
    assert failure["error"]["code"] == "INPUT_INVALID"


def test_job_file_emits_one_json_document_and_uses_file_directory(tmp_path: Path, capsys):
    source = _minimal_pptx(tmp_path / "figure.pptx")
    request_path = tmp_path / "request.json"
    request_path.write_text(json.dumps(_request(source.name)), encoding="utf-8")

    exit_code = main(["job", str(request_path)])
    captured = capsys.readouterr()
    result = json.loads(captured.out)

    assert exit_code == 0
    assert result["status"] == "validated"
    assert result["source"]["path"] == str(source.resolve())
    assert captured.out.count("\n") == 1
    assert captured.err == ""


def test_job_stdin_emits_structured_failure(tmp_path: Path, monkeypatch, capsys):
    monkeypatch.setattr("sys.stdin.read", lambda: "{not json")

    exit_code = main(["job", "-"])
    captured = capsys.readouterr()
    result = json.loads(captured.out)

    assert exit_code == 30
    assert result["status"] == "failed"
    assert result["error"]["stage"] == "validation"
    assert captured.err == ""


def test_jsonl_progress_is_schema_valid_and_stays_on_stderr(tmp_path: Path, capsys):
    source = _minimal_pptx(tmp_path / "figure.pptx")
    document = _request(source.name)
    document["behavior"]["progress"] = "jsonl"
    request_path = tmp_path / "request.json"
    request_path.write_text(json.dumps(document), encoding="utf-8")

    exit_code = main(["job", str(request_path)])
    captured = capsys.readouterr()
    result = json.loads(captured.out)
    events = [json.loads(line) for line in captured.err.splitlines()]

    assert exit_code == 0
    assert result["status"] == "validated"
    assert [event["sequence"] for event in events] == [0, 1]
    for event in events:
        validate_document(event, "progress-event.schema.json")


def test_invalid_request_does_not_call_export_engine(tmp_path: Path, monkeypatch, capsys):
    source = _minimal_pptx(tmp_path / "figure.pptx")
    document = _request(source.name)
    document["behavior"]["dryRun"] = False
    document["crop"]["boundsPercent"]["right"] = 1
    request_path = tmp_path / "request.json"
    request_path.write_text(json.dumps(document), encoding="utf-8")

    def unexpected_export(*_args, **_kwargs):
        raise AssertionError("export engine must not be called")

    monkeypatch.setattr("slideguard.application.export_job", unexpected_export)
    exit_code = main(["job", str(request_path)])
    result = json.loads(capsys.readouterr().out)

    assert exit_code == 30
    assert result["error"]["stage"] == "validation"


@pytest.mark.parametrize(
    "payload",
    [
        '{"schemaVersion":"1.0","input":"x.pptx","crop":{"expandPercent":NaN}}',
        '{"schemaVersion":"1.0","input":"first.pptx","input":"second.pptx"}',
    ],
)
def test_request_loader_rejects_non_standard_json_and_duplicate_keys(payload):
    with pytest.raises(InputError, match="strict JSON"):
        load_request(payload)


def test_negative_zero_and_positive_zero_have_same_fingerprint(tmp_path: Path):
    source = _minimal_pptx(tmp_path / "figure.pptx")
    positive = _request(source.name)
    negative = json.loads(json.dumps(positive))
    positive["crop"]["expandPercent"] = 0.0
    negative["crop"]["expandPercent"] = -0.0

    assert prepare_request(positive, base_dir=tmp_path).config_fingerprint == prepare_request(
        negative, base_dir=tmp_path
    ).config_fingerprint


def test_result_schema_is_independently_resolvable_offline(tmp_path: Path):
    source = _minimal_pptx(tmp_path / "figure.pptx")
    result = validated_result(prepare_request(_request(source.name), base_dir=tmp_path))
    Draft202012Validator(load_schema("export-result.schema.json")).validate(result)


def test_result_schema_rejects_contradictory_success(tmp_path: Path):
    source = _minimal_pptx(tmp_path / "figure.pptx")
    result = validated_result(prepare_request(_request(source.name), base_dir=tmp_path))
    result.update({"status": "succeeded", "output": {"packagePath": "x", "manifestPath": "manifest.json", "reportPath": "qa-report.json"}, "jobId": "x", "verdict": "PASS", "exitCode": 50})

    with pytest.raises(InputError):
        validate_document(result, "export-result.schema.json")


def test_error_stage_defaults_follow_error_class():
    result = failed_result(EnvironmentError("PowerPoint missing"))
    assert result["error"]["stage"] == "environment"


@pytest.mark.parametrize("argv", [["job", "--bad-option"], ["export", "figure.pptx", "--json", "--bad-option"]])
def test_machine_argument_errors_emit_one_result_document(argv, capsys):
    exit_code = main(argv)
    captured = capsys.readouterr()
    result = json.loads(captured.out)

    assert exit_code == 30
    assert result["error"]["code"] == "INPUT_INVALID"
    assert captured.out.count("\n") == 1
    assert captured.err == ""
