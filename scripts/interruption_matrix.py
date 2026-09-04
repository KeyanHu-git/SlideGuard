from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
import subprocess
import sys
import tempfile
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Iterable


ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from slideguard import PIPELINE_REVISION, __version__  # noqa: E402
from slideguard import checkpoint as checkpoint_module  # noqa: E402
from slideguard import engine  # noqa: E402
from slideguard.cancellation import CancellationToken  # noqa: E402
from slideguard.checkpoint import (  # noqa: E402
    CHECKPOINT_FILENAME,
    CheckpointCursor,
    CheckpointIdentity,
    CheckpointJournal,
    CheckpointPhase,
    CheckpointReadError,
    CheckpointStatus,
    JobCheckpoint,
    load_checkpoint,
)
from slideguard.errors import CancelledError  # noqa: E402
from slideguard.resume import build_resume_plan  # noqa: E402
from slideguard.util import sha256_file, stable_json  # noqa: E402
from slideguard.workspace import OwnedWorkspace, create_owned_workspace, delete_owned_workspace  # noqa: E402


SEED = 20260904
SLIDES = (2, 5)
SOURCE_HASH = "a" * 64
REQUEST_FINGERPRINT = "sha256:" + "b" * 64
TASK_ID = "interruption-matrix"
WORKER_TIMEOUT_SECONDS = 20.0


@dataclass(frozen=True, slots=True)
class Boundary:
    label: str
    phase: CheckpointPhase
    status: CheckpointStatus
    cursor: CheckpointCursor | None
    sequence: int


def boundaries(slides: tuple[int, ...] = SLIDES) -> tuple[Boundary, ...]:
    result = [
        Boundary("discover", CheckpointPhase.DISCOVER, CheckpointStatus.COMPLETE, None, 0),
        Boundary("preflight", CheckpointPhase.PREFLIGHT, CheckpointStatus.COMPLETE, None, 1),
        Boundary("inventory", CheckpointPhase.INVENTORY, CheckpointStatus.COMPLETE, None, 2),
    ]
    sequence = 3
    for ordinal, slide in enumerate(slides, 1):
        cursor = CheckpointCursor(ordinal, slide)
        for label, phase in (
            ("native-export", CheckpointPhase.NATIVE_EXPORT),
            ("patch", CheckpointPhase.PATCH),
            ("validate", CheckpointPhase.VALIDATE),
        ):
            result.append(Boundary(f"{label}:p{ordinal}", phase, CheckpointStatus.COMPLETE, cursor, sequence))
            sequence += 1
    result.extend(
        [
            Boundary("package", CheckpointPhase.PACKAGE, CheckpointStatus.COMPLETE, None, sequence),
            Boundary("publish:pending", CheckpointPhase.PUBLISH, CheckpointStatus.PENDING, None, sequence + 1),
            Boundary("publish:complete", CheckpointPhase.PUBLISH, CheckpointStatus.COMPLETE, None, sequence + 2),
        ]
    )
    return tuple(result)


def _case_nonce(case_id: str) -> str:
    return hashlib.sha256(f"{SEED}:{case_id}".encode("utf-8")).hexdigest()[:32]


def _new_journal(root: Path, case_id: str) -> tuple[OwnedWorkspace, CheckpointJournal]:
    nonce = _case_nonce(case_id)
    workspace = create_owned_workspace(
        root,
        prefix="matrix",
        task_id=TASK_ID,
        kind="export-workspace",
        nonce=nonce,
    )
    identity = CheckpointIdentity.create(
        task_id=TASK_ID,
        workspace_nonce=nonce,
        request_fingerprint=REQUEST_FINGERPRINT,
        source_name="synthetic.pptx",
        source_sha256=SOURCE_HASH,
        tool_version=__version__,
        pipeline_revision=PIPELINE_REVISION,
    )
    return workspace, CheckpointJournal(workspace, identity, SLIDES)


def _write_stage_file(workspace: OwnedWorkspace, boundary: Boundary, relative: str) -> Path:
    artifact = workspace.path / Path(*relative.split("/"))
    artifact.parent.mkdir(parents=True, exist_ok=True)
    artifact.write_bytes(
        stable_json(
            {
                "seed": SEED,
                "sequence": boundary.sequence,
                "phase": boundary.phase.value,
                "status": boundary.status.value,
                "cursor": boundary.cursor.to_document() if boundary.cursor else None,
                "artifact": relative,
            }
        ).encode("utf-8")
    )
    return artifact


def _artifacts(workspace: OwnedWorkspace, boundary: Boundary) -> list[tuple[str, Path]]:
    base = f"stage-{boundary.sequence:03d}"
    if boundary.phase == CheckpointPhase.NATIVE_EXPORT:
        return [
            ("native-pdf", _write_stage_file(workspace, boundary, f"{base}/native.pdf")),
            ("reference-png", _write_stage_file(workspace, boundary, f"{base}/reference.png")),
        ]
    if boundary.phase == CheckpointPhase.PATCH:
        return [
            ("pdf", _write_stage_file(workspace, boundary, f"{base}/patched.pdf")),
            ("raw-svg", _write_stage_file(workspace, boundary, f"{base}/raw.svg")),
            ("svg", _write_stage_file(workspace, boundary, f"{base}/patched.svg")),
        ]
    if boundary.phase == CheckpointPhase.VALIDATE:
        return [
            ("png", _write_stage_file(workspace, boundary, f"{base}/accepted.png")),
            ("evidence", _write_stage_file(workspace, boundary, f"{base}/evidence/render.png")),
        ]
    if boundary.phase == CheckpointPhase.PACKAGE:
        package = workspace.path / "package"
        _synthetic_package(package, TASK_ID)
        return [("package-file", path) for path in sorted(package.rglob("*")) if path.is_file()]
    return [
        ("stage-artifact", _write_stage_file(workspace, boundary, f"{base}/complete.json")),
    ]


def _advance(journal: CheckpointJournal, workspace: OwnedWorkspace, boundary: Boundary) -> JobCheckpoint:
    return journal.advance(
        boundary.phase,
        status=boundary.status,
        cursor=boundary.cursor,
        artifacts=_artifacts(workspace, boundary),
    )


def _advance_before(journal: CheckpointJournal, workspace: OwnedWorkspace, target_index: int) -> JobCheckpoint | None:
    checkpoint = None
    for boundary in boundaries()[:target_index]:
        checkpoint = _advance(journal, workspace, boundary)
    return checkpoint


def _observe(workspace: OwnedWorkspace) -> JobCheckpoint | None:
    if not (workspace.path / CHECKPOINT_FILENAME).exists():
        return None
    return load_checkpoint(workspace)


def _resume_task(workspace: OwnedWorkspace) -> engine.ExportTaskModel:
    output_root = workspace.root / "published"
    return engine.ExportTaskModel(
        source=workspace.root / "synthetic.pptx",
        source_sha256=SOURCE_HASH,
        slides=SLIDES,
        config={},
        request_fingerprint=REQUEST_FINGERPRINT,
        slug="synthetic",
        job_id=TASK_ID,
        output_root=output_root,
        final_dir=output_root / TASK_ID,
    )


def _resume_evidence(workspace: OwnedWorkspace, observed: JobCheckpoint | None) -> dict[str, object]:
    before = _tree_hashes(workspace.path)
    plan = build_resume_plan(workspace, _resume_task(workspace), compact_svg=False)
    repeated = build_resume_plan(workspace, _resume_task(workspace), compact_svg=False)
    after = _tree_hashes(workspace.path)
    deterministic = plan == repeated
    read_only = before == after
    if observed is None:
        expected_status = "rejected"
        expected_sequence = None
        correct = (
            plan["status"] == expected_status
            and plan["resumeFromSequence"] is None
            and plan["error"]["code"] == "CHECKPOINT_READ_FAILED"
            and deterministic
            and read_only
        )
        first_reason = plan["error"]["code"]
    else:
        expected_status = "resumable"
        expected_sequence = (
            observed.state.sequence
            if observed.state.phase == CheckpointPhase.PUBLISH
            else observed.state.sequence + 1
        )
        resume_step = next(
            (step for step in plan["steps"] if step["sequence"] == plan["resumeFromSequence"]),
            None,
        )
        first_reason = resume_step["reasonCode"] if resume_step else None
        correct = (
            plan["status"] == expected_status
            and plan["resumeFromSequence"] == expected_sequence
            and all(
                step["action"] == "reuse"
                for step in plan["steps"]
                if step["sequence"] < expected_sequence
            )
            and all(
                step["action"] == "recompute"
                for step in plan["steps"]
                if step["sequence"] >= expected_sequence
            )
            and deterministic
            and read_only
        )
    return {
        "resumePlanStatus": plan["status"],
        "resumePlanKey": plan["planKey"],
        "resumeFromSequence": plan["resumeFromSequence"],
        "expectedResumeFromSequence": expected_sequence,
        "firstResumeReasonCode": first_reason,
        "resumePlanDeterministic": deterministic,
        "resumePlanReadOnly": read_only,
        "resumeDecisionCorrect": correct,
    }


def _case_result(
    *,
    boundary: Boundary,
    fault_kind: str,
    observed: JobCheckpoint | None,
    expected_sequence: int | None,
    final_state: str = "absent",
    extra: dict[str, object] | None = None,
) -> dict[str, object]:
    actual_sequence = observed.state.sequence if observed else None
    passed = actual_sequence == expected_sequence and final_state != "partial"
    result: dict[str, object] = {
        "injectionPoint": boundary.label,
        "phase": boundary.phase.value,
        "faultKind": fault_kind,
        "expectedCommittedSequence": expected_sequence,
        "observedCommittedSequence": actual_sequence,
        "checkpointValid": observed is not None,
        "finalDirectoryState": final_state,
        "verdict": "PASS" if passed else "FAIL",
    }
    if extra:
        result.update(extra)
    if result.get("resumeDecisionCorrect") is False:
        result["verdict"] = "FAIL"
    return result


def _run_in_process_case(base: Path, target_index: int, fault_kind: str) -> dict[str, object]:
    target = boundaries()[target_index]
    case_id = f"{fault_kind}-{target_index}"
    workspace, journal = _new_journal(base / case_id, case_id)
    previous = _advance_before(journal, workspace, target_index)
    uncommitted_artifacts = _artifacts(workspace, target)
    try:
        if fault_kind == "cooperative-cancel":
            token = CancellationToken()
            token.cancel()
            token.throw_if_cancelled()
        elif fault_kind == "python-exception":
            raise RuntimeError("deterministic injected stage failure")
        else:
            raise ValueError(f"unknown in-process fault kind: {fault_kind}")
    except (CancelledError, RuntimeError):
        observed = _observe(workspace)
    else:  # pragma: no cover - a failed injector must make the matrix fail loudly
        raise AssertionError("fault injector did not interrupt the stage")
    expected = previous.state.sequence if previous else None
    resume_evidence = _resume_evidence(workspace, observed)
    result = _case_result(
        boundary=target,
        fault_kind=fault_kind,
        observed=observed,
        expected_sequence=expected,
        extra={
            "uncommittedStageArtifactIgnored": all(path.is_file() for _kind, path in uncommitted_artifacts),
            **resume_evidence,
        },
    )
    delete_owned_workspace(workspace)
    return result


def _signal_and_block(event: Path, payload: dict[str, object]) -> None:
    event.parent.mkdir(parents=True, exist_ok=True)
    with open(event, "x", encoding="utf-8", newline="\n") as stream:
        stream.write(stable_json(payload))
        stream.flush()
        os.fsync(stream.fileno())
    while True:
        time.sleep(1)


def _checkpoint_worker(base: Path, target_index: int, case_id: str, event: Path) -> int:
    workspace, journal = _new_journal(base, case_id)
    _advance_before(journal, workspace, target_index)
    target = boundaries()[target_index]
    real_write = checkpoint_module._write_temp_file

    def stop_after_durable_temp(path: Path, payload: bytes) -> None:
        real_write(path, payload)
        _signal_and_block(
            event,
            {
                "kind": "checkpoint-before-rename",
                "sequence": target.sequence,
                "workspace": workspace.path.name,
            },
        )

    checkpoint_module._write_temp_file = stop_after_durable_temp
    _advance(journal, workspace, target)
    return 70


def _worker_environment() -> dict[str, str]:
    environment = dict(os.environ)
    old = environment.get("PYTHONPATH")
    environment["PYTHONPATH"] = str(SRC) if not old else str(SRC) + os.pathsep + old
    return environment


def _wait_for_event(process: subprocess.Popen[bytes], event: Path) -> None:
    deadline = time.monotonic() + WORKER_TIMEOUT_SECONDS
    while time.monotonic() < deadline:
        if event.is_file():
            return
        if process.poll() is not None:
            stdout, stderr = process.communicate()
            raise RuntimeError(
                f"fault worker exited before its barrier: rc={process.returncode}; "
                f"stdout={len(stdout)} bytes; stderr={len(stderr)} bytes"
            )
        time.sleep(0.01)
    process.kill()
    process.wait(timeout=5)
    raise TimeoutError("fault worker did not reach its deterministic barrier")


def _kill_at_barrier(arguments: list[str], event: Path) -> int:
    process = subprocess.Popen(
        [sys.executable, str(Path(__file__).resolve()), *arguments],
        cwd=str(ROOT),
        env=_worker_environment(),
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    try:
        _wait_for_event(process, event)
        process.kill()
        return process.wait(timeout=5)
    finally:
        if process.poll() is None:
            process.kill()
            process.wait(timeout=5)


def _run_hard_termination_case(base: Path, target_index: int) -> dict[str, object]:
    target = boundaries()[target_index]
    case_id = f"hard-termination-{target_index}"
    case_root = base / case_id
    event = base / "events" / f"{case_id}.json"
    return_code = _kill_at_barrier(
        [
            "--checkpoint-worker",
            "--workspace-root",
            str(case_root),
            "--target-index",
            str(target_index),
            "--case-id",
            case_id,
            "--event",
            str(event),
        ],
        event,
    )
    nonce = _case_nonce(case_id)
    workspace = OwnedWorkspace(
        path=case_root / f"matrix-{nonce[:12]}",
        root=case_root,
        nonce=nonce,
        task_id=TASK_ID,
        kind="export-workspace",
    )
    observed = _observe(workspace)
    expected_sequence = target_index - 1 if target_index else None
    temporary_count = len(list(workspace.path.glob(".job-state.*.tmp")))
    resume_evidence = _resume_evidence(workspace, observed)
    result = _case_result(
        boundary=target,
        fault_kind="process-terminated",
        observed=observed,
        expected_sequence=expected_sequence,
        extra={
            "workerReturnCode": return_code,
            "durableUncommittedTempFiles": temporary_count,
            "temporaryCheckpointIgnored": temporary_count == 1,
            **resume_evidence,
        },
    )
    if temporary_count != 1 or return_code == 0:
        result["verdict"] = "FAIL"
    delete_owned_workspace(workspace)
    return result


def _run_compound_corruption_case(base: Path) -> dict[str, object]:
    case_id = "checkpoint-and-artifact-corruption"
    workspace, journal = _new_journal(base / case_id, case_id)
    checkpoint = _advance_before(journal, workspace, 9)
    assert checkpoint is not None
    recorded = next(item for item in checkpoint.artifacts if item.kind == "native-pdf")
    recorded_path = workspace.path / Path(*recorded.path.split("/"))
    payload = bytearray(recorded_path.read_bytes())
    payload[0] ^= 0x01
    recorded_path.write_bytes(payload)
    (workspace.path / CHECKPOINT_FILENAME).write_bytes(b'{"schemaVersion":"1.0"')

    plan = build_resume_plan(workspace, _resume_task(workspace), compact_svg=False)
    passed = (
        plan["status"] == "rejected"
        and plan["error"]["code"] == "CHECKPOINT_READ_FAILED"
        and plan["reusedThroughSequence"] is None
        and not _resume_task(workspace).final_dir.exists()
    )
    result = {
        "injectionPoint": "resume-plan:compound-corruption",
        "phase": "RESUME_PLAN",
        "faultKind": "checkpoint-and-artifact-corruption",
        "checkpointMutation": "truncated-json",
        "artifactMutation": "same-size-different-hash",
        "resumePlanStatus": plan["status"],
        "resumePlanReasonCode": plan["error"]["code"],
        "reusedThroughSequence": plan["reusedThroughSequence"],
        "finalDirectoryState": "absent",
        "verdict": "PASS" if passed else "FAIL",
    }
    delete_owned_workspace(workspace)
    return result


def _run_concurrent_planner_read_case(base: Path) -> dict[str, object]:
    case_id = "concurrent-planner-readers"
    workspace, journal = _new_journal(base / case_id, case_id)
    _advance_before(journal, workspace, 11)
    before = _tree_hashes(workspace.path)
    barrier = threading.Barrier(3)

    def read_plan() -> str:
        barrier.wait(timeout=5)
        return stable_json(build_resume_plan(workspace, _resume_task(workspace), compact_svg=False))

    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(read_plan) for _ in range(2)]
        barrier.wait(timeout=5)
        plans = [future.result(timeout=10) for future in futures]
    after = _tree_hashes(workspace.path)
    plan = json.loads(plans[0])
    passed = (
        plans[0] == plans[1]
        and before == after
        and plan["status"] == "resumable"
        and plan["resumeFromSequence"] == boundaries()[-2].sequence
    )
    result = {
        "injectionPoint": "resume-plan:concurrent-readers",
        "phase": "RESUME_PLAN",
        "faultKind": "concurrent-read-only-planners",
        "readerCount": 2,
        "scope": "planner-only; writer serialization is KEY-199",
        "plansByteIdentical": plans[0] == plans[1],
        "workspaceReadOnly": before == after,
        "resumeFromSequence": plan["resumeFromSequence"],
        "verdict": "PASS" if passed else "FAIL",
    }
    delete_owned_workspace(workspace)
    return result


def _tree_hashes(root: Path) -> dict[str, str]:
    return {
        path.relative_to(root).as_posix(): sha256_file(path)
        for path in sorted(root.rglob("*"))
        if path.is_file()
    }


def _synthetic_package(path: Path, job_id: str) -> dict[str, str]:
    path.mkdir(parents=True)
    (path / "artifact.bin").write_bytes(hashlib.sha256(f"{SEED}:artifact".encode()).digest() * 8)
    (path / "qa-report.json").write_text(
        stable_json({"schemaVersion": "1.0", "verdict": "PASS", "seed": SEED}),
        encoding="utf-8",
    )
    (path / "report.html").write_text("<!doctype html><title>PASS</title>", encoding="utf-8")
    (path / "junit.xml").write_text(
        '<testsuite name="interruption-matrix" tests="1" failures="0"/>',
        encoding="utf-8",
    )
    (path / "manifest.json").write_text(
        stable_json({"schemaVersion": "1.0", "jobId": job_id, "verdict": "PASS", "artifacts": []}),
        encoding="utf-8",
    )
    checksum_targets = [item for item in sorted(path.rglob("*")) if item.is_file()]
    (path / "checksums.sha256").write_text(
        "".join(f"{sha256_file(item)}  {item.relative_to(path).as_posix()}\n" for item in checksum_targets),
        encoding="ascii",
    )
    return _tree_hashes(path)


def _publish_worker(package: Path, output: Path, final: Path, job_id: str, event: Path, moment: str) -> int:
    real_replace = engine.os.replace

    def stop_at_publish_rename(source: str | bytes | os.PathLike[str] | os.PathLike[bytes], destination) -> None:
        if moment == "after":
            real_replace(source, destination)
        _signal_and_block(event, {"kind": f"publish-{moment}-rename", "jobId": job_id})

    engine.os.replace = stop_at_publish_rename
    engine._publish_package(package, output, final, job_id, None)
    return 71


def _run_publish_termination_case(base: Path, moment: str) -> dict[str, object]:
    case_root = base / f"publish-{moment}-rename"
    package = case_root / "package"
    output = case_root / "output"
    job_id = "synthetic--aaaaaaaa--bbbbbbbb"
    final = output / job_id
    expected_hashes = _synthetic_package(package, job_id)
    event = base / "events" / f"publish-{moment}.json"
    return_code = _kill_at_barrier(
        [
            "--publish-worker",
            "--package",
            str(package),
            "--output-root",
            str(output),
            "--final-dir",
            str(final),
            "--job-id",
            job_id,
            "--event",
            str(event),
            "--rename-moment",
            moment,
        ],
        event,
    )
    state_at_interrupt = "complete" if final.is_dir() and _tree_hashes(final) == expected_hashes else (
        "partial" if final.exists() else "absent"
    )
    staging = sorted(output.glob(".sg-publish-*"))
    engine._publish_package(package, output, final, job_id, None)
    recovered_hashes = _tree_hashes(final)
    passed = (
        state_at_interrupt == ("absent" if moment == "before" else "complete")
        and recovered_hashes == expected_hashes
        and return_code != 0
    )
    return {
        "injectionPoint": f"publish:{moment}-atomic-rename",
        "phase": CheckpointPhase.PUBLISH.value,
        "faultKind": "process-terminated",
        "workerReturnCode": return_code,
        "finalDirectoryStateAtInterrupt": state_at_interrupt,
        "hiddenStagingDirectoriesAtInterrupt": len(staging),
        "recoveredPackageMatchesCleanRun": recovered_hashes == expected_hashes,
        "publishedManifestVerdict": json.loads((final / "manifest.json").read_text(encoding="utf-8"))["verdict"],
        "verdict": "PASS" if passed else "FAIL",
    }


class _CancelBeforePublishRename:
    def __init__(self) -> None:
        self.checks = 0

    def throw_if_cancelled(self) -> None:
        self.checks += 1
        if self.checks == 2:
            raise CancelledError("deterministic cancellation before publish rename", stage="cancellation")


def _run_publish_cancellation_case(base: Path, moment: str) -> dict[str, object]:
    case_root = base / f"publish-cancel-{moment}"
    package = case_root / "package"
    output = case_root / "output"
    job_id = "synthetic--aaaaaaaa--bbbbbbbb"
    final = output / job_id
    expected_hashes = _synthetic_package(package, job_id)
    cancellation_raised = False
    if moment == "before":
        try:
            engine._publish_package(package, output, final, job_id, _CancelBeforePublishRename())
        except CancelledError:
            cancellation_raised = True
    else:
        token = CancellationToken()
        real_replace = engine.os.replace

        def cancel_after_replace(source, destination) -> None:
            real_replace(source, destination)
            token.cancel()

        engine.os.replace = cancel_after_replace
        try:
            engine._publish_package(package, output, final, job_id, token)
        finally:
            engine.os.replace = real_replace
    state = "complete" if final.is_dir() and _tree_hashes(final) == expected_hashes else (
        "partial" if final.exists() else "absent"
    )
    staging_count = len(list(output.glob(".sg-publish-*"))) if output.exists() else 0
    expected_state = "absent" if moment == "before" else "complete"
    passed = (
        state == expected_state
        and staging_count == 0
        and cancellation_raised == (moment == "before")
    )
    return {
        "injectionPoint": f"publish:{moment}-atomic-rename",
        "phase": CheckpointPhase.PUBLISH.value,
        "faultKind": "cooperative-cancel",
        "cancellationRaised": cancellation_raised,
        "finalDirectoryStateAtInterrupt": state,
        "hiddenStagingDirectoriesAtInterrupt": staging_count,
        "publishedPackageMatchesCleanRun": state == "complete" and _tree_hashes(final) == expected_hashes,
        "verdict": "PASS" if passed else "FAIL",
    }


def _environment_document() -> dict[str, str]:
    document = {
        "implementation": platform.python_implementation(),
        "python": f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}",
        "system": platform.system(),
        "release": platform.release(),
        "machine": platform.machine(),
        "slideguard": __version__,
        "pipelineRevision": PIPELINE_REVISION,
    }
    document["fingerprint"] = "sha256:" + hashlib.sha256(stable_json(document).encode("utf-8")).hexdigest()
    return document


def _git_commit() -> str:
    result = subprocess.run(
        ["git", "rev-parse", "--verify", "HEAD"],
        cwd=str(ROOT),
        check=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
    )
    return result.stdout.strip()


def run_matrix(work_root: Path, *, commit_sha: str | None = None) -> dict[str, object]:
    work_root.mkdir(parents=True, exist_ok=False)
    cases: list[dict[str, object]] = []
    for target_index, _boundary in enumerate(boundaries()):
        for fault_kind in ("cooperative-cancel", "python-exception"):
            cases.append(_run_in_process_case(work_root / "in-process", target_index, fault_kind))
        cases.append(_run_hard_termination_case(work_root / "hard", target_index))
    cases.extend(
        [
            _run_compound_corruption_case(work_root / "resume"),
            _run_concurrent_planner_read_case(work_root / "resume"),
            _run_publish_cancellation_case(work_root / "publication", "before"),
            _run_publish_cancellation_case(work_root / "publication", "after"),
            _run_publish_termination_case(work_root / "publication", "before"),
            _run_publish_termination_case(work_root / "publication", "after"),
        ]
    )
    verdict = "PASS" if all(case["verdict"] == "PASS" for case in cases) else "FAIL"
    return {
        "schemaVersion": "1.0",
        "matrixContract": "KEY-176",
        "seed": SEED,
        "commitSha": commit_sha or _git_commit(),
        "environment": _environment_document(),
        "coverage": {
            "checkpointBoundaries": len(boundaries()),
            "faultKindsPerBoundary": 3,
            "publicationRenameMoments": ["before", "after"],
            "publicationRenameFaultKinds": ["cooperative-cancel", "process-terminated"],
            "compoundCorruptionCases": 1,
            "concurrentReadOnlyPlannerCases": 1,
            "cases": len(cases),
        },
        "cases": cases,
        "verdict": verdict,
    }


def _atomic_report(path: Path, document: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    payload = stable_json(document).encode("utf-8")
    try:
        with open(temporary, "xb") as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run SlideGuard's deterministic interruption matrix")
    parser.add_argument("--output", type=Path)
    parser.add_argument("--work-root", type=Path)
    parser.add_argument("--commit-sha")
    parser.add_argument("--checkpoint-worker", action="store_true")
    parser.add_argument("--publish-worker", action="store_true")
    parser.add_argument("--workspace-root", type=Path)
    parser.add_argument("--target-index", type=int)
    parser.add_argument("--case-id")
    parser.add_argument("--event", type=Path)
    parser.add_argument("--package", type=Path)
    parser.add_argument("--output-root", type=Path)
    parser.add_argument("--final-dir", type=Path)
    parser.add_argument("--job-id")
    parser.add_argument("--rename-moment", choices=("before", "after"))
    return parser


def main(argv: Iterable[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    if args.checkpoint_worker:
        return _checkpoint_worker(args.workspace_root, args.target_index, args.case_id, args.event)
    if args.publish_worker:
        return _publish_worker(
            args.package,
            args.output_root,
            args.final_dir,
            args.job_id,
            args.event,
            args.rename_moment,
        )
    if args.output is None:
        raise SystemExit("--output is required")
    if args.work_root is not None:
        report = run_matrix(args.work_root, commit_sha=args.commit_sha)
    else:
        with tempfile.TemporaryDirectory(prefix="slideguard-interruption-") as temporary:
            report = run_matrix(Path(temporary) / "work", commit_sha=args.commit_sha)
    _atomic_report(args.output.resolve(), report)
    print(stable_json({"report": str(args.output.resolve()), "verdict": report["verdict"]}))
    return 0 if report["verdict"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
