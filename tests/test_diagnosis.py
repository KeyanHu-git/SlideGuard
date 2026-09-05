from __future__ import annotations

import json
from typing import Any

import pytest
from jsonschema import Draft202012Validator

from slideguard.contracts import load_schema, validate_document
from slideguard.diagnosis import (
    DiagnosticSafetyError,
    DiagnosticSizeError,
    build_diagnostic_bundle,
    validate_diagnostic_bundle,
)


def _doctor() -> dict[str, Any]:
    return {
        "tool": {"name": "SlideGuard", "version": "0.2.0.dev0"},
        "platform": {"system": "Windows", "release": "11", "machine": "AMD64"},
        "executables": {
            "powershell": "C:\\Windows\\System32\\WindowsPowerShell\\powershell.exe",
            "pdftocairo": "C:\\Tools\\poppler\\pdftocairo.exe",
            "pdftoppm": "C:\\Tools\\poppler\\pdftoppm.exe",
            "pdfinfo": "C:\\Tools\\poppler\\pdfinfo.exe",
        },
        "svgRenderer": {"name": "Edge"},
        "powerpoint": {"version": "16.0", "build": "20326", "path": "C:\\Program Files\\Office"},
        "ok": True,
        "errors": [],
    }


def _identity(value: Any) -> Any:
    return value


def _no_secrets(_value: Any) -> list[str]:
    return []


def _redact_strings(value: Any) -> Any:
    if isinstance(value, dict):
        return {key: _redact_strings(child) for key, child in value.items()}
    if isinstance(value, list):
        return [_redact_strings(child) for child in value]
    if isinstance(value, str) and (
        ":\\" in value or value.startswith("\\\\") or "secret-token" in value or "Keyan Hu" in value
    ):
        return "[redacted]"
    return value


def test_builds_schema_valid_metadata_only_bundle_and_scans_twice():
    calls = []

    def scanner(value):
        calls.append(value)
        return []

    bundle = build_diagnostic_bundle(
        _doctor(),
        {"code": "EXPORT_FAILED", "stage": "export", "exitCode": 40},
        redactor=_redact_strings,
        scanner=scanner,
    )

    validate_document(bundle, "diagnostic-bundle.schema.json")
    assert len(calls) == 2
    assert bundle["doctor"]["powerpoint"] == {"available": True, "version": "16.0", "build": "20326"}
    assert bundle["report"] is None
    assert [item["id"] for item in bundle["recommendations"]] == ["EXPORT_FAILED"]
    encoded = json.dumps(bundle, ensure_ascii=False)
    assert "C:\\" not in encoded
    assert "environment" not in encoded.casefold()


def test_schema_is_self_contained_and_valid_offline():
    schema = load_schema("diagnostic-bundle.schema.json")

    Draft202012Validator.check_schema(schema)
    assert "$ref" not in json.dumps(schema)


def test_report_is_opt_in_and_reduced_to_fixed_summary_fields():
    report = {
        "source_path": "C:\\Users\\Keyan Hu\\paper.pptx",
        "environment": {"USERNAME": "Keyan Hu"},
        "verdict": "PASS_WITH_SOURCE_WARNINGS",
        "configFingerprint": "sha256:" + "a" * 64,
        "artifacts": [
            {"kind": "pdf", "path": "C:\\secret\\figure.pdf"},
            {"kind": "svg-compact", "path": "C:\\secret\\figure.svg"},
        ],
        "findings": [
            {"status": "PASS", "code": "SOURCE_IMMUTABLE", "message": "C:\\private"},
            {"status": "PASS_WITH_SOURCE_WARNINGS", "code": "IMAGE_UNMATCHED_CANDIDATE"},
        ],
    }
    omitted = build_diagnostic_bundle(
        _doctor(), report=report, redactor=_redact_strings, scanner=_no_secrets,
    )
    included = build_diagnostic_bundle(
        _doctor(), report=report, include_report=True, redactor=_redact_strings, scanner=_no_secrets,
    )

    assert omitted["report"] is None
    assert included["report"] == {
        "verdict": "PASS_WITH_SOURCE_WARNINGS",
        "findingCounts": {"PASS": 1, "PASS_WITH_SOURCE_WARNINGS": 1},
        "findingCodes": ["IMAGE_UNMATCHED_CANDIDATE", "SOURCE_IMMUTABLE"],
        "artifactKinds": ["pdf", "svg-compact"],
    }
    assert included["configFingerprint"] == "sha256:" + "a" * 64
    encoded = json.dumps(included, ensure_ascii=False)
    assert "paper.pptx" not in encoded
    assert "Keyan Hu" not in encoded
    assert "USERNAME" not in encoded


@pytest.mark.parametrize(
    ("doctor_patch", "error", "expected"),
    [
        ({"powerpoint": None}, None, "POWERPOINT_UNAVAILABLE"),
        ({"executables": {"powershell": "ok"}}, None, "POPPLER_UNAVAILABLE"),
        ({"compatibility": {"powerpoint": "unsupported"}}, None, "POWERPOINT_VERSION_UNSUPPORTED"),
        ({"errors": ["Access is denied"]}, {"code": "PERMISSION_DENIED"}, "PERMISSION_DENIED"),
        ({}, {"code": "INPUT_INVALID", "stage": "validation", "exitCode": 30}, "INPUT_INVALID"),
    ],
)
def test_fixed_inputs_produce_fixed_offline_recommendations(doctor_patch, error, expected):
    doctor = _doctor()
    doctor.update(doctor_patch)
    first = build_diagnostic_bundle(doctor, error, redactor=_redact_strings, scanner=_no_secrets)
    second = build_diagnostic_bundle(doctor, error, redactor=_redact_strings, scanner=_no_secrets)

    assert first == second
    assert expected in {item["id"] for item in first["recommendations"]}


def test_adversarial_username_token_unc_and_chinese_path_are_rejected_without_echo():
    dangerous = _doctor()
    dangerous["errors"] = [
        "user Keyan Hu token secret-token at \\\\lab-server\\共享\\论文图片.png"
    ]

    def scanner(value):
        text = json.dumps(value, ensure_ascii=False)
        hits = []
        if "Keyan Hu" in text:
            hits.append("USERNAME")
        if "secret-token" in text:
            hits.append("API_TOKEN")
        if "\\\\lab-server" in text:
            hits.append("UNC_PATH")
        if "论文图片.png" in text:
            hits.append("CHINESE_PATH")
        return hits

    with pytest.raises(DiagnosticSafetyError) as caught:
        build_diagnostic_bundle(dangerous, redactor=_identity, scanner=scanner)

    assert caught.value.details == {
        "scanPass": "pre-package",
        "categories": ["API_TOKEN", "CHINESE_PATH", "UNC_PATH", "USERNAME"],
    }
    assert "secret-token" not in str(caught.value)
    assert "Keyan Hu" not in json.dumps(caught.value.details)


def test_second_scan_can_reject_a_bad_final_redaction_without_secret_echo():
    calls = 0

    def scanner(_value):
        nonlocal calls
        calls += 1
        return [] if calls == 1 else ["PRIVATE_KEY"]

    with pytest.raises(DiagnosticSafetyError) as caught:
        build_diagnostic_bundle(_doctor(), redactor=_redact_strings, scanner=scanner)

    assert caught.value.details == {"scanPass": "post-package", "categories": ["PRIVATE_KEY"]}


def test_scanner_failure_is_fail_closed_without_echoing_exception_text():
    def broken_scanner(_value):
        raise RuntimeError("secret-token-from-scanner")

    with pytest.raises(DiagnosticSafetyError) as caught:
        build_diagnostic_bundle(_doctor(), redactor=_redact_strings, scanner=broken_scanner)

    assert caught.value.details == {
        "scanPass": "pre-package",
        "categories": ["SECRET_SCANNER_FAILED"],
    }
    assert "secret-token-from-scanner" not in str(caught.value)


def test_absolute_paths_and_source_images_are_forbidden_even_if_schema_allows_string():
    bundle = build_diagnostic_bundle(_doctor(), redactor=_redact_strings, scanner=_no_secrets)
    bundle["recommendations"][0:0] = [
        {"id": "BAD", "title": "Open C:\\Users\\name\\figure.pptx", "steps": ["See image.png"]}
    ]

    with pytest.raises(DiagnosticSafetyError) as caught:
        validate_diagnostic_bundle(bundle, scanner=_no_secrets)

    assert caught.value.details["categories"] == ["ABSOLUTE_PATH", "SOURCE_OR_IMAGE_FILE"]


@pytest.mark.parametrize(
    "value",
    ["Open /opt/slideguard/report.json", "Open file:///C:/private/report.json", "Attach output.svg"],
)
def test_metadata_policy_rejects_other_absolute_paths_and_exported_files(value):
    bundle = build_diagnostic_bundle(_doctor(), redactor=_redact_strings, scanner=_no_secrets)
    bundle["recommendations"][0:0] = [{"id": "BAD", "title": value, "steps": ["Retry."]}]

    with pytest.raises(DiagnosticSafetyError):
        validate_diagnostic_bundle(bundle, scanner=_no_secrets)


def test_bundle_size_limit_is_strict():
    report = {
        "verdict": "FAIL",
        "findings": [
            {"status": "FAIL", "code": f"FAILURE_{index:04d}"}
            for index in range(256)
        ],
    }
    bundle = build_diagnostic_bundle(
        _doctor(), report=report, include_report=True,
        redactor=_redact_strings, scanner=_no_secrets,
    )

    with pytest.raises(DiagnosticSizeError) as caught:
        validate_diagnostic_bundle(bundle, scanner=_no_secrets, max_bytes=1024)

    assert caught.value.details["actualBytes"] > caught.value.details["maxBytes"]


def test_partial_privacy_dependency_is_rejected():
    with pytest.raises(DiagnosticSafetyError) as caught:
        build_diagnostic_bundle(_doctor(), redactor=_identity)

    assert caught.value.details["categories"] == ["PRIVACY_INTERFACE_INCOMPLETE"]


def test_default_privacy_service_drops_local_fields_and_paths():
    doctor = _doctor()
    doctor["errors"] = [
        "read failed at \\\\lab-server\\共享\\论文图片.png"
    ]
    report = {
        "source_path": "C:\\Users\\Keyan Hu\\paper.pptx",
        "environment": {"USERNAME": "Keyan Hu"},
        "verdict": "FAIL",
        "findings": [{"status": "FAIL", "code": "INPUT_INVALID"}],
    }

    bundle = build_diagnostic_bundle(doctor, report=report, include_report=True)
    encoded = json.dumps(bundle, ensure_ascii=False)

    assert bundle["report"]["findingCodes"] == ["INPUT_INVALID"]
    assert bundle["doctor"]["dependencies"] == {
        "powerShell": True,
        "pdfToCairo": True,
        "pdfToPpm": True,
        "pdfInfo": True,
        "svgRenderer": True,
    }
    assert "Keyan Hu" not in encoded
    assert "paper.pptx" not in encoded
    assert "论文图片.png" not in encoded
