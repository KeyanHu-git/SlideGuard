from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from slideguard import resume_lease as lease_module
from slideguard.contracts import validate_document
from slideguard.resume_lease import (
    RESUME_LEASE_FILENAME,
    ResumeInProgressError,
    ResumeLeaseInvalidError,
    ResumeLeaseLostError,
    acquire_resume_lease,
)
from slideguard.workspace import create_owned_workspace


def _workspace(tmp_path: Path):
    return create_owned_workspace(
        tmp_path / "w",
        prefix="resume",
        task_id="paper--aaaaaaaa--bbbbbbbb",
        kind="export-workspace",
        nonce="1" * 32,
    )


def _abandon_without_unlink(lease) -> None:
    lease_module._unlock_stream(lease._stream)
    lease._stream.close()
    lease._released = True


def _rewrite(path: Path, **changes: object) -> bytes:
    document = json.loads(path.read_text(encoding="utf-8"))
    document.update(changes)
    payload = lease_module.stable_json(document).encode("utf-8")
    path.write_bytes(payload)
    return payload


_PROCESS_CONTENDER = r"""
import os
import sys
import time
from pathlib import Path

from slideguard.resume_lease import ResumeInProgressError, acquire_resume_lease
from slideguard.workspace import open_owned_workspace

workspace_path = Path(sys.argv[1])
gate_path = Path(sys.argv[2])
done_path = Path(sys.argv[3])
outcome_path = Path(sys.argv[4])
lease_nonce = sys.argv[5]
mode = sys.argv[6]
workspace = open_owned_workspace(workspace_path, expected_kind="export-workspace")
while not gate_path.exists():
    time.sleep(0.005)
try:
    lease = acquire_resume_lease(workspace, lease_nonce=lease_nonce)
except ResumeInProgressError as exc:
    outcome_path.write_text(exc.code, encoding="utf-8")
else:
    outcome_path.write_text("writer", encoding="utf-8")
    if mode == "crash":
        os._exit(19)
    while not done_path.exists():
        time.sleep(0.005)
    lease.release()
"""


def _process_flags() -> int:
    return subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0


def _wait_for_files(paths: list[Path], processes: list[subprocess.Popen[bytes]], timeout: float = 15.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if all(path.exists() for path in paths):
            return
        failed = [process for process in processes if process.poll() not in (None, 0)]
        if failed:
            diagnostics = []
            for process in failed:
                stdout, stderr = process.communicate()
                diagnostics.append(
                    {
                        "returncode": process.returncode,
                        "stdout": stdout.decode(errors="replace"),
                        "stderr": stderr.decode(errors="replace"),
                    }
                )
            raise AssertionError(f"resume contender exited before reporting an outcome: {diagnostics}")
        time.sleep(0.01)
    raise AssertionError("timed out waiting for resume contender outcomes")


def _spawn_contender(
    workspace_path: Path,
    gate_path: Path,
    done_path: Path,
    outcome_path: Path,
    lease_nonce: str,
    mode: str = "hold",
) -> subprocess.Popen[bytes]:
    environment = os.environ.copy()
    source_root = str(Path(__file__).resolve().parents[1] / "src")
    existing_pythonpath = environment.get("PYTHONPATH")
    environment["PYTHONPATH"] = (
        source_root if not existing_pythonpath else source_root + os.pathsep + existing_pythonpath
    )
    return subprocess.Popen(
        [
            sys.executable,
            "-c",
            _PROCESS_CONTENDER,
            str(workspace_path),
            str(gate_path),
            str(done_path),
            str(outcome_path),
            lease_nonce,
            mode,
        ],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        creationflags=_process_flags(),
        env=environment,
    )


def test_lease_document_is_schema_valid_path_free_and_released_cleanly(tmp_path: Path):
    workspace = _workspace(tmp_path)

    lease = acquire_resume_lease(workspace, lease_nonce="2" * 32)
    lease.assert_current()
    document = json.loads(lease.path.read_text(encoding="utf-8"))

    validate_document(document, "resume-lease.schema.json")
    assert document["taskId"] == workspace.task_id
    assert document["workspaceNonce"] == workspace.nonce
    assert document["leaseNonce"] == "2" * 32
    assert str(tmp_path) not in json.dumps(document)

    lease.release()
    lease.release()
    assert not (workspace.path / RESUME_LEASE_FILENAME).exists()


def test_second_writer_is_rejected_without_changing_the_first_lease(tmp_path: Path):
    workspace = _workspace(tmp_path)
    first = acquire_resume_lease(workspace, lease_nonce="2" * 32)
    before = first.path.read_bytes()

    with pytest.raises(ResumeInProgressError) as error:
        acquire_resume_lease(workspace, lease_nonce="3" * 32)

    assert error.value.code == "RESUME_IN_PROGRESS"
    assert first.path.read_bytes() == before
    first.assert_current()
    first.release()


def _contend(workspace, count: int, *, stale_owner: bool = False) -> list[str]:
    start = threading.Barrier(count)
    condition = threading.Condition()
    outcomes: list[str] = []

    def contender(index: int) -> None:
        start.wait(timeout=10)
        try:
            lease = acquire_resume_lease(
                workspace,
                lease_nonce=f"{index + 10:032x}",
                process_is_active=(lambda _document: False) if stale_owner else lease_module.owner_process_is_active,
            )
        except ResumeInProgressError as exc:
            with condition:
                outcomes.append(exc.code)
                condition.notify_all()
            return
        with lease:
            with condition:
                outcomes.append("writer")
                condition.notify_all()
                condition.wait_for(lambda: len(outcomes) == count, timeout=10)

    with ThreadPoolExecutor(max_workers=count) as pool:
        futures = [pool.submit(contender, index) for index in range(count)]
        for future in futures:
            future.result(timeout=20)
    return outcomes


def test_atomic_first_claim_elects_exactly_one_writer(tmp_path: Path):
    workspace = _workspace(tmp_path)

    outcomes = _contend(workspace, 8)

    assert outcomes.count("writer") == 1
    assert outcomes.count("RESUME_IN_PROGRESS") == 7
    assert not (workspace.path / RESUME_LEASE_FILENAME).exists()


def test_stale_takeover_race_elects_exactly_one_new_writer(tmp_path: Path):
    workspace = _workspace(tmp_path)
    stale = acquire_resume_lease(workspace, lease_nonce="2" * 32)
    _abandon_without_unlink(stale)

    outcomes = _contend(workspace, 8, stale_owner=True)

    assert outcomes.count("writer") == 1
    assert outcomes.count("RESUME_IN_PROGRESS") == 7
    assert not (workspace.path / RESUME_LEASE_FILENAME).exists()


def test_independent_processes_elect_exactly_one_writer(tmp_path: Path):
    workspace = _workspace(tmp_path)
    gate = tmp_path / "go"
    done = tmp_path / "done"
    outcomes = [tmp_path / "outcome-a", tmp_path / "outcome-b"]
    processes = [
        _spawn_contender(workspace.path, gate, done, outcomes[0], "2" * 32),
        _spawn_contender(workspace.path, gate, done, outcomes[1], "3" * 32),
    ]
    try:
        gate.touch()
        _wait_for_files(outcomes, processes)
        values = [path.read_text(encoding="utf-8") for path in outcomes]
        assert values.count("writer") == 1
        assert values.count("RESUME_IN_PROGRESS") == 1
        done.touch()
        completed = [process.communicate(timeout=10) for process in processes]
        assert [process.returncode for process in processes] == [0, 0], completed
        assert not (workspace.path / RESUME_LEASE_FILENAME).exists()
    finally:
        done.touch(exist_ok=True)
        for process in processes:
            if process.poll() is None:
                process.kill()
            process.communicate()


def test_crashed_process_releases_mutex_but_leaves_a_provable_stale_lease(tmp_path: Path):
    workspace = _workspace(tmp_path)
    gate = tmp_path / "go"
    done = tmp_path / "unused"
    outcome = tmp_path / "crash-outcome"
    process = _spawn_contender(workspace.path, gate, done, outcome, "2" * 32, mode="crash")
    gate.touch()
    _wait_for_files([outcome], [process])
    _stdout, stderr = process.communicate(timeout=10)
    assert process.returncode == 19, stderr.decode(errors="replace")
    assert outcome.read_text(encoding="utf-8") == "writer"
    assert (workspace.path / RESUME_LEASE_FILENAME).exists()

    replacement = acquire_resume_lease(workspace, lease_nonce="3" * 32)

    assert replacement.nonce == "3" * 32
    replacement.assert_current()
    replacement.release()


def test_free_lock_is_not_taken_over_when_recorded_owner_is_still_active(tmp_path: Path):
    workspace = _workspace(tmp_path)
    old = acquire_resume_lease(workspace, lease_nonce="2" * 32)
    path = old.path
    before = path.read_bytes()
    _abandon_without_unlink(old)

    with pytest.raises(ResumeInProgressError) as error:
        acquire_resume_lease(
            workspace,
            lease_nonce="3" * 32,
            process_is_active=lambda _document: True,
        )

    assert error.value.details["reason"] == "active-owner"
    assert path.read_bytes() == before


def test_owner_inspection_failure_preserves_the_existing_lease(tmp_path: Path):
    workspace = _workspace(tmp_path)
    old = acquire_resume_lease(workspace, lease_nonce="2" * 32)
    path = old.path
    before = path.read_bytes()
    _abandon_without_unlink(old)

    def cannot_inspect(_document: dict[str, object]) -> bool:
        raise PermissionError("injected")

    with pytest.raises(ResumeInProgressError) as error:
        acquire_resume_lease(workspace, lease_nonce="3" * 32, process_is_active=cannot_inspect)

    assert error.value.details["reason"] == "owner-inspection-failed"
    assert path.read_bytes() == before


@pytest.mark.parametrize("payload", [b"", b'{"schemaVersion":"1.0"', b"not-json"])
def test_invalid_or_truncated_lease_is_preserved_and_rejected(tmp_path: Path, payload: bytes):
    workspace = _workspace(tmp_path)
    path = workspace.path / RESUME_LEASE_FILENAME
    path.write_bytes(payload)

    with pytest.raises(ResumeLeaseInvalidError) as error:
        acquire_resume_lease(workspace, lease_nonce="3" * 32)

    assert error.value.code == "RESUME_LEASE_INVALID"
    assert path.read_bytes() == payload


def test_pid_reuse_token_mismatch_allows_stale_takeover(tmp_path: Path):
    workspace = _workspace(tmp_path)
    old = acquire_resume_lease(workspace, lease_nonce="2" * 32)
    path = old.path
    _abandon_without_unlink(old)
    _rewrite(path, processId=os.getpid(), processStartToken="win:reused-pid-old-token")

    replacement = acquire_resume_lease(workspace, lease_nonce="3" * 32)

    assert replacement.nonce == "3" * 32
    replacement.assert_current()
    replacement.release()


def test_unavailable_start_token_for_live_pid_fails_closed(tmp_path: Path):
    workspace = _workspace(tmp_path)
    old = acquire_resume_lease(workspace, lease_nonce="2" * 32)
    path = old.path
    _abandon_without_unlink(old)
    before = _rewrite(path, processId=os.getpid(), processStartToken="unavailable")

    with pytest.raises(ResumeInProgressError) as error:
        acquire_resume_lease(workspace, lease_nonce="3" * 32)

    assert error.value.details["reason"] == "active-owner"
    assert path.read_bytes() == before


def test_reparse_lease_path_is_rejected_without_traversal(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    workspace = _workspace(tmp_path)
    path = workspace.path / RESUME_LEASE_FILENAME
    path.write_text("outside", encoding="utf-8")
    real_detector = lease_module._is_reparse_point
    monkeypatch.setattr(
        lease_module,
        "_is_reparse_point",
        lambda candidate: Path(candidate) == path or real_detector(candidate),
    )

    with pytest.raises(ResumeLeaseInvalidError, match="reparse"):
        acquire_resume_lease(workspace, lease_nonce="3" * 32)

    assert path.read_text(encoding="utf-8") == "outside"


def test_assert_current_detects_path_identity_change_and_never_unlinks_it(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    workspace = _workspace(tmp_path)
    lease = acquire_resume_lease(workspace, lease_nonce="2" * 32)
    monkeypatch.setattr(lease_module, "_same_open_file", lambda _stream, _path: False)

    with pytest.raises(ResumeLeaseLostError):
        lease.assert_current()
    with pytest.raises(ResumeLeaseLostError):
        lease.release()

    assert lease.path.exists()
    assert lease._stream.closed


def test_candidate_replace_failure_leaves_no_fixed_or_candidate_files(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    workspace = _workspace(tmp_path)

    def fail_replace(*_args, **_kwargs) -> None:
        raise PermissionError("injected")

    monkeypatch.setattr(lease_module.os, "replace", fail_replace)

    with pytest.raises(lease_module.ResumeLeaseError) as error:
        acquire_resume_lease(workspace, lease_nonce="2" * 32)

    assert error.value.code == "RESUME_LEASE_FAILED"
    assert not (workspace.path / RESUME_LEASE_FILENAME).exists()
    assert not list(workspace.path.glob(".slideguard-resume-lease.*.candidate"))
