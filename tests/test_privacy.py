from __future__ import annotations

import json

import pytest

from slideguard.privacy import (
    CYCLE,
    ENV_VALUE,
    REDACTED,
    redact,
    redact_for_sharing,
    redact_structure,
    redact_text,
    scan_secret_categories,
)


UNIQUE_SECRET = "SG-ONLY-SECRET-9f4a7c2e613b"


def test_recursive_redaction_preserves_contract_fields_and_does_not_mutate_input():
    source = {
        "code": "EXPORT_FAILED",
        "stage": "powerpoint-timeout",
        "relativePath": "evidence/p0001/compare.png",
        "details": {
            "path": r"C:\Users\研究员\OneDrive\论文\figure.pptx",
            "apiKey": UNIQUE_SECRET,
            "messages": ["password=" + UNIQUE_SECRET, {"status": "failed"}],
        },
    }

    result = redact_structure(source, environ={})

    assert result["code"] == "EXPORT_FAILED"
    assert result["stage"] == "powerpoint-timeout"
    assert result["relativePath"] == "evidence/p0001/compare.png"
    assert result["details"]["path"] == r"<USER_DIR>\figure.pptx"
    assert result["details"]["apiKey"] == REDACTED
    assert result["details"]["messages"][0] == "password=" + REDACTED
    assert source["details"]["apiKey"] == UNIQUE_SECRET


def test_exception_chain_is_converted_without_traceback_or_secret_values():
    try:
        try:
            raise OSError(r"PowerPoint failed at \\lab-server\private\run\deck.pptx token=" + UNIQUE_SECRET)
        except OSError as cause:
            raise RuntimeError("worker environment was " + UNIQUE_SECRET) from cause
    except RuntimeError as error:
        result = redact(error, environ={"SLIDEGUARD_TEST_SECRET": UNIQUE_SECRET})

    serialized = json.dumps(result, ensure_ascii=False)
    assert UNIQUE_SECRET not in serialized
    assert result["exceptionType"] == "RuntimeError"
    assert result["cause"]["exceptionType"] == "OSError"
    assert "<UNC_PATH>\\deck.pptx" in result["cause"]["message"]
    assert "traceback" not in serialized.casefold()


@pytest.mark.parametrize(
    "value",
    [
        "Authorization: Bearer abcdefghijklmnopqrstuvwxyz012345",
        "api_key=abcdefghijklmnopqrstuvwxyz012345",
        "token=abcdefghijklmnopqrstuvwxyz012345",
        "Authorization: Basic abcdefghijklmnopqrstuvwxyz012345",
        "ghp_abcdefghijklmnopqrstuvwxyz012345",
        "sk-abcdefghijklmnopqrstuvwxyz012345",
        "AKIAABCDEFGHIJKLMNOP",
        "eyJabcdefghijk.abcdefghijkl.abcdefghijkl",
    ],
)
def test_credential_shapes_are_masked(value: str):
    result = redact_text(value, environ={})

    assert value != result
    assert REDACTED in result
    assert "abcdefghijklmnopqrstuvwxyz012345" not in result


def test_absolute_path_forms_keep_only_the_final_filename():
    message = (
        r"Windows C:\Research Team\Private\figure.pptx; "
        r"user C:\Users\Alice Smith\Secret\notes.docx; "
        r"UNC \\server-01\hidden-share\paper\plot.svg; "
        "Unix /home/alice/private/result.pdf"
    )

    result = redact_text(message, environ={})

    assert r"<ABS_PATH>\figure.pptx" in result
    assert r"<USER_DIR>\notes.docx" in result
    assert r"<UNC_PATH>\plot.svg" in result
    assert "<ABS_PATH>/result.pdf" in result
    for private_part in ("Research Team", "Alice Smith", "hidden-share", "/home/alice/private"):
        assert private_part not in result


def test_share_copy_omits_local_fields_but_keeps_relative_evidence_names():
    local_result = {
        "source": {"path": r"C:\Users\Alice\paper.pptx", "sha256": "a" * 64},
        "sourcePath": r"C:\Users\Alice\paper.pptx",
        "output": {
            "packagePath": r"D:\Private\slideguard-output\job-1",
            "manifestPath": "manifest.json",
            "reportPath": "qa-report.json",
        },
        "environment": {
            "python": {"executable": r"C:\Python313\python.exe", "version": "3.13.9"},
            "platform": "Windows 11",
        },
        "artifacts": [{"relativePath": "svg/figure.svg", "sha256": "b" * 64}],
        "error": {"code": "EXPORT_FAILED", "stage": "export", "details": {}},
    }

    result = redact_for_sharing(local_result, environ={})

    assert "path" not in result["source"]
    assert "sourcePath" not in result
    assert "packagePath" not in result["output"]
    assert result["output"] == {"manifestPath": "manifest.json", "reportPath": "qa-report.json"}
    assert result["environment"]["python"] == {"version": "3.13.9"}
    assert result["artifacts"][0]["relativePath"] == "svg/figure.svg"
    assert result["error"]["code"] == "EXPORT_FAILED"
    assert result["error"]["stage"] == "export"


def test_unique_secret_injection_has_zero_hits_in_share_output():
    injected = {
        "sourcePath": rf"C:\Users\{UNIQUE_SECRET}\OneDrive\paper.pptx",
        "request": {
            "apiKey": UNIQUE_SECRET,
            "note": UNIQUE_SECRET,
            "input": rf"\\server\{UNIQUE_SECRET}\figure.pptx",
        },
        "environment": {"SLIDEGUARD_TEST_SECRET": UNIQUE_SECRET},
        "powerpointError": RuntimeError(
            rf"Open failed for C:\Users\{UNIQUE_SECRET}\deck.pptx; Bearer {UNIQUE_SECRET}"
        ),
        "relativePath": "evidence/p0001/diff.png",
        "code": "EXPORT_FAILED",
        "stage": "powerpoint",
    }

    shared = redact_for_sharing(injected, environ={"SLIDEGUARD_TEST_SECRET": UNIQUE_SECRET})
    serialized = json.dumps(shared, ensure_ascii=False, sort_keys=True)

    assert UNIQUE_SECRET not in serialized
    assert shared["relativePath"] == "evidence/p0001/diff.png"
    assert shared["code"] == "EXPORT_FAILED"
    assert shared["stage"] == "powerpoint"
    assert scan_secret_categories(
        shared,
        environ={"SLIDEGUARD_TEST_SECRET": UNIQUE_SECRET},
    ) == []


def test_scanner_returns_categories_without_returning_secret_material():
    injected = {
        "sourcePath": r"C:\Users\Alice\paper.pptx",
        "apiKey": UNIQUE_SECRET,
        "message": rf"Bearer {UNIQUE_SECRET} at \\server\share\deck.pptx",
        "error": RuntimeError("env=" + UNIQUE_SECRET),
    }

    categories = scan_secret_categories(
        injected,
        environ={"SLIDEGUARD_TEST_SECRET": UNIQUE_SECRET},
    )

    assert categories == sorted(categories)
    assert {
        "absolute-path",
        "credential-field",
        "credential-text",
        "environment-value",
        "exception-text",
        "local-only-field",
        "unc-path",
        "user-directory",
    }.issubset(categories)
    assert UNIQUE_SECRET not in json.dumps(categories)


def test_unique_secret_audit_covers_each_share_channel():
    environment = {"SLIDEGUARD_TEST_SECRET": UNIQUE_SECRET}
    raw_channels = {
        "stdout": {
            "source": {"path": rf"C:\Users\{UNIQUE_SECRET}\paper.pptx"},
            "code": "EXPORT_FAILED",
        },
        "stderr": {"event": "progress", "message": "token=" + UNIQUE_SECRET},
        "report": {
            "source_path": rf"\\server\{UNIQUE_SECRET}\paper.pptx",
            "environment": {"SLIDEGUARD_TEST_SECRET": UNIQUE_SECRET},
        },
        "diagnostic": {
            "stage": "powerpoint",
            "error": RuntimeError("PowerPoint said " + UNIQUE_SECRET),
        },
    }

    for name, raw in raw_channels.items():
        shared = redact_for_sharing(raw, environ=environment)
        encoded = json.dumps(shared, ensure_ascii=False, default=str)
        assert UNIQUE_SECRET not in encoded, name
        assert scan_secret_categories(shared, environ=environment) == [], name


def test_cycles_are_replaced_with_a_fixed_marker():
    value = []
    value.append(value)

    assert redact(value, environ={}) == [CYCLE]


def test_short_environment_values_are_not_used_as_global_replacements():
    assert redact_text("slide 1 of 3", environ={"SHORT": "of"}) == "slide 1 of 3"
    assert redact_text("marker-abcd", environ={"LONG": "abcd"}) == "marker-" + ENV_VALUE


def test_environment_replacement_cannot_destroy_error_identity_or_relative_paths():
    value = {
        "code": "EXPORT_FAILED",
        "stage": "export",
        "status": "failed",
        "relativePath": "evidence/p0001/diff.png",
        "message": "export failed at evidence/p0001/diff.png",
    }
    environment = {
        "A": "EXPORT_FAILED",
        "B": "export",
        "C": "evidence/p0001/diff.png",
    }

    result = redact(value, environ=environment)

    assert result["code"] == "EXPORT_FAILED"
    assert result["stage"] == "export"
    assert result["status"] == "failed"
    assert result["relativePath"] == "evidence/p0001/diff.png"
    assert result["message"] == f"{ENV_VALUE} failed at {ENV_VALUE}"
