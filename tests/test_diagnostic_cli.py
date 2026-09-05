from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from slideguard.cli import main
from slideguard.contracts import validate_document


def _doctor() -> dict:
    return {
        "platform": {"system": "Windows", "release": "11", "machine": "AMD64"},
        "executables": {
            "powershell": r"C:\Windows\System32\WindowsPowerShell\powershell.exe",
            "pdftocairo": r"C:\Tools\poppler\pdftocairo.exe",
            "pdftoppm": r"C:\Tools\poppler\pdftoppm.exe",
            "pdfinfo": r"C:\Tools\poppler\pdfinfo.exe",
        },
        "svgRenderer": {"name": "Edge", "path": r"C:\Program Files\Edge\msedge.exe"},
        "powerpoint": {
            "version": "16.0",
            "build": "20326",
            "path": r"C:\Program Files\Office",
        },
        "ok": True,
        "errors": [],
    }


def _write_json(path: Path, document: object) -> Path:
    path.write_text(json.dumps(document, ensure_ascii=False), encoding="utf-8")
    return path


def test_consent_is_required_before_inputs_are_read(tmp_path: Path, monkeypatch, capsys):
    doctor_path = tmp_path / "doctor.json"
    output_path = tmp_path / "diagnostic.json"

    def unexpected_read(*_args, **_kwargs):
        raise AssertionError("diagnostic inputs must not be read without consent")

    monkeypatch.setattr(Path, "read_bytes", unexpected_read)
    exit_code = main(["diagnose", "--doctor", str(doctor_path), "--out", str(output_path)])
    captured = capsys.readouterr()
    result = json.loads(captured.out)

    assert exit_code == 30
    assert result["error"]["code"] == "INPUT_INVALID"
    assert result["error"]["details"] == {"reason": "consent-required"}
    assert captured.out.count("\n") == 1
    assert captured.err == ""
    assert not output_path.exists()


def test_default_stdout_is_one_strict_metadata_only_json_document(tmp_path: Path, capsys):
    doctor_path = _write_json(tmp_path / "doctor.json", _doctor())

    exit_code = main(["diagnose", "--consent", "--doctor", str(doctor_path)])
    captured = capsys.readouterr()
    bundle = json.loads(captured.out, parse_constant=lambda value: pytest.fail(value))

    assert exit_code == 0
    validate_document(bundle, "diagnostic-bundle.schema.json")
    assert captured.out.count("\n") == 1
    assert captured.err == ""
    assert bundle["report"] is None
    assert bundle["doctor"]["dependencies"]["powerShell"] is True
    assert str(tmp_path) not in captured.out
    assert "powerpoint.exe" not in captured.out.casefold()


def test_report_is_included_only_when_its_path_is_explicit(tmp_path: Path, capsys):
    doctor_path = _write_json(tmp_path / "doctor.json", _doctor())
    report_path = _write_json(
        tmp_path / "qa-report.json",
        {
            "source_path": str(tmp_path / "private.pptx"),
            "environment": {"USERNAME": "private-user"},
            "verdict": "FAIL",
            "findings": [{"status": "FAIL", "code": "INPUT_INVALID"}],
        },
    )

    assert main(["diagnose", "--consent", "--doctor", str(doctor_path)]) == 0
    without_report = json.loads(capsys.readouterr().out)
    assert without_report["report"] is None

    assert main(
        ["diagnose", "--consent", "--doctor", str(doctor_path), "--report", str(report_path)]
    ) == 0
    with_report = json.loads(capsys.readouterr().out)
    assert with_report["report"]["findingCodes"] == ["INPUT_INVALID"]
    encoded = json.dumps(with_report, ensure_ascii=False)
    assert "private-user" not in encoded
    assert "private.pptx" not in encoded


@pytest.mark.parametrize(
    "payload",
    [b"\xff\xfe{\x00}\x00", b"[]", b'{"ok":true,"ok":false}', b'{"value":NaN}'],
)
def test_input_must_be_a_strict_utf8_json_object_without_echo(tmp_path: Path, payload: bytes, capsys):
    private_directory = tmp_path / "Keyan-Hu-private"
    private_directory.mkdir()
    doctor_path = private_directory / "doctor.json"
    doctor_path.write_bytes(payload)

    exit_code = main(["diagnose", "--consent", "--doctor", str(doctor_path)])
    captured = capsys.readouterr()
    result = json.loads(captured.out)

    assert exit_code == 30
    assert result["error"]["code"] == "INPUT_INVALID"
    assert result["error"]["stage"] == "diagnosis"
    assert "Keyan-Hu-private" not in captured.out
    assert captured.out.count("\n") == 1
    assert captured.err == ""


def test_output_is_atomically_replaced_and_stdout_stays_empty(tmp_path: Path, monkeypatch, capsys):
    doctor_path = _write_json(tmp_path / "doctor.json", _doctor())
    output_path = tmp_path / "diagnostic.json"
    output_path.write_text("old", encoding="utf-8")
    real_replace = os.replace
    replacements = []

    def tracked_replace(source, target):
        replacements.append((Path(source), Path(target)))
        return real_replace(source, target)

    monkeypatch.setattr("slideguard.cli.os.replace", tracked_replace)
    exit_code = main(
        ["diagnose", "--consent", "--doctor", str(doctor_path), "--out", str(output_path)]
    )
    captured = capsys.readouterr()
    bundle = json.loads(output_path.read_text(encoding="utf-8"))

    assert exit_code == 0
    validate_document(bundle, "diagnostic-bundle.schema.json")
    assert replacements and replacements[0][1] == output_path
    assert replacements[0][0].parent == output_path.parent
    assert not replacements[0][0].exists()
    assert captured.out == ""
    assert captured.err == ""


def test_atomic_write_failure_is_structured_and_does_not_leak_paths(tmp_path: Path, monkeypatch, capsys):
    doctor_path = _write_json(tmp_path / "doctor.json", _doctor())
    private_directory = tmp_path / "Keyan-Hu-private"
    output_path = private_directory / "diagnostic.json"

    def fail_replace(_source, _target):
        raise OSError(f"cannot replace {output_path}")

    monkeypatch.setattr("slideguard.cli.os.replace", fail_replace)
    exit_code = main(
        ["diagnose", "--consent", "--doctor", str(doctor_path), "--out", str(output_path)]
    )
    captured = capsys.readouterr()
    result = json.loads(captured.out)

    assert exit_code == 30
    assert result["error"]["code"] == "INPUT_INVALID"
    assert result["error"]["details"] == {"operation": "write-output"}
    assert "Keyan-Hu-private" not in captured.out
    assert list(private_directory.glob("*.tmp")) == []
    assert captured.out.count("\n") == 1
    assert captured.err == ""


def test_output_cannot_replace_any_input_file(tmp_path: Path, capsys):
    doctor_path = _write_json(tmp_path / "doctor.json", _doctor())
    original = doctor_path.read_bytes()

    exit_code = main(
        ["diagnose", "--consent", "--doctor", str(doctor_path), "--out", str(doctor_path)]
    )
    result = json.loads(capsys.readouterr().out)

    assert exit_code == 30
    assert result["error"]["details"] == {"operation": "protect-input"}
    assert doctor_path.read_bytes() == original
