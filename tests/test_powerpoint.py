import json
import subprocess
from pathlib import Path

import pytest

from slideguard.cancellation import CancellationToken
from slideguard.errors import CancelledError, ExportError
from slideguard.powerpoint import (
    _proven_owned_process,
    _timeout_details,
    invoke,
    preview_reference,
)


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


class _TimedOutWorker:
    def __init__(self, pid: int = 4242) -> None:
        self.pid = pid
        self.terminated = False
        self.killed = False

    def communicate(self, timeout=None):
        raise subprocess.TimeoutExpired("worker", timeout)

    def poll(self):
        return None

    def terminate(self):
        self.terminated = True

    def kill(self):
        self.killed = True

    def wait(self, timeout=None):
        return 1


def _worker_state(*, nonce: str, worker_pid: int, ownership: str) -> dict:
    return {
        "schemaVersion": "1.0",
        "nonce": nonce,
        "workerPid": worker_pid,
        "phase": "exporting-pdf",
        "cleanupComplete": False,
        "comScratch": None,
        "powerpoint": {
            "pid": 9898,
            "parentPid": worker_pid,
            "startTimeUtc": "2026-09-04T00:00:00.0000000Z",
            "ownership": ownership,
            "proof": {
                "absentBeforeActivation": True,
                "parentIsWorker": True,
                "startedDuringActivation": True,
                "windowPidMatches": True,
                "identityMethod": "application-window",
                "automationCommandLine": True,
                "uniqueActivationCandidate": True,
            },
        },
    }


def test_caller_cancellation_uses_worker_cleanup_path(tmp_path: Path, monkeypatch):
    worker = _TimedOutWorker()
    token = CancellationToken()
    token.cancel()
    monkeypatch.setattr("slideguard.powerpoint.subprocess.Popen", lambda *_args, **_kwargs: worker)
    monkeypatch.setattr("slideguard.powerpoint._powershell", lambda: "powershell")
    monkeypatch.setattr("slideguard.powerpoint._worker", lambda: tmp_path / "worker.ps1")
    observed = []

    def cleanup(process, **kwargs):
        observed.append((process, kwargs))
        return {"residualRisk": False}

    monkeypatch.setattr("slideguard.powerpoint._timeout_details", cleanup)
    with pytest.raises(CancelledError) as caught:
        invoke({"mode": "probe"}, tmp_path / "work", cancel_token=token)

    assert caught.value.code == "CANCELLED"
    assert caught.value.details["requestedBy"] == "caller"
    assert observed[0][0] is worker


def test_owned_pid_requires_nonce_worker_and_all_four_proofs():
    state = _worker_state(nonce="n", worker_pid=42, ownership="slideguard-owned")
    assert _proven_owned_process(state, nonce="n", worker_pid=42)["pid"] == 9898

    for mutation in (
        lambda item: item.update(nonce="wrong"),
        lambda item: item.update(workerPid=41),
        lambda item: item["powerpoint"].update(ownership="reused-or-unproven"),
        lambda item: item["powerpoint"]["proof"].update(startedDuringActivation=False),
    ):
        changed = json.loads(json.dumps(state))
        mutation(changed)
        assert _proven_owned_process(changed, nonce="n", worker_pid=42) is None

    dcom_state = json.loads(json.dumps(state))
    dcom_state["powerpoint"]["parentPid"] = 1912
    dcom_state["powerpoint"]["proof"].update(
        parentIsWorker=False,
        windowPidMatches=False,
        identityMethod="unique-automation-activation",
    )
    assert _proven_owned_process(dcom_state, nonce="n", worker_pid=42)["pid"] == 9898


def test_timeout_never_terminates_unproven_or_reused_powerpoint(tmp_path: Path, monkeypatch):
    nonce = "reused"
    worker = _TimedOutWorker()
    status = tmp_path / "status.json"
    cancel = tmp_path / "cancel.json"
    status.write_text(
        json.dumps(_worker_state(nonce=nonce, worker_pid=worker.pid, ownership="reused-or-unproven")),
        encoding="utf-8",
    )
    calls = []
    monkeypatch.setattr("slideguard.powerpoint._terminate_proven_owned_powerpoint", lambda value: calls.append(value))
    monkeypatch.setattr("slideguard.powerpoint._reap_in_background", lambda value: calls.append("reap"))

    details = _timeout_details(
        worker, status_path=status, cancel_path=cancel, nonce=nonce, grace_seconds=0.01,
    )

    assert worker.terminated is False
    assert worker.killed is False
    assert calls == ["reap"]
    assert details["powerpointCleanup"]["status"] == "not-attempted-unproven-ownership"
    assert details["workerDisposition"] == "left-running-for-cooperative-cleanup"
    assert details["residualRisk"] is True
    assert json.loads(cancel.read_text(encoding="utf-8"))["nonce"] == nonce


def test_timeout_stops_only_the_exact_proven_owned_pid(tmp_path: Path, monkeypatch):
    nonce = "owned"
    worker = _TimedOutWorker()
    status = tmp_path / "status.json"
    status.write_text(
        json.dumps(_worker_state(nonce=nonce, worker_pid=worker.pid, ownership="slideguard-owned")),
        encoding="utf-8",
    )
    captured = []

    def exact_cleanup(powerpoint):
        captured.append(powerpoint)
        return {"status": "stopped-proven-owned-process", "stopped": True}

    monkeypatch.setattr("slideguard.powerpoint._terminate_proven_owned_powerpoint", exact_cleanup)
    monkeypatch.setattr("slideguard.powerpoint._remove_owned_scratch", lambda state: {"status": "not-recorded"})

    details = _timeout_details(
        worker,
        status_path=status,
        cancel_path=tmp_path / "cancel.json",
        nonce=nonce,
        grace_seconds=0.01,
    )

    assert worker.terminated is True
    assert [item["pid"] for item in captured] == [9898]
    assert details["powerpointCleanup"]["stopped"] is True
    assert details["residualRisk"] is False


def test_timeout_reports_risk_when_exact_cleanup_recheck_refuses(tmp_path: Path, monkeypatch):
    nonce = "owned-but-changed"
    worker = _TimedOutWorker()
    status = tmp_path / "status.json"
    status.write_text(
        json.dumps(_worker_state(nonce=nonce, worker_pid=worker.pid, ownership="slideguard-owned")),
        encoding="utf-8",
    )
    monkeypatch.setattr(
        "slideguard.powerpoint._terminate_proven_owned_powerpoint",
        lambda value: {"status": "not-stopped-proof-mismatch", "stopped": False},
    )

    details = _timeout_details(
        worker,
        status_path=status,
        cancel_path=tmp_path / "cancel.json",
        nonce=nonce,
        grace_seconds=0.01,
    )

    assert details["scratchCleanup"]["status"] == "not-attempted"
    assert details["residualRisk"] is True


def test_invoke_maps_timeout_cleanup_evidence_into_export_error(tmp_path: Path, monkeypatch):
    worker = _TimedOutWorker(pid=77)
    monkeypatch.setattr("slideguard.powerpoint.subprocess.Popen", lambda *args, **kwargs: worker)
    monkeypatch.setattr(
        "slideguard.powerpoint._timeout_details",
        lambda *args, **kwargs: {"workerDisposition": "cooperative-exit", "residualRisk": False},
    )
    monkeypatch.setattr("slideguard.powerpoint._powershell", lambda: "powershell")

    with pytest.raises(ExportError) as caught:
        invoke({"mode": "probe"}, tmp_path, timeout=1)

    assert caught.value.stage == "export"
    assert caught.value.details["workerDisposition"] == "cooperative-exit"
    assert caught.value.details["timeoutSeconds"] == 1
    request = json.loads((tmp_path / "powerpoint-job.json").read_text(encoding="utf-8"))
    assert request["statusPath"].endswith("powerpoint-worker-status.json")
    assert request["cancelPath"].endswith("powerpoint-cancel.json")
    assert request["nonce"]


def test_cleanup_script_has_only_pid_exact_termination():
    script = (
        Path(__file__).parents[1]
        / "src" / "slideguard" / "resources" / "powerpoint_pid_cleanup.ps1"
    ).read_text(encoding="utf-8")
    assert "Stop-Process -Id $ProcessId" in script
    assert "Stop-Process -Name" not in script
    assert "taskkill" not in script.lower()
