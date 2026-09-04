from __future__ import annotations

import hashlib
import os
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any, Iterable

from . import PIPELINE_REVISION, __version__
from .checkpoint import (
    CheckpointArtifact,
    CheckpointError,
    CheckpointIdentity,
    CheckpointPhase,
    CheckpointStatus,
    JobCheckpoint,
    load_checkpoint,
)
from .engine import ExportTaskModel, build_export_task_model
from .errors import InputError
from .model import Verdict
from .util import sha256_file, stable_json
from .verify import verify_package
from .workspace import OwnedWorkspace, WorkspaceSafetyError, _is_reparse_point, open_owned_workspace


RESUME_PLAN_SCHEMA_VERSION = "1.0"


@dataclass(frozen=True, slots=True)
class _Requirement:
    kind: str | None = None
    relative_path: str | None = None
    minimum: int = 1


@dataclass(frozen=True, slots=True)
class _Stage:
    sequence: int
    phase: CheckpointPhase
    status: CheckpointStatus
    output_ordinal: int | None = None
    source_slide: int | None = None
    requirements: tuple[_Requirement, ...] = ()

    def cursor_document(self) -> dict[str, int] | None:
        if self.output_ordinal is None or self.source_slide is None:
            return None
        return {
            "outputOrdinal": self.output_ordinal,
            "sourceSlide": self.source_slide,
        }


def _expected_stages(
    task: ExportTaskModel,
    *,
    compact_svg: bool,
) -> tuple[_Stage, ...]:
    stages: list[_Stage] = [
        _Stage(0, CheckpointPhase.DISCOVER, CheckpointStatus.COMPLETE),
        _Stage(1, CheckpointPhase.PREFLIGHT, CheckpointStatus.COMPLETE),
        _Stage(2, CheckpointPhase.INVENTORY, CheckpointStatus.COMPLETE),
    ]
    for ordinal, slide in enumerate(task.slides, 1):
        native_sequence = 3 + (ordinal - 1) * 3
        stages.append(_Stage(
            native_sequence,
            CheckpointPhase.NATIVE_EXPORT,
            CheckpointStatus.COMPLETE,
            ordinal,
            slide,
            (
                _Requirement(kind="native-pdf"),
                _Requirement(kind="reference-png"),
            ),
        ))
        patch_requirements = [
            _Requirement(kind="pdf"),
            _Requirement(kind="raw-svg"),
            _Requirement(kind="svg"),
        ]
        if compact_svg:
            patch_requirements.append(_Requirement(kind="svg-compact"))
        stages.append(_Stage(
            native_sequence + 1,
            CheckpointPhase.PATCH,
            CheckpointStatus.COMPLETE,
            ordinal,
            slide,
            tuple(patch_requirements),
        ))
        stages.append(_Stage(
            native_sequence + 2,
            CheckpointPhase.VALIDATE,
            CheckpointStatus.COMPLETE,
            ordinal,
            slide,
            (
                _Requirement(kind="png"),
                _Requirement(kind="evidence"),
            ),
        ))
    package_sequence = 3 + len(task.slides) * 3
    stages.append(_Stage(
        package_sequence,
        CheckpointPhase.PACKAGE,
        CheckpointStatus.COMPLETE,
        requirements=tuple(
            _Requirement(relative_path=f"package/{name}")
            for name in (
                "manifest.json",
                "qa-report.json",
                "report.html",
                "junit.xml",
                "checksums.sha256",
            )
        ),
    ))
    stages.append(_Stage(
        package_sequence + 1,
        CheckpointPhase.PUBLISH,
        CheckpointStatus.PENDING,
    ))
    stages.append(_Stage(
        package_sequence + 2,
        CheckpointPhase.PUBLISH,
        CheckpointStatus.COMPLETE,
    ))
    return tuple(stages)


def _checkpoint_summary(checkpoint: JobCheckpoint) -> dict[str, Any]:
    return {
        "schemaVersion": "1.0",
        "toolVersion": checkpoint.identity.tool_version,
        "pipelineRevision": checkpoint.identity.pipeline_revision,
        "state": checkpoint.state.to_document(),
        "complete": checkpoint.complete,
    }


def _artifact_document(
    workspace: OwnedWorkspace,
    artifact: CheckpointArtifact,
) -> dict[str, Any]:
    result: dict[str, Any] = {
        "kind": artifact.kind,
        "relativePath": artifact.path,
        "expectedBytes": artifact.bytes,
        "expectedSha256": artifact.sha256,
        "actualBytes": None,
        "actualSha256": None,
        "status": "missing",
    }
    candidate = workspace.path / Path(*PurePosixPath(artifact.path).parts)
    current = workspace.path
    for part in PurePosixPath(artifact.path).parts:
        current = current / part
        if _is_reparse_point(current):
            result["status"] = "unsafe"
            return result
    try:
        if not candidate.is_file():
            return result
        before_bytes = candidate.stat().st_size
        actual_sha256 = sha256_file(candidate)
        after_bytes = candidate.stat().st_size
    except OSError:
        result["status"] = "unreadable"
        return result
    result["actualBytes"] = after_bytes
    result["actualSha256"] = actual_sha256
    if before_bytes != after_bytes:
        result["status"] = "changed-during-read"
    elif after_bytes != artifact.bytes:
        result["status"] = "size-mismatch"
    elif actual_sha256 != artifact.sha256:
        result["status"] = "hash-mismatch"
    else:
        result["status"] = "valid"
    return result


def _requirement_document(
    requirement: _Requirement,
    artifacts: Iterable[CheckpointArtifact],
    *,
    checked: bool,
) -> dict[str, Any]:
    candidates = list(artifacts)
    if requirement.kind is not None:
        matched = sum(item.kind == requirement.kind for item in candidates)
    else:
        matched = sum(item.path == requirement.relative_path for item in candidates)
    return {
        "kind": requirement.kind,
        "relativePath": requirement.relative_path,
        "minimum": requirement.minimum,
        "matched": matched,
        "status": (
            "not-checked"
            if not checked
            else ("pass" if matched >= requirement.minimum else "fail")
        ),
    }


def _tree_contains_reparse(root: Path) -> bool:
    if not root.exists():
        return False
    pending = [root]
    while pending:
        current = pending.pop()
        if _is_reparse_point(current):
            return True
        try:
            with os.scandir(current) as entries:
                for entry in entries:
                    child = Path(entry.path)
                    if _is_reparse_point(child):
                        return True
                    if entry.is_dir(follow_symlinks=False):
                        pending.append(child)
        except OSError:
            return True
    return False


def _identity_rejection(
    checkpoint: JobCheckpoint,
    expected: CheckpointIdentity,
    slides: tuple[int, ...],
) -> str | None:
    actual = checkpoint.identity
    if actual.source_name != expected.source_name:
        return "SOURCE_NAME_MISMATCH"
    if actual.source_sha256 != expected.source_sha256:
        return "SOURCE_SHA256_MISMATCH"
    if actual.request_fingerprint != expected.request_fingerprint:
        return "REQUEST_FINGERPRINT_MISMATCH"
    if actual.tool_version != expected.tool_version:
        return "TOOL_VERSION_MISMATCH"
    if actual.pipeline_revision != expected.pipeline_revision:
        return "PIPELINE_REVISION_MISMATCH"
    if checkpoint.selected_slides != slides:
        return "SELECTED_SLIDES_MISMATCH"
    if actual.task_id != expected.task_id:
        return "TASK_ID_MISMATCH"
    if actual.workspace_nonce != expected.workspace_nonce or actual.resume_key != expected.resume_key:
        return "CHECKPOINT_IDENTITY_MISMATCH"
    return None


def _step_document(
    stage: _Stage,
    *,
    action: str,
    reason_code: str,
    artifacts: list[dict[str, Any]],
    requirements: list[dict[str, Any]],
) -> dict[str, Any]:
    return {
        "sequence": stage.sequence,
        "phase": stage.phase.value,
        "status": stage.status.value,
        "cursor": stage.cursor_document(),
        "action": action,
        "reasonCode": reason_code,
        "artifacts": artifacts,
        "requirements": requirements,
    }


def _finalize_plan(document: dict[str, Any]) -> dict[str, Any]:
    unsigned = dict(document)
    unsigned.pop("planKey", None)
    document["planKey"] = "sha256:" + hashlib.sha256(
        stable_json(unsigned).encode("utf-8")
    ).hexdigest()
    from .contracts import validate_document

    validate_document(document, "resume-plan.schema.json")
    return document


def _rejected_plan(
    workspace: OwnedWorkspace,
    task: ExportTaskModel,
    stages: tuple[_Stage, ...],
    *,
    reason_code: str,
    checkpoint: JobCheckpoint | None,
) -> dict[str, Any]:
    steps = [
        _step_document(
            stage,
            action="reject",
            reason_code=reason_code,
            artifacts=[],
            requirements=[
                _requirement_document(requirement, (), checked=False)
                for requirement in stage.requirements
            ],
        )
        for stage in stages
    ]
    expected = CheckpointIdentity.create(
        task_id=task.job_id,
        workspace_nonce=workspace.nonce,
        request_fingerprint=task.request_fingerprint,
        source_name=task.source.name,
        source_sha256=task.source_sha256,
        tool_version=__version__,
        pipeline_revision=PIPELINE_REVISION,
    )
    return _finalize_plan({
        "schemaVersion": RESUME_PLAN_SCHEMA_VERSION,
        "kind": "slideguard-resume-plan",
        "status": "rejected",
        "exitCode": 40,
        "planKey": "",
        "taskId": task.job_id,
        "resumeKey": expected.resume_key,
        "requestFingerprint": task.request_fingerprint,
        "source": {"name": task.source.name, "sha256": task.source_sha256},
        "selectedSlides": list(task.slides),
        "workspace": {
            "name": workspace.path.name,
            "nonce": workspace.nonce,
            "disposition": "retained",
        },
        "checkpoint": _checkpoint_summary(checkpoint) if checkpoint else None,
        "reusedThroughSequence": None,
        "resumeFromSequence": None,
        "publication": {
            "outputName": task.final_dir.name,
            "action": "reject",
            "reasonCode": reason_code,
        },
        "steps": steps,
        "error": {"code": reason_code, "stage": "resume-plan"},
    })


def _stage_failure_reason(
    artifact_documents: list[dict[str, Any]],
    requirement_documents: list[dict[str, Any]],
) -> str | None:
    statuses = {item["status"] for item in artifact_documents}
    if "missing" in statuses:
        return "ARTIFACT_MISSING"
    if "unreadable" in statuses:
        return "ARTIFACT_UNREADABLE"
    if "changed-during-read" in statuses:
        return "ARTIFACT_CHANGED_DURING_READ"
    if "size-mismatch" in statuses:
        return "ARTIFACT_SIZE_MISMATCH"
    if "hash-mismatch" in statuses:
        return "ARTIFACT_HASH_MISMATCH"
    if any(item["status"] == "fail" for item in requirement_documents):
        return "STAGE_RECORD_MISSING"
    return None


def build_resume_plan(
    workspace: OwnedWorkspace,
    task: ExportTaskModel,
    *,
    compact_svg: bool,
) -> dict[str, Any]:
    """Build a deterministic, read-only plan for one marker-bound checkpoint."""
    stages = _expected_stages(task, compact_svg=compact_svg)
    expected_identity = CheckpointIdentity.create(
        task_id=task.job_id,
        workspace_nonce=workspace.nonce,
        request_fingerprint=task.request_fingerprint,
        source_name=task.source.name,
        source_sha256=task.source_sha256,
        tool_version=__version__,
        pipeline_revision=PIPELINE_REVISION,
    )
    try:
        checkpoint = load_checkpoint(workspace, verify_artifacts=False)
    except CheckpointError as exc:
        return _rejected_plan(
            workspace,
            task,
            stages,
            reason_code=exc.code,
            checkpoint=None,
        )

    identity_reason = _identity_rejection(checkpoint, expected_identity, task.slides)
    if identity_reason is not None:
        return _rejected_plan(
            workspace,
            task,
            stages,
            reason_code=identity_reason,
            checkpoint=checkpoint,
        )
    if checkpoint.complete or (
        checkpoint.state.phase == CheckpointPhase.PUBLISH
        and checkpoint.state.status == CheckpointStatus.COMPLETE
    ):
        return _rejected_plan(
            workspace,
            task,
            stages,
            reason_code="CHECKPOINT_COMPLETION_UNTRUSTED",
            checkpoint=checkpoint,
        )
    if os.path.lexists(task.final_dir):
        return _rejected_plan(
            workspace,
            task,
            stages,
            reason_code="OUTPUT_COLLISION",
            checkpoint=checkpoint,
        )

    artifacts_by_sequence: dict[int, list[CheckpointArtifact]] = {}
    artifact_documents_by_sequence: dict[int, list[dict[str, Any]]] = {}
    unsafe = False
    for artifact in checkpoint.artifacts:
        artifacts_by_sequence.setdefault(artifact.sequence, []).append(artifact)
        verification = _artifact_document(workspace, artifact)
        artifact_documents_by_sequence.setdefault(artifact.sequence, []).append(verification)
        unsafe = unsafe or verification["status"] == "unsafe"
    if unsafe or _tree_contains_reparse(workspace.path / "package"):
        return _rejected_plan(
            workspace,
            task,
            stages,
            reason_code="CHECKPOINT_PATH_UNSAFE",
            checkpoint=checkpoint,
        )

    first_recompute: int | None = None
    steps: list[dict[str, Any]] = []
    for stage in stages:
        reached = stage.sequence <= checkpoint.state.sequence
        stage_artifacts = artifacts_by_sequence.get(stage.sequence, [])
        artifact_documents = artifact_documents_by_sequence.get(stage.sequence, [])
        requirement_documents = [
            _requirement_document(requirement, stage_artifacts, checked=reached)
            for requirement in stage.requirements
        ]

        own_failure = _stage_failure_reason(artifact_documents, requirement_documents) if reached else None
        if (
            reached
            and stage.phase == CheckpointPhase.PACKAGE
            and own_failure is None
        ):
            try:
                verdict, _findings = verify_package(workspace.path / "package" / "manifest.json")
                if verdict != Verdict.PASS:
                    own_failure = "PACKAGE_INTEGRITY_FAILED"
            except Exception:
                own_failure = "PACKAGE_INTEGRITY_FAILED"

        if first_recompute is not None:
            action = "recompute"
            reason = "UPSTREAM_INVALID"
        elif not reached:
            first_recompute = stage.sequence
            action = "recompute"
            reason = "CHECKPOINT_NOT_REACHED"
        elif stage.phase == CheckpointPhase.PUBLISH:
            first_recompute = stage.sequence
            action = "recompute"
            reason = (
                "PENDING_STAGE_NOT_REUSABLE"
                if stage.status == CheckpointStatus.PENDING
                else "ATOMIC_PUBLISH_REQUIRED"
            )
        elif own_failure is not None:
            first_recompute = stage.sequence
            action = "recompute"
            reason = own_failure
        else:
            action = "reuse"
            reason = "CHECKPOINT_AND_PREREQUISITES_VALID"

        steps.append(_step_document(
            stage,
            action=action,
            reason_code=reason,
            artifacts=artifact_documents,
            requirements=requirement_documents,
        ))

    assert first_recompute is not None
    reused = [step["sequence"] for step in steps if step["action"] == "reuse"]
    plan = {
        "schemaVersion": RESUME_PLAN_SCHEMA_VERSION,
        "kind": "slideguard-resume-plan",
        "status": "resumable",
        "exitCode": 0,
        "planKey": "",
        "taskId": task.job_id,
        "resumeKey": expected_identity.resume_key,
        "requestFingerprint": task.request_fingerprint,
        "source": {"name": task.source.name, "sha256": task.source_sha256},
        "selectedSlides": list(task.slides),
        "workspace": {
            "name": workspace.path.name,
            "nonce": workspace.nonce,
            "disposition": "retained",
        },
        "checkpoint": _checkpoint_summary(checkpoint),
        "reusedThroughSequence": max(reused) if reused else None,
        "resumeFromSequence": first_recompute,
        "publication": {
            "outputName": task.final_dir.name,
            "action": "publish-atomically",
            "reasonCode": "ATOMIC_PUBLISH_REQUIRED",
        },
        "steps": steps,
        "error": None,
    }
    return _finalize_plan(plan)


class ResumePlanningService:
    """Non-Qt application boundary shared by JSON, CLI and desktop workers."""

    def execute(
        self,
        document: dict[str, Any],
        *,
        workspace_path: Path,
        base_dir: Path,
    ) -> dict[str, Any]:
        from .contracts import prepare_request

        prepared = prepare_request(document, base_dir=base_dir)
        task = build_export_task_model(
            prepared.source,
            prepared.options,
        )
        if (
            task.slides != prepared.effective_slides
            or task.source_sha256 != prepared.source_sha256
        ):
            raise InputError(
                "The source changed while the resume task was being normalized",
                stage="resume-plan",
            )
        try:
            workspace = open_owned_workspace(
                workspace_path,
                expected_kind="export-workspace",
            )
        except WorkspaceSafetyError as exc:
            raise InputError(
                "Resume workspace ownership could not be verified",
                stage="resume-plan",
                details={"reason": "workspace-ownership"},
            ) from exc
        return build_resume_plan(
            workspace,
            task,
            compact_svg=prepared.options.svg_max_bytes is not None,
        )


def format_resume_plan(plan: dict[str, Any]) -> str:
    """Render a compact human view without changing plan semantics."""
    status = str(plan["status"]).upper()
    lines = [f"SlideGuard resume plan: {status}"]
    if plan["status"] == "rejected":
        lines.append(f"Reason: {plan['error']['code']}")
        lines.append("Workspace: retained; no artifact or published output was changed")
        return "\n".join(lines)
    resume_sequence = plan["resumeFromSequence"]
    resume_step = next(item for item in plan["steps"] if item["sequence"] == resume_sequence)
    lines.append(f"Reusable through sequence: {plan['reusedThroughSequence']}")
    lines.append(
        f"Resume from sequence {resume_sequence}: {resume_step['phase']}/{resume_step['status']} "
        f"({resume_step['reasonCode']})"
    )
    lines.append("Publication: atomic publish required; workspace retained until execution succeeds")
    return "\n".join(lines)
