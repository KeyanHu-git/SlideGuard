from __future__ import annotations

import hashlib
import json
import os
import re
import uuid
from dataclasses import dataclass
from enum import Enum
from functools import lru_cache
from importlib import resources
from pathlib import Path, PurePosixPath
from typing import Iterable

from jsonschema import Draft202012Validator, FormatChecker

from .errors import ExportError
from .util import native_long_path, sha256_file, stable_json, utc_now
from .workspace import (
    OWNER_MARKER,
    OwnedWorkspace,
    WorkspaceSafetyError,
    _is_reparse_point,
    _read_marker,
    _require_direct_child,
)


CHECKPOINT_FILENAME = "job-state.json"
CHECKPOINT_SCHEMA_VERSION = "1.0"
CHECKPOINT_CONTRACT_VERSION = "1.0"
MAX_CHECKPOINT_BYTES = 32 * 1024 * 1024
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_FINGERPRINT = re.compile(r"^sha256:[0-9a-f]{64}$")
_KIND = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")


class CheckpointError(ExportError):
    """Base class for stable, machine-readable checkpoint failures."""

    code = "CHECKPOINT_INVALID"


class CheckpointReadError(CheckpointError):
    code = "CHECKPOINT_READ_FAILED"


class CheckpointWriteError(CheckpointError):
    code = "CHECKPOINT_WRITE_FAILED"


class CheckpointVersionError(CheckpointError):
    code = "CHECKPOINT_VERSION_UNSUPPORTED"


class CheckpointSchemaError(CheckpointError):
    code = "CHECKPOINT_SCHEMA_INVALID"


class CheckpointIdentityError(CheckpointError):
    code = "CHECKPOINT_IDENTITY_MISMATCH"


class CheckpointPathError(CheckpointError):
    code = "CHECKPOINT_PATH_UNSAFE"


class CheckpointArtifactError(CheckpointError):
    code = "CHECKPOINT_ARTIFACT_INVALID"


class CheckpointTransitionError(CheckpointError):
    code = "CHECKPOINT_TRANSITION_INVALID"


class CheckpointPhase(str, Enum):
    DISCOVER = "DISCOVER"
    PREFLIGHT = "PREFLIGHT"
    INVENTORY = "INVENTORY"
    NATIVE_EXPORT = "NATIVE_EXPORT"
    PATCH = "PATCH"
    VALIDATE = "VALIDATE"
    PACKAGE = "PACKAGE"
    PUBLISH = "PUBLISH"


class CheckpointStatus(str, Enum):
    PENDING = "pending"
    COMPLETE = "complete"


@dataclass(frozen=True, slots=True)
class CheckpointCursor:
    output_ordinal: int
    source_slide: int

    def to_document(self) -> dict[str, int]:
        return {"outputOrdinal": self.output_ordinal, "sourceSlide": self.source_slide}


@dataclass(frozen=True, slots=True)
class CheckpointState:
    sequence: int
    phase: CheckpointPhase
    status: CheckpointStatus
    cursor: CheckpointCursor | None = None

    def to_document(self) -> dict[str, object]:
        return {
            "sequence": self.sequence,
            "phase": self.phase.value,
            "status": self.status.value,
            "cursor": self.cursor.to_document() if self.cursor else None,
        }


@dataclass(frozen=True, slots=True)
class CheckpointArtifact:
    kind: str
    path: str
    bytes: int
    sha256: str
    phase: CheckpointPhase
    sequence: int

    def to_document(self) -> dict[str, object]:
        return {
            "kind": self.kind,
            "path": self.path,
            "bytes": self.bytes,
            "sha256": self.sha256,
            "phase": self.phase.value,
            "sequence": self.sequence,
        }


@dataclass(frozen=True, slots=True)
class CheckpointIdentity:
    task_id: str
    workspace_nonce: str
    request_fingerprint: str
    source_name: str
    source_sha256: str
    tool_version: str
    pipeline_revision: str
    resume_key: str

    @classmethod
    def create(
        cls,
        *,
        task_id: str,
        workspace_nonce: str,
        request_fingerprint: str,
        source_name: str,
        source_sha256: str,
        tool_version: str,
        pipeline_revision: str,
    ) -> "CheckpointIdentity":
        _validate_identity_fields(
            task_id=task_id,
            workspace_nonce=workspace_nonce,
            request_fingerprint=request_fingerprint,
            source_name=source_name,
            source_sha256=source_sha256,
            tool_version=tool_version,
            pipeline_revision=pipeline_revision,
        )
        resume_key = _resume_key(
            task_id=task_id,
            request_fingerprint=request_fingerprint,
            source_name=source_name,
            source_sha256=source_sha256,
            tool_version=tool_version,
            pipeline_revision=pipeline_revision,
        )
        return cls(
            task_id=task_id,
            workspace_nonce=workspace_nonce,
            request_fingerprint=request_fingerprint,
            source_name=source_name,
            source_sha256=source_sha256,
            tool_version=tool_version,
            pipeline_revision=pipeline_revision,
            resume_key=resume_key,
        )


@dataclass(frozen=True, slots=True)
class JobCheckpoint:
    identity: CheckpointIdentity
    selected_slides: tuple[int, ...]
    state: CheckpointState
    artifacts: tuple[CheckpointArtifact, ...]
    complete: bool
    written_at: str

    def to_document(self) -> dict[str, object]:
        return {
            "schemaVersion": CHECKPOINT_SCHEMA_VERSION,
            "kind": "slideguard-checkpoint",
            "taskId": self.identity.task_id,
            "workspaceNonce": self.identity.workspace_nonce,
            "requestFingerprint": self.identity.request_fingerprint,
            "resumeKey": self.identity.resume_key,
            "source": {
                "name": self.identity.source_name,
                "sha256": self.identity.source_sha256,
            },
            "tool": {
                "version": self.identity.tool_version,
                "pipelineRevision": self.identity.pipeline_revision,
                "checkpointContractVersion": CHECKPOINT_CONTRACT_VERSION,
            },
            "selectedSlides": list(self.selected_slides),
            "state": self.state.to_document(),
            "artifacts": [item.to_document() for item in self.artifacts],
            "complete": self.complete,
            "writtenAt": self.written_at,
        }


def _resume_key(
    *,
    task_id: str,
    request_fingerprint: str,
    source_name: str,
    source_sha256: str,
    tool_version: str,
    pipeline_revision: str,
) -> str:
    # The nonce and wall-clock time are deliberately excluded. This identity is
    # comparable across attempts, while workspaceNonce separately prevents a
    # checkpoint copied from another workspace from being trusted.
    stable = {
        "schemaMajor": 1,
        "taskId": task_id,
        "requestFingerprint": request_fingerprint,
        "source": {"name": source_name, "sha256": source_sha256},
        "tool": {"version": tool_version, "pipelineRevision": pipeline_revision},
    }
    return "sha256:" + hashlib.sha256(stable_json(stable).encode("utf-8")).hexdigest()


def _safe_leaf(value: str) -> bool:
    return bool(value and value not in {".", ".."} and "/" not in value and "\\" not in value and ":" not in value)


def _validate_identity_fields(
    *,
    task_id: str,
    workspace_nonce: str,
    request_fingerprint: str,
    source_name: str,
    source_sha256: str,
    tool_version: str,
    pipeline_revision: str,
) -> None:
    if not _safe_leaf(task_id) or len(task_id) > 256:
        raise CheckpointIdentityError("Checkpoint task identity is invalid", stage="checkpoint")
    if not re.fullmatch(r"[0-9a-f]{32}", workspace_nonce):
        raise CheckpointIdentityError("Checkpoint workspace nonce is invalid", stage="checkpoint")
    if not _FINGERPRINT.fullmatch(request_fingerprint):
        raise CheckpointIdentityError("Checkpoint request fingerprint is invalid", stage="checkpoint")
    if not _safe_leaf(source_name) or len(source_name) > 255:
        raise CheckpointPathError("Checkpoint source name must not contain a path", stage="checkpoint")
    if not _SHA256.fullmatch(source_sha256):
        raise CheckpointIdentityError("Checkpoint source hash is invalid", stage="checkpoint")
    if not tool_version or len(tool_version) > 64 or not pipeline_revision or len(pipeline_revision) > 128:
        raise CheckpointIdentityError("Checkpoint tool identity is invalid", stage="checkpoint")


@lru_cache(maxsize=1)
def _schema_validator() -> Draft202012Validator:
    schema = json.loads(
        resources.files("slideguard").joinpath("schemas", "job-state.schema.json").read_text(encoding="utf-8")
    )
    Draft202012Validator.check_schema(schema)
    return Draft202012Validator(schema, format_checker=FormatChecker())


def _strict_json(raw: bytes) -> object:
    def reject_constant(value: str) -> None:
        raise ValueError(f"non-standard constant:{value}")

    def reject_duplicates(pairs: list[tuple[str, object]]) -> dict[str, object]:
        result: dict[str, object] = {}
        for key, value in pairs:
            if key in result:
                raise ValueError("duplicate key")
            result[key] = value
        return result

    try:
        return json.loads(
            raw.decode("utf-8", errors="strict"),
            parse_constant=reject_constant,
            object_pairs_hook=reject_duplicates,
        )
    except (UnicodeError, json.JSONDecodeError, ValueError) as exc:
        raise CheckpointReadError(
            "Checkpoint is not strict, complete UTF-8 JSON",
            stage="checkpoint",
            details={"reason": "invalid-json"},
        ) from exc


def _safe_relative_path(value: object) -> str:
    if not isinstance(value, str) or not value or "\\" in value or "\x00" in value:
        raise CheckpointPathError("Checkpoint contains an unsafe artifact path", stage="checkpoint")
    pure = PurePosixPath(value)
    if pure.is_absolute() or pure.parts[0].endswith(":") or any(part in {"", ".", ".."} for part in pure.parts):
        raise CheckpointPathError("Checkpoint contains an unsafe artifact path", stage="checkpoint")
    normalized = pure.as_posix()
    if normalized != value:
        raise CheckpointPathError("Checkpoint artifact path is not canonical", stage="checkpoint")
    if normalized in {CHECKPOINT_FILENAME, OWNER_MARKER} or normalized.startswith(".job-state."):
        raise CheckpointPathError("Checkpoint cannot list its own control files", stage="checkpoint")
    return normalized


def _preflight_document_paths(document: object) -> None:
    if not isinstance(document, dict):
        return
    source = document.get("source")
    if isinstance(source, dict) and "name" in source:
        name = source["name"]
        if not isinstance(name, str) or not _safe_leaf(name):
            raise CheckpointPathError("Checkpoint source name must not contain a path", stage="checkpoint")
    artifacts = document.get("artifacts")
    if isinstance(artifacts, list):
        for item in artifacts:
            if isinstance(item, dict) and "path" in item:
                _safe_relative_path(item["path"])


def _validate_schema_document(document: object) -> None:
    errors = sorted(_schema_validator().iter_errors(document), key=lambda item: list(item.absolute_path))
    if errors:
        paths = ["/" + "/".join(str(part) for part in item.absolute_path) for item in errors[:20]]
        raise CheckpointSchemaError(
            "Checkpoint does not satisfy job-state.schema.json",
            stage="checkpoint",
            details={"reason": "schema", "paths": paths},
        )


def _phase_sequence(phase: CheckpointPhase, status: CheckpointStatus, cursor: CheckpointCursor | None, slide_count: int) -> int:
    if phase == CheckpointPhase.DISCOVER:
        return 0
    if phase == CheckpointPhase.PREFLIGHT:
        return 1
    if phase == CheckpointPhase.INVENTORY:
        return 2
    if phase in {CheckpointPhase.NATIVE_EXPORT, CheckpointPhase.PATCH, CheckpointPhase.VALIDATE}:
        if cursor is None:
            raise CheckpointTransitionError("Per-slide phase requires a cursor", stage="checkpoint")
        if (
            isinstance(cursor.output_ordinal, bool)
            or not isinstance(cursor.output_ordinal, int)
            or cursor.output_ordinal < 1
            or isinstance(cursor.source_slide, bool)
            or not isinstance(cursor.source_slide, int)
            or cursor.source_slide < 1
        ):
            raise CheckpointTransitionError("Checkpoint cursor is invalid", stage="checkpoint")
        phase_offset = {
            CheckpointPhase.NATIVE_EXPORT: 0,
            CheckpointPhase.PATCH: 1,
            CheckpointPhase.VALIDATE: 2,
        }[phase]
        return 3 + (cursor.output_ordinal - 1) * 3 + phase_offset
    package_sequence = 3 + slide_count * 3
    if phase == CheckpointPhase.PACKAGE:
        return package_sequence
    if phase == CheckpointPhase.PUBLISH:
        return package_sequence + (2 if status == CheckpointStatus.COMPLETE else 1)
    raise CheckpointTransitionError("Unknown checkpoint phase", stage="checkpoint")


def _validate_semantics(checkpoint: JobCheckpoint) -> None:
    slides = checkpoint.selected_slides
    if (
        not slides
        or len(set(slides)) != len(slides)
        or any(isinstance(item, bool) or not isinstance(item, int) or item < 1 for item in slides)
    ):
        raise CheckpointSchemaError("Checkpoint selectedSlides is invalid", stage="checkpoint")
    cursor = checkpoint.state.cursor
    per_slide = checkpoint.state.phase in {
        CheckpointPhase.NATIVE_EXPORT,
        CheckpointPhase.PATCH,
        CheckpointPhase.VALIDATE,
    }
    if per_slide != (cursor is not None):
        raise CheckpointTransitionError("Checkpoint cursor does not match its phase", stage="checkpoint")
    if cursor is not None:
        if cursor.output_ordinal > len(slides) or slides[cursor.output_ordinal - 1] != cursor.source_slide:
            raise CheckpointTransitionError("Checkpoint cursor is not bound to selectedSlides", stage="checkpoint")
    if checkpoint.state.phase != CheckpointPhase.PUBLISH and checkpoint.state.status != CheckpointStatus.COMPLETE:
        raise CheckpointTransitionError("Only PUBLISH may be pending", stage="checkpoint")
    expected_sequence = _phase_sequence(
        checkpoint.state.phase, checkpoint.state.status, checkpoint.state.cursor, len(slides),
    )
    if checkpoint.state.sequence != expected_sequence:
        raise CheckpointTransitionError("Checkpoint sequence does not match its phase", stage="checkpoint")
    should_be_complete = (
        checkpoint.state.phase == CheckpointPhase.PUBLISH
        and checkpoint.state.status == CheckpointStatus.COMPLETE
    )
    if checkpoint.complete != should_be_complete:
        raise CheckpointTransitionError("Checkpoint completion flag is inconsistent", stage="checkpoint")
    seen: set[str] = set()
    previous = ""
    for artifact in checkpoint.artifacts:
        normalized = _safe_relative_path(artifact.path)
        if normalized in seen or normalized < previous:
            raise CheckpointSchemaError("Checkpoint artifacts must be unique and sorted", stage="checkpoint")
        if artifact.sequence > checkpoint.state.sequence:
            raise CheckpointTransitionError("Checkpoint artifact belongs to a future phase", stage="checkpoint")
        if not _artifact_sequence_matches_phase(artifact, len(slides)):
            raise CheckpointTransitionError("Checkpoint artifact sequence does not match its phase", stage="checkpoint")
        seen.add(normalized)
        previous = normalized


def _artifact_sequence_matches_phase(artifact: CheckpointArtifact, slide_count: int) -> bool:
    sequence = artifact.sequence
    if artifact.phase == CheckpointPhase.DISCOVER:
        return sequence == 0
    if artifact.phase == CheckpointPhase.PREFLIGHT:
        return sequence == 1
    if artifact.phase == CheckpointPhase.INVENTORY:
        return sequence == 2
    if artifact.phase in {CheckpointPhase.NATIVE_EXPORT, CheckpointPhase.PATCH, CheckpointPhase.VALIDATE}:
        offset = {
            CheckpointPhase.NATIVE_EXPORT: 3,
            CheckpointPhase.PATCH: 4,
            CheckpointPhase.VALIDATE: 5,
        }[artifact.phase]
        return sequence >= offset and (sequence - offset) % 3 == 0 and sequence <= 2 + slide_count * 3
    package_sequence = 3 + slide_count * 3
    if artifact.phase == CheckpointPhase.PACKAGE:
        return sequence == package_sequence
    return artifact.phase == CheckpointPhase.PUBLISH and sequence in {
        package_sequence + 1,
        package_sequence + 2,
    }


def _checkpoint_from_document(document: object) -> JobCheckpoint:
    if not isinstance(document, dict):
        raise CheckpointSchemaError("Checkpoint root is not an object", stage="checkpoint")
    version = document.get("schemaVersion")
    if not isinstance(version, str):
        raise CheckpointVersionError("Checkpoint schema version is missing", stage="checkpoint")
    try:
        major = int(version.split(".", 1)[0])
    except (ValueError, IndexError) as exc:
        raise CheckpointVersionError("Checkpoint schema version is invalid", stage="checkpoint") from exc
    if major != 1:
        raise CheckpointVersionError(
            "Checkpoint schema major version is unsupported",
            stage="checkpoint",
            details={"supportedMajor": 1},
        )
    _preflight_document_paths(document)
    _validate_schema_document(document)
    source = document["source"]
    tool = document["tool"]
    state_document = document["state"]
    assert isinstance(source, dict) and isinstance(tool, dict) and isinstance(state_document, dict)
    identity = CheckpointIdentity.create(
        task_id=str(document["taskId"]),
        workspace_nonce=str(document["workspaceNonce"]),
        request_fingerprint=str(document["requestFingerprint"]),
        source_name=str(source["name"]),
        source_sha256=str(source["sha256"]),
        tool_version=str(tool["version"]),
        pipeline_revision=str(tool["pipelineRevision"]),
    )
    if document["resumeKey"] != identity.resume_key:
        raise CheckpointIdentityError("Checkpoint resumeKey does not match its identity", stage="checkpoint")
    cursor_document = state_document["cursor"]
    cursor = (
        CheckpointCursor(
            output_ordinal=int(cursor_document["outputOrdinal"]),
            source_slide=int(cursor_document["sourceSlide"]),
        )
        if isinstance(cursor_document, dict)
        else None
    )
    state = CheckpointState(
        sequence=int(state_document["sequence"]),
        phase=CheckpointPhase(str(state_document["phase"])),
        status=CheckpointStatus(str(state_document["status"])),
        cursor=cursor,
    )
    artifacts = tuple(
        CheckpointArtifact(
            kind=str(item["kind"]),
            path=str(item["path"]),
            bytes=int(item["bytes"]),
            sha256=str(item["sha256"]),
            phase=CheckpointPhase(str(item["phase"])),
            sequence=int(item["sequence"]),
        )
        for item in document["artifacts"]
    )
    checkpoint = JobCheckpoint(
        identity=identity,
        selected_slides=tuple(int(item) for item in document["selectedSlides"]),
        state=state,
        artifacts=artifacts,
        complete=bool(document["complete"]),
        written_at=str(document["writtenAt"]),
    )
    _validate_semantics(checkpoint)
    return checkpoint


def _validated_workspace(workspace: OwnedWorkspace) -> Path:
    try:
        path, root = _require_direct_child(workspace.path, workspace.root)
        marker, _ = _read_marker(path, root=root)
    except WorkspaceSafetyError as exc:
        raise CheckpointIdentityError(
            "Checkpoint workspace ownership could not be verified",
            stage="checkpoint",
            details={"reason": "workspace-ownership"},
        ) from exc
    if (
        marker["instanceNonce"] != workspace.nonce
        or marker["taskId"] != workspace.task_id
        or marker["kind"] != workspace.kind
        or workspace.kind != "export-workspace"
    ):
        raise CheckpointIdentityError(
            "Checkpoint workspace identity does not match its owner marker",
            stage="checkpoint",
        )
    return path


def _write_temp_file(path: Path, payload: bytes) -> None:
    with open(native_long_path(path), "xb") as stream:
        stream.write(payload)
        stream.flush()
        os.fsync(stream.fileno())


def _atomic_write(path: Path, document: dict[str, object], nonce: str) -> None:
    payload = stable_json(document).encode("utf-8")
    if len(payload) > MAX_CHECKPOINT_BYTES:
        raise CheckpointWriteError(
            "Checkpoint exceeds its size limit",
            stage="checkpoint",
            details={"limitBytes": MAX_CHECKPOINT_BYTES},
        )
    temporary = path.parent / f".job-state.{nonce}.{uuid.uuid4().hex}.tmp"
    try:
        _write_temp_file(temporary, payload)
        os.replace(native_long_path(temporary), native_long_path(path))
    except OSError as exc:
        raise CheckpointWriteError(
            "Checkpoint could not be written atomically",
            stage="checkpoint",
            details={"reason": type(exc).__name__},
        ) from exc
    finally:
        try:
            os.unlink(native_long_path(temporary))
        except FileNotFoundError:
            pass
        except OSError:
            pass


def _next_state(
    previous: CheckpointState | None,
    *,
    phase: CheckpointPhase,
    status: CheckpointStatus,
    cursor: CheckpointCursor | None,
    slides: tuple[int, ...],
) -> CheckpointState:
    sequence = _phase_sequence(phase, status, cursor, len(slides))
    candidate = CheckpointState(sequence=sequence, phase=phase, status=status, cursor=cursor)
    if previous is None:
        if candidate != CheckpointState(0, CheckpointPhase.DISCOVER, CheckpointStatus.COMPLETE):
            raise CheckpointTransitionError("First checkpoint must complete DISCOVER", stage="checkpoint")
        return candidate
    if candidate.sequence != previous.sequence + 1:
        raise CheckpointTransitionError(
            "Checkpoint transition must advance exactly one state",
            stage="checkpoint",
            details={"fromSequence": previous.sequence, "toSequence": candidate.sequence},
        )
    return candidate


def _artifact_path(workspace_path: Path, candidate: Path) -> tuple[Path, str]:
    candidate = Path(os.path.abspath(os.fspath(candidate)))
    workspace_path = Path(os.path.abspath(os.fspath(workspace_path)))
    try:
        relative = candidate.relative_to(workspace_path)
    except ValueError as exc:
        raise CheckpointPathError("Checkpoint artifact is outside its workspace", stage="checkpoint") from exc
    current = workspace_path
    for part in relative.parts:
        current = current / part
        if _is_reparse_point(current):
            raise CheckpointPathError("Checkpoint artifact path contains a link or reparse point", stage="checkpoint")
    if not candidate.is_file():
        raise CheckpointArtifactError("Checkpoint artifact is missing or not a file", stage="checkpoint")
    normalized = _safe_relative_path(PurePosixPath(*relative.parts).as_posix())
    return candidate, normalized


class CheckpointJournal:
    """Append-only logical checkpoint journal stored as one atomic snapshot."""

    def __init__(
        self,
        workspace: OwnedWorkspace,
        identity: CheckpointIdentity,
        selected_slides: Iterable[int],
    ) -> None:
        self.workspace = workspace
        self.path = _validated_workspace(workspace) / CHECKPOINT_FILENAME
        if identity.workspace_nonce != workspace.nonce or identity.task_id != workspace.task_id:
            raise CheckpointIdentityError("Checkpoint identity is not bound to its workspace", stage="checkpoint")
        self.identity = identity
        self.selected_slides = tuple(selected_slides)
        if not self.selected_slides:
            raise CheckpointSchemaError("Checkpoint requires at least one selected slide", stage="checkpoint")
        self.state: CheckpointState | None = None
        self._artifacts: dict[str, CheckpointArtifact] = {}

    def advance(
        self,
        phase: CheckpointPhase,
        *,
        status: CheckpointStatus = CheckpointStatus.COMPLETE,
        cursor: CheckpointCursor | None = None,
        artifacts: Iterable[tuple[str, Path]] = (),
    ) -> JobCheckpoint:
        candidate_state = _next_state(
            self.state,
            phase=phase,
            status=status,
            cursor=cursor,
            slides=self.selected_slides,
        )
        candidate_artifacts = dict(self._artifacts)
        workspace_path = self.path.parent
        for kind, path in artifacts:
            if not _KIND.fullmatch(kind):
                raise CheckpointArtifactError("Checkpoint artifact kind is invalid", stage="checkpoint")
            absolute, relative = _artifact_path(workspace_path, path)
            artifact = CheckpointArtifact(
                kind=kind,
                path=relative,
                bytes=absolute.stat().st_size,
                sha256=sha256_file(absolute),
                phase=phase,
                sequence=candidate_state.sequence,
            )
            old = candidate_artifacts.get(relative)
            if old is not None and (old.bytes != artifact.bytes or old.sha256 != artifact.sha256):
                raise CheckpointArtifactError(
                    "A completed checkpoint artifact was modified",
                    stage="checkpoint",
                    details={"relativePath": relative},
                )
            if old is None:
                candidate_artifacts[relative] = artifact
        ordered = tuple(candidate_artifacts[key] for key in sorted(candidate_artifacts))
        checkpoint = JobCheckpoint(
            identity=self.identity,
            selected_slides=self.selected_slides,
            state=candidate_state,
            artifacts=ordered,
            complete=(phase == CheckpointPhase.PUBLISH and status == CheckpointStatus.COMPLETE),
            written_at=utc_now(),
        )
        _validate_semantics(checkpoint)
        document = checkpoint.to_document()
        _preflight_document_paths(document)
        _validate_schema_document(document)
        _atomic_write(self.path, document, self.workspace.nonce)
        self.state = candidate_state
        self._artifacts = candidate_artifacts
        return checkpoint


def load_checkpoint(
    workspace: OwnedWorkspace,
    *,
    expected_identity: CheckpointIdentity | None = None,
    verify_artifacts: bool = True,
) -> JobCheckpoint:
    workspace_path = _validated_workspace(workspace)
    path = workspace_path / CHECKPOINT_FILENAME
    if _is_reparse_point(path):
        raise CheckpointPathError("Checkpoint file is a link or reparse point", stage="checkpoint")
    try:
        size = path.stat().st_size
        if size > MAX_CHECKPOINT_BYTES:
            raise CheckpointReadError(
                "Checkpoint exceeds its size limit",
                stage="checkpoint",
                details={"limitBytes": MAX_CHECKPOINT_BYTES},
            )
        raw = path.read_bytes()
    except CheckpointError:
        raise
    except OSError as exc:
        raise CheckpointReadError(
            "Checkpoint is missing or unreadable",
            stage="checkpoint",
            details={"reason": type(exc).__name__},
        ) from exc
    checkpoint = _checkpoint_from_document(_strict_json(raw))
    if checkpoint.identity.workspace_nonce != workspace.nonce or checkpoint.identity.task_id != workspace.task_id:
        raise CheckpointIdentityError("Checkpoint is not bound to its owner marker", stage="checkpoint")
    if expected_identity is not None and checkpoint.identity != expected_identity:
        raise CheckpointIdentityError("Checkpoint does not match the expected job identity", stage="checkpoint")
    if verify_artifacts:
        for artifact in checkpoint.artifacts:
            candidate, _ = _artifact_path(workspace_path, workspace_path / PurePosixPath(artifact.path))
            actual_bytes = candidate.stat().st_size
            if actual_bytes != artifact.bytes:
                raise CheckpointArtifactError(
                    "Checkpoint artifact size does not match",
                    stage="checkpoint",
                    details={"relativePath": artifact.path},
                )
            if sha256_file(candidate) != artifact.sha256:
                raise CheckpointArtifactError(
                    "Checkpoint artifact hash does not match",
                    stage="checkpoint",
                    details={"relativePath": artifact.path},
                )
    return checkpoint
