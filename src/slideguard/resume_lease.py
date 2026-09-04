from __future__ import annotations

import json
import os
import re
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import BinaryIO, Callable

from .errors import ExportError
from .util import native_long_path, stable_json, utc_now
from .workspace import (
    OwnedWorkspace,
    WorkspaceSafetyError,
    _is_reparse_point,
    _process_start_token,
    open_owned_workspace,
    owner_process_is_active,
)


RESUME_LEASE_FILENAME = ".slideguard-resume-lease.json"
RESUME_LOCK_FILENAME = ".slideguard-resume.lock"
RESUME_LEASE_SCHEMA_VERSION = "1.0"
MAX_RESUME_LEASE_BYTES = 64 * 1024
_NONCE = re.compile(r"^[0-9a-f]{32}$")


class ResumeLeaseError(ExportError):
    code = "RESUME_LEASE_FAILED"


class ResumeInProgressError(ResumeLeaseError):
    code = "RESUME_IN_PROGRESS"


class ResumeLeaseInvalidError(ResumeLeaseError):
    code = "RESUME_LEASE_INVALID"


class ResumeLeaseLostError(ResumeLeaseError):
    code = "RESUME_LEASE_LOST"


def _lock_stream(stream: BinaryIO) -> bool:
    stream.seek(0)
    if os.name == "nt":
        import msvcrt

        try:
            if stream.seek(0, os.SEEK_END) == 0:
                stream.write(b"\0")
                stream.flush()
                os.fsync(stream.fileno())
            stream.seek(0)
            msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
            return True
        except OSError:
            return False
    import fcntl

    try:
        fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        return True
    except OSError:
        return False


def _unlock_stream(stream: BinaryIO) -> None:
    try:
        stream.seek(0)
        if os.name == "nt":
            import msvcrt

            msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
        else:
            import fcntl

            fcntl.flock(stream.fileno(), fcntl.LOCK_UN)
    except (OSError, ValueError):
        pass


def _same_open_file(stream: BinaryIO, path: Path) -> bool:
    try:
        opened = os.fstat(stream.fileno())
        current = os.stat(native_long_path(path), follow_symlinks=False)
    except (OSError, ValueError):
        return False
    return (opened.st_dev, opened.st_ino) == (current.st_dev, current.st_ino)


def _validated_workspace(workspace: OwnedWorkspace) -> OwnedWorkspace:
    try:
        opened = open_owned_workspace(workspace.path, expected_kind="export-workspace")
    except WorkspaceSafetyError as exc:
        raise ResumeLeaseInvalidError(
            "Resume workspace ownership could not be verified",
            stage="resume-lease",
            details={"reason": "workspace-ownership"},
        ) from exc
    if (
        opened.nonce != workspace.nonce
        or opened.task_id != workspace.task_id
        or opened.kind != workspace.kind
    ):
        raise ResumeLeaseInvalidError(
            "Resume workspace identity changed",
            stage="resume-lease",
            details={"reason": "workspace-identity"},
        )
    return opened


def _lease_document(workspace: OwnedWorkspace, nonce: str) -> dict[str, object]:
    process_id = os.getpid()
    return {
        "schemaVersion": RESUME_LEASE_SCHEMA_VERSION,
        "kind": "slideguard-resume-lease",
        "taskId": workspace.task_id,
        "workspaceNonce": workspace.nonce,
        "leaseNonce": nonce,
        "processId": process_id,
        "processStartToken": _process_start_token(process_id) or "unavailable",
        "createdAt": utc_now(),
    }


def _validate_document(value: object, workspace: OwnedWorkspace) -> dict[str, object]:
    if not isinstance(value, dict):
        raise ResumeLeaseInvalidError("Resume lease is not an object", stage="resume-lease")
    required = {
        "schemaVersion",
        "kind",
        "taskId",
        "workspaceNonce",
        "leaseNonce",
        "processId",
        "processStartToken",
        "createdAt",
    }
    if set(value) != required:
        raise ResumeLeaseInvalidError("Resume lease fields do not match the schema", stage="resume-lease")
    if value["schemaVersion"] != RESUME_LEASE_SCHEMA_VERSION:
        raise ResumeLeaseInvalidError("Resume lease schema is unsupported", stage="resume-lease")
    if value["kind"] != "slideguard-resume-lease":
        raise ResumeLeaseInvalidError("Resume lease kind is invalid", stage="resume-lease")
    if value["taskId"] != workspace.task_id or value["workspaceNonce"] != workspace.nonce:
        raise ResumeLeaseInvalidError("Resume lease belongs to another workspace", stage="resume-lease")
    nonce = value["leaseNonce"]
    if not isinstance(nonce, str) or not _NONCE.fullmatch(nonce):
        raise ResumeLeaseInvalidError("Resume lease nonce is invalid", stage="resume-lease")
    process_id = value["processId"]
    if isinstance(process_id, bool) or not isinstance(process_id, int) or process_id <= 0:
        raise ResumeLeaseInvalidError("Resume lease process ID is invalid", stage="resume-lease")
    if not isinstance(value["processStartToken"], str) or not value["processStartToken"]:
        raise ResumeLeaseInvalidError("Resume lease process token is invalid", stage="resume-lease")
    if not isinstance(value["createdAt"], str) or not value["createdAt"]:
        raise ResumeLeaseInvalidError("Resume lease creation time is invalid", stage="resume-lease")
    return value


def _read_lease_document(path: Path, workspace: OwnedWorkspace) -> tuple[dict[str, object], bytes]:
    if _is_reparse_point(path):
        raise ResumeLeaseInvalidError("Resume lease is a link or reparse point", stage="resume-lease")
    try:
        size = os.stat(native_long_path(path), follow_symlinks=False).st_size
        if size <= 0 or size > MAX_RESUME_LEASE_BYTES:
            raise ResumeLeaseInvalidError("Resume lease size is invalid", stage="resume-lease")
        with open(native_long_path(path), "rb") as stream:
            raw = stream.read(MAX_RESUME_LEASE_BYTES + 1)
        if len(raw) != size:
            raise ResumeLeaseInvalidError("Resume lease changed while being read", stage="resume-lease")
        value = json.loads(raw.decode("utf-8", errors="strict"))
    except ResumeLeaseError:
        raise
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ResumeLeaseInvalidError("Resume lease is not valid UTF-8 JSON", stage="resume-lease") from exc
    return _validate_document(value, workspace), raw


def _write_candidate(path: Path, document: dict[str, object]) -> None:
    payload = stable_json(document).encode("utf-8")
    if len(payload) > MAX_RESUME_LEASE_BYTES:
        raise ResumeLeaseInvalidError("Resume lease exceeds its size limit", stage="resume-lease")
    try:
        with open(native_long_path(path), "xb") as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
    except OSError as exc:
        raise ResumeLeaseError(
            "Resume lease candidate could not be written",
            stage="resume-lease",
            details={"reason": type(exc).__name__},
        ) from exc


def _open_mutex(path: Path) -> BinaryIO | None:
    if _is_reparse_point(path):
        raise ResumeLeaseInvalidError("Resume lock is a link or reparse point", stage="resume-lease")
    try:
        stream = open(native_long_path(path), "a+b", buffering=0)
    except OSError as exc:
        raise ResumeLeaseError(
            "Resume lock could not be opened",
            stage="resume-lease",
            details={"reason": type(exc).__name__},
        ) from exc
    if _lock_stream(stream):
        return stream
    stream.close()
    return None


def _safe_unlink(path: Path) -> bool:
    try:
        os.unlink(native_long_path(path))
        return True
    except FileNotFoundError:
        return True
    except OSError:
        return False


@dataclass(slots=True)
class ResumeLease:
    workspace: OwnedWorkspace
    path: Path
    lock_path: Path
    nonce: str
    document: dict[str, object]
    _stream: BinaryIO = field(repr=False)
    _candidate_path: Path | None = field(default=None, repr=False)
    _released: bool = field(default=False, init=False, repr=False)

    def assert_current(self) -> None:
        if self._released or self._stream.closed:
            raise ResumeLeaseLostError("Resume lease is no longer held", stage="resume-lease")
        _validated_workspace(self.workspace)
        if _is_reparse_point(self.lock_path) or not _same_open_file(self._stream, self.lock_path):
            raise ResumeLeaseLostError("Resume lock path no longer names this owner", stage="resume-lease")
        document, raw = _read_lease_document(self.path, self.workspace)
        if document["leaseNonce"] != self.nonce or raw != stable_json(self.document).encode("utf-8"):
            raise ResumeLeaseLostError("Resume lease contents changed", stage="resume-lease")

    def release(self) -> None:
        if self._released:
            return
        lost: ResumeLeaseLostError | None = None
        try:
            self.assert_current()
            if not _safe_unlink(self.path):
                lost = ResumeLeaseLostError("Resume lease could not be removed", stage="resume-lease")
        except ResumeLeaseLostError as exc:
            lost = exc
        finally:
            _unlock_stream(self._stream)
            self._stream.close()
            if self._candidate_path is not None:
                _safe_unlink(self._candidate_path)
            self._released = True
        if lost is not None:
            raise lost

    def __enter__(self) -> "ResumeLease":
        self.assert_current()
        return self

    def __exit__(self, exc_type, exc, traceback) -> bool:
        try:
            self.release()
        except ResumeLeaseLostError:
            if exc is None:
                raise
        return False


def _candidate_path(workspace: OwnedWorkspace, nonce: str) -> Path:
    return workspace.path / f".slideguard-resume-lease.{nonce}.candidate"


def _replace_lease(
    workspace: OwnedWorkspace,
    lease_path: Path,
    nonce: str,
    document: dict[str, object],
) -> None:
    candidate = _candidate_path(workspace, nonce)
    _write_candidate(candidate, document)
    try:
        if _is_reparse_point(candidate) or _is_reparse_point(lease_path):
            raise ResumeLeaseInvalidError(
                "Resume lease path is a link or reparse point",
                stage="resume-lease",
            )
        os.replace(native_long_path(candidate), native_long_path(lease_path))
    except ResumeLeaseError:
        raise
    except OSError as exc:
        raise ResumeLeaseError(
            "Resume lease could not be replaced atomically",
            stage="resume-lease",
            details={"reason": type(exc).__name__},
        ) from exc
    finally:
        _safe_unlink(candidate)


def acquire_resume_lease(
    workspace: OwnedWorkspace,
    *,
    lease_nonce: str | None = None,
    process_is_active: Callable[[dict[str, object]], bool] = owner_process_is_active,
) -> ResumeLease:
    workspace = _validated_workspace(workspace)
    nonce = lease_nonce or uuid.uuid4().hex
    if not _NONCE.fullmatch(nonce):
        raise ValueError("resume lease nonce must be 32 lowercase hexadecimal characters")
    lease_path = workspace.path / RESUME_LEASE_FILENAME
    lock_path = workspace.path / RESUME_LOCK_FILENAME
    if _is_reparse_point(lease_path) or _is_reparse_point(lock_path):
        raise ResumeLeaseInvalidError("Resume lease is a link or reparse point", stage="resume-lease")
    document = _lease_document(workspace, nonce)

    mutex_stream = _open_mutex(lock_path)
    if mutex_stream is None:
        raise ResumeInProgressError(
            "Another resume process holds the writer lease",
            stage="resume-lease",
            details={"reason": "locked"},
        )
    try:
        _validated_workspace(workspace)
        if _is_reparse_point(lock_path) or not _same_open_file(mutex_stream, lock_path):
            raise ResumeLeaseInvalidError("Resume lock identity changed", stage="resume-lease")
        if lease_path.exists():
            previous, raw = _read_lease_document(lease_path, workspace)
            try:
                active = process_is_active(previous)
            except Exception as exc:
                raise ResumeInProgressError(
                    "The previous resume owner cannot be inspected safely",
                    stage="resume-lease",
                    details={"reason": "owner-inspection-failed"},
                ) from exc
            if active:
                raise ResumeInProgressError(
                    "A live resume owner still matches the lease identity",
                    stage="resume-lease",
                    details={"reason": "active-owner"},
                )
            _previous_again, raw_again = _read_lease_document(lease_path, workspace)
            if raw_again != raw:
                raise ResumeInProgressError(
                    "Resume lease changed during stale-owner inspection",
                    stage="resume-lease",
                    details={"reason": "lease-changed"},
                )
        _replace_lease(workspace, lease_path, nonce, document)
        lease = ResumeLease(workspace, lease_path, lock_path, nonce, document, mutex_stream)
        lease.assert_current()
        return lease
    except Exception:
        _unlock_stream(mutex_stream)
        mutex_stream.close()
        raise
