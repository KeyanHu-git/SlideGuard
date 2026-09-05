from __future__ import annotations

import io
import json
import os
import sys
import warnings
from types import SimpleNamespace

import pytest

import slideguard.cli as cli
from slideguard.machine_io import (
    MachineOutputFirewall,
    emit_noise_summary,
    sanitize_machine_document,
)


SECRET = "SG_STDOUT_SECRET_72e830f5"
PRIVATE_PATH = rf"C:\Users\研究员\{SECRET}\paper.pptx"


def _emit_untrusted_noise() -> None:
    print(f"third-party stdout {PRIVATE_PATH} token={SECRET}")
    print(f"third-party stderr Bearer {SECRET}", file=sys.stderr)
    warnings.warn(f"third-party warning {PRIVATE_PATH}", RuntimeWarning)
    os.write(1, f"native stdout {SECRET}".encode())
    os.write(2, f"native stderr {SECRET}".encode())


def _strict_json_documents(text: str) -> list[dict]:
    return [
        json.loads(line, parse_constant=lambda value: pytest.fail(f"non-finite JSON: {value}"))
        for line in text.splitlines()
        if line.strip()
    ]


def _assert_guarded_machine_output(captured, *, stdout_documents: int = 1) -> list[dict]:
    assert SECRET not in captured.out
    assert SECRET not in captured.err
    assert PRIVATE_PATH not in captured.out
    assert PRIVATE_PATH not in captured.err
    output = _strict_json_documents(captured.out)
    assert len(output) == stdout_documents
    stderr = _strict_json_documents(captured.err)
    assert len(stderr) == 1
    assert stderr[0]["code"] == "OUTPUT_NOISE_SUPPRESSED"
    return output


def test_firewall_keeps_python_binary_warning_and_fd_noise_out_of_stdout(capfd):
    progress = {"schemaVersion": "1.0", "event": "progress", "sequence": 0}
    with MachineOutputFirewall() as firewall:
        print("library print " + SECRET)
        sys.stdout.buffer.write(("binary " + SECRET).encode("utf-8"))
        print("stderr " + SECRET, file=sys.stderr)
        warnings.warn("warning " + SECRET, RuntimeWarning)
        os.write(1, ("native stdout " + SECRET).encode("utf-8"))
        os.write(2, ("native stderr " + SECRET).encode("utf-8"))
        print(json.dumps(progress, separators=(",", ":")), file=firewall.safe_stderr, flush=True)

    emit_noise_summary(firewall)
    print(json.dumps({"status": "validated", "exitCode": 0}, separators=(",", ":")))
    captured = capfd.readouterr()

    assert SECRET not in captured.out
    assert SECRET not in captured.err
    assert len(captured.out.strip().splitlines()) == 1
    assert json.loads(captured.out) == {"status": "validated", "exitCode": 0}
    stderr_documents = [json.loads(line) for line in captured.err.splitlines()]
    assert stderr_documents[0] == progress
    assert stderr_documents[1]["code"] == "OUTPUT_NOISE_SUPPRESSED"
    assert stderr_documents[1]["suppressed"]["stdoutBytes"] > 0
    assert stderr_documents[1]["suppressed"]["stderrBytes"] > 0


def test_no_noise_produces_no_stderr_summary(capfd):
    with MachineOutputFirewall() as firewall:
        pass

    emit_noise_summary(firewall)

    assert capfd.readouterr().err == ""


def test_error_text_is_redacted_without_changing_declared_result_paths():
    document = {
        "status": "failed",
        "exitCode": 40,
        "source": {"path": r"C:\Users\研究员\paper.pptx", "sha256": "a" * 64},
        "output": {"packagePath": r"D:\exports\job-1"},
        "error": {
            "code": "EXPORT_FAILED",
            "stage": "powerpoint",
            "message": rf"PowerPoint failed at C:\Users\研究员\paper.pptx token={SECRET}",
            "details": {"stderr": rf"Bearer {SECRET}"},
        },
    }

    result = sanitize_machine_document(document)
    encoded = json.dumps(result, ensure_ascii=False)

    assert SECRET not in encoded
    assert result["source"]["path"] == document["source"]["path"]
    assert result["output"]["packagePath"] == document["output"]["packagePath"]
    assert result["error"]["code"] == "EXPORT_FAILED"
    assert result["error"]["stage"] == "powerpoint"
    assert "<USER_DIR>" in result["error"]["message"]
    assert "<REDACTED>" in result["error"]["message"]


def test_secret_shaped_fields_are_redacted_at_any_depth():
    document = {
        "doctor": {"apiToken": SECRET},
        "results": [{"details": {"password": SECRET}}],
        "authorization": SECRET,
    }

    result = sanitize_machine_document(document)

    assert SECRET not in json.dumps(result)
    assert result["doctor"]["apiToken"] == "<REDACTED>"
    assert result["results"][0]["details"]["password"] == "<REDACTED>"
    assert result["authorization"] == "<REDACTED>"


def test_secret_scan_status_is_not_mistaken_for_a_credential():
    document = {"safety": {"secretScan": "passed"}}

    result = sanitize_machine_document(document)

    assert result == document


def test_sanitizer_does_not_mutate_the_machine_result():
    document = {"error": {"message": "token=" + SECRET}}

    sanitize_machine_document(document)

    assert document["error"]["message"] == "token=" + SECRET


def test_job_cli_contains_service_noise_and_redacts_failure(monkeypatch, capfd):
    class NoisyExportService:
        def execute(self, document, *, base_dir, event_sink=None):
            _emit_untrusted_noise()
            raise RuntimeError(f"export failed at {PRIVATE_PATH}; token={SECRET}")

    monkeypatch.setattr(cli, "ExportService", NoisyExportService)
    monkeypatch.setattr(sys, "stdin", io.StringIO('{"schemaVersion":"1.0"}'))

    exit_code = cli.main(["job", "-"])
    captured = capfd.readouterr()
    [result] = _assert_guarded_machine_output(captured)

    assert exit_code == 70
    assert result["error"]["code"] == "INTERNAL_ERROR"
    assert result["error"]["stage"] == "internal"
    assert "<USER_DIR>" in result["error"]["message"]


def test_batch_cli_contains_service_noise(monkeypatch, capfd):
    class NoisyBatchService:
        def execute(self, document, *, base_dir, event_sink=None):
            _emit_untrusted_noise()
            return {"schemaVersion": "1.0", "status": "completed", "exitCode": 0, "results": []}

    monkeypatch.setattr(cli, "BatchService", NoisyBatchService)
    monkeypatch.setattr(sys, "stdin", io.StringIO('{"schemaVersion":"1.0"}'))

    assert cli.main(["batch", "-"]) == 0
    captured = capfd.readouterr()
    [result] = _assert_guarded_machine_output(captured)

    assert result["status"] == "completed"


@pytest.mark.parametrize("command", ["doctor", "verify", "fixtures"])
def test_json_utility_commands_contain_dependency_noise(command, monkeypatch, capfd, tmp_path):
    if command == "doctor":
        def noisy_doctor():
            _emit_untrusted_noise()
            return {"ok": True, "errors": []}

        monkeypatch.setattr(cli, "doctor", noisy_doctor)
        arguments = ["doctor", "--json"]
    elif command == "verify":
        def noisy_verify(_manifest):
            _emit_untrusted_noise()
            return SimpleNamespace(value="PASS"), []

        monkeypatch.setattr(cli, "verify_package", noisy_verify)
        arguments = ["verify", str(tmp_path / "manifest.json")]
    else:
        import slideguard.fixtures as fixtures

        def noisy_fixture(_output):
            _emit_untrusted_noise()
            return {"status": "created"}

        monkeypatch.setattr(fixtures, "build_core_fixture", noisy_fixture)
        arguments = ["fixtures", "--out", str(tmp_path / "fixture")]

    assert cli.main(arguments) == 0
    captured = capfd.readouterr()
    _assert_guarded_machine_output(captured)


def test_diagnose_cli_contains_builder_noise(monkeypatch, capfd, tmp_path):
    doctor_path = tmp_path / "doctor.json"
    doctor_path.write_text('{"schemaVersion":"1.0"}', encoding="utf-8")

    def noisy_builder(*args, **kwargs):
        _emit_untrusted_noise()
        return {
            "schemaVersion": "1.0",
            "safety": {"secretScan": "passed"},
            "events": [],
        }

    monkeypatch.setattr(cli, "build_diagnostic_bundle", noisy_builder)

    assert cli.main(["diagnose", "--consent", "--doctor", str(doctor_path)]) == 0
    captured = capfd.readouterr()
    [result] = _assert_guarded_machine_output(captured)

    assert result["safety"]["secretScan"] == "passed"


def test_human_help_remains_plain_text(capsys):
    with pytest.raises(SystemExit) as raised:
        cli.main(["--help"])

    captured = capsys.readouterr()
    assert raised.value.code == 0
    assert captured.out.startswith("usage: slideguard")
    assert "{doctor,export,job,batch,resume-plan,diagnose,gui,studio,verify,fixtures}" in captured.out


def test_machine_dispatch_failure_is_one_redacted_json_document(monkeypatch, capsys):
    def fail_before_service(_args):
        raise RuntimeError(f"setup failed at {PRIVATE_PATH}; password={SECRET}")

    monkeypatch.setattr(cli, "_document_from_export_args", fail_before_service)

    assert cli.main(["export", "figure.pptx", "--json"]) == 70
    captured = capsys.readouterr()
    [result] = _strict_json_documents(captured.out)

    assert captured.err == ""
    assert SECRET not in captured.out
    assert PRIVATE_PATH not in captured.out
    assert result["error"]["code"] == "INTERNAL_ERROR"
    assert result["error"]["stage"] == "internal"
