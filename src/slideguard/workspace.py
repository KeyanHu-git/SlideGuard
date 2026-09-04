from __future__ import annotations

import ctypes
import hashlib
import json
import os
import re
import stat
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Callable

from .util import default_work_root, utc_now


OWNER_MARKER = ".slideguard-owner.json"
OWNER_SCHEMA_VERSION = 1
_NONCE = re.compile(r"^[0-9a-f]{32}$")
_SAFE_PREFIX = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")
_KINDS = {"export-workspace", "doctor-workspace", "preview-workspace"}


class WorkspaceSafetyError(RuntimeError):
    """A fail-closed workspace ownership or path validation error."""


@dataclass(frozen=True, slots=True)
class OwnedWorkspace:
    path: Path
    root: Path
    nonce: str
    task_id: str
    kind: str


@dataclass(frozen=True, slots=True)
class MaintenanceItem:
    name: str
    action: str
    reason: str


@dataclass(frozen=True, slots=True)
class MaintenanceReport:
    root_available: bool
    items: tuple[MaintenanceItem, ...]

    @property
    def removed(self) -> int:
        return sum(item.action == "removed" for item in self.items)


def _absolute(path: Path) -> Path:
    return Path(os.path.abspath(os.fspath(path)))


def _same_path(left: Path, right: Path) -> bool:
    return os.path.normcase(os.fspath(_absolute(left))) == os.path.normcase(os.fspath(_absolute(right)))


def _root_fingerprint(root: Path) -> str:
    normalized = os.path.normcase(os.fspath(_absolute(root).resolve(strict=False)))
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


def _is_reparse_point(path: Path) -> bool:
    """Treat every link or Windows reparse point as unsafe for traversal."""
    try:
        info = os.lstat(path)
    except FileNotFoundError:
        return False
    except OSError:
        return True
    attributes = getattr(info, "st_file_attributes", 0)
    if attributes & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0):
        return True
    if stat.S_ISLNK(info.st_mode):
        return True
    is_junction = getattr(os.path, "isjunction", None)
    return bool(is_junction and is_junction(path))


def _require_safe_root(root: Path) -> Path:
    root = _absolute(root)
    if root.exists() and (_is_reparse_point(root) or not root.is_dir()):
        raise WorkspaceSafetyError("workspace root is not a plain directory")
    return root


def _require_direct_child(path: Path, root: Path) -> tuple[Path, Path]:
    root = _require_safe_root(root)
    path = _absolute(path)
    if _same_path(path, root) or not _same_path(path.parent, root):
        raise WorkspaceSafetyError("workspace must be one direct child of the owned root")
    if _is_reparse_point(path):
        raise WorkspaceSafetyError("workspace is a link or reparse point")
    return path, root


def _process_start_token(process_id: int) -> str | None:
    if process_id <= 0:
        return None
    if os.name == "nt":
        process_query_limited_information = 0x1000
        handle = ctypes.windll.kernel32.OpenProcess(  # type: ignore[attr-defined]
            process_query_limited_information, False, process_id,
        )
        if not handle:
            return None
        try:
            created = ctypes.c_ulonglong()
            exited = ctypes.c_ulonglong()
            kernel = ctypes.c_ulonglong()
            user = ctypes.c_ulonglong()
            ok = ctypes.windll.kernel32.GetProcessTimes(  # type: ignore[attr-defined]
                handle,
                ctypes.byref(created),
                ctypes.byref(exited),
                ctypes.byref(kernel),
                ctypes.byref(user),
            )
            return f"win:{created.value}" if ok else None
        finally:
            ctypes.windll.kernel32.CloseHandle(handle)  # type: ignore[attr-defined]
    proc_stat = Path(f"/proc/{process_id}/stat")
    try:
        value = proc_stat.read_text(encoding="ascii")
        fields = value[value.rfind(")") + 2 :].split()
        return f"proc:{fields[19]}" if len(fields) > 19 else None
    except (OSError, UnicodeError, ValueError):
        return None


def _process_exists(process_id: int) -> bool:
    try:
        os.kill(process_id, 0)
        return True
    except PermissionError:
        return True
    except (OSError, ValueError):
        return False


def owner_process_is_active(marker: dict[str, object]) -> bool:
    process_id = marker["processId"]
    expected = marker["processStartToken"]
    assert isinstance(process_id, int) and isinstance(expected, str)
    if expected == "unavailable":
        return _process_exists(process_id)
    actual = _process_start_token(process_id)
    if actual is None:
        # Failure to inspect a live process is not permission to delete its files.
        return _process_exists(process_id)
    return actual == expected


def _marker_document(
    *, root: Path, nonce: str, task_id: str, kind: str, state: str = "active",
    completed_at: str | None = None,
) -> dict[str, object]:
    process_id = os.getpid()
    return {
        "schemaVersion": OWNER_SCHEMA_VERSION,
        "owner": "SlideGuard",
        "kind": kind,
        "taskId": task_id,
        "instanceNonce": nonce,
        "rootFingerprint": _root_fingerprint(root),
        "processId": process_id,
        "processStartToken": _process_start_token(process_id) or "unavailable",
        "createdAt": utc_now(),
        "completedAt": completed_at,
        "state": state,
    }


def _validate_marker(value: object, *, root: Path) -> dict[str, object]:
    if not isinstance(value, dict):
        raise WorkspaceSafetyError("owner marker is not an object")
    required = {
        "schemaVersion", "owner", "kind", "taskId", "instanceNonce", "rootFingerprint",
        "processId", "processStartToken", "createdAt", "completedAt", "state",
    }
    if set(value) != required:
        raise WorkspaceSafetyError("owner marker fields do not match the schema")
    if value["schemaVersion"] != OWNER_SCHEMA_VERSION or value["owner"] != "SlideGuard":
        raise WorkspaceSafetyError("owner marker identity is not supported")
    if value["kind"] not in _KINDS or value["state"] not in {"active", "complete"}:
        raise WorkspaceSafetyError("owner marker kind or state is invalid")
    if not isinstance(value["taskId"], str) or not value["taskId"] or len(value["taskId"]) > 256:
        raise WorkspaceSafetyError("owner marker taskId is invalid")
    nonce = value["instanceNonce"]
    if not isinstance(nonce, str) or not _NONCE.fullmatch(nonce):
        raise WorkspaceSafetyError("owner marker nonce is invalid")
    fingerprint = value["rootFingerprint"]
    if not isinstance(fingerprint, str) or fingerprint != _root_fingerprint(root):
        raise WorkspaceSafetyError("owner marker belongs to a different root")
    process_id = value["processId"]
    if isinstance(process_id, bool) or not isinstance(process_id, int) or process_id <= 0:
        raise WorkspaceSafetyError("owner marker processId is invalid")
    if not isinstance(value["processStartToken"], str) or not value["processStartToken"]:
        raise WorkspaceSafetyError("owner marker process token is invalid")
    for field in ("createdAt", "completedAt"):
        field_value = value[field]
        if field_value is not None and not isinstance(field_value, str):
            raise WorkspaceSafetyError(f"owner marker {field} is invalid")
    return value


def _read_marker(path: Path, *, root: Path) -> tuple[dict[str, object], bytes]:
    marker_path = path / OWNER_MARKER
    if _is_reparse_point(marker_path):
        raise WorkspaceSafetyError("owner marker is a link or reparse point")
    try:
        raw = marker_path.read_bytes()
        value = json.loads(raw.decode("utf-8", errors="strict"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise WorkspaceSafetyError("owner marker is missing or unreadable") from exc
    marker = _validate_marker(value, root=root)
    nonce = str(marker["instanceNonce"])
    if not path.name.endswith(f"-{nonce[:12]}"):
        raise WorkspaceSafetyError("owner marker nonce does not match the workspace name")
    return marker, raw


def _atomic_write_marker(path: Path, document: dict[str, object], nonce: str) -> None:
    marker_path = path / OWNER_MARKER
    temporary = path / f".{OWNER_MARKER}.{nonce}.tmp"
    payload = json.dumps(document, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    try:
        with open(temporary, "xb") as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, marker_path)
    finally:
        try:
            temporary.unlink(missing_ok=True)
        except OSError:
            pass


def create_owned_workspace(
    root: Path,
    *,
    prefix: str,
    task_id: str,
    kind: str,
    nonce: str | None = None,
) -> OwnedWorkspace:
    if not _SAFE_PREFIX.fullmatch(prefix):
        raise ValueError("workspace prefix must use only ASCII letters, digits, dot, underscore or hyphen")
    if kind not in _KINDS:
        raise ValueError("unknown workspace kind")
    nonce = nonce or uuid.uuid4().hex
    if not _NONCE.fullmatch(nonce):
        raise ValueError("workspace nonce must be 32 lowercase hexadecimal characters")
    root = _require_safe_root(root)
    root.mkdir(parents=True, exist_ok=True)
    root = _require_safe_root(root)
    path = root / f"{prefix}-{nonce[:12]}"
    path.mkdir(exist_ok=False)
    try:
        _atomic_write_marker(path, _marker_document(
            root=root, nonce=nonce, task_id=task_id, kind=kind,
        ), nonce)
    except Exception:
        # Creation has not established ownership, so only remove the known-empty directory.
        try:
            path.rmdir()
        except OSError:
            pass
        raise
    return OwnedWorkspace(path=path, root=root, nonce=nonce, task_id=task_id, kind=kind)


def open_owned_workspace(
    path: Path,
    *,
    expected_kind: str | None = None,
) -> OwnedWorkspace:
    """Open one marker-bound direct child without weakening the owned-root checks."""
    candidate = _absolute(path)
    root = candidate.parent
    candidate, root = _require_direct_child(candidate, root)
    marker, _ = _read_marker(candidate, root=root)
    kind = str(marker["kind"])
    if expected_kind is not None and kind != expected_kind:
        raise WorkspaceSafetyError("workspace kind does not match the requested operation")
    return OwnedWorkspace(
        path=candidate,
        root=root,
        nonce=str(marker["instanceNonce"]),
        task_id=str(marker["taskId"]),
        kind=kind,
    )


def mark_workspace_complete(workspace: OwnedWorkspace) -> bool:
    try:
        path, root = _require_direct_child(workspace.path, workspace.root)
        marker, _ = _read_marker(path, root=root)
        if marker["instanceNonce"] != workspace.nonce or marker["taskId"] != workspace.task_id:
            return False
        marker["state"] = "complete"
        marker["completedAt"] = utc_now()
        _atomic_write_marker(path, marker, workspace.nonce)
        return True
    except (OSError, WorkspaceSafetyError):
        return False


def _walk_without_reparse(path: Path) -> list[Path]:
    if _is_reparse_point(path):
        raise WorkspaceSafetyError("workspace tree contains a link or reparse point")
    result: list[Path] = []
    try:
        with os.scandir(path) as entries:
            for entry in entries:
                child = Path(entry.path)
                if _is_reparse_point(child):
                    raise WorkspaceSafetyError("workspace tree contains a link or reparse point")
                result.append(child)
                if entry.is_dir(follow_symlinks=False):
                    result.extend(_walk_without_reparse(child))
    except OSError as exc:
        raise WorkspaceSafetyError("workspace tree cannot be inspected safely") from exc
    return result


def _remove_tree_without_links(path: Path) -> None:
    if _is_reparse_point(path):
        raise WorkspaceSafetyError("refusing to traverse a link or reparse point")
    with os.scandir(path) as entries:
        children = [Path(entry.path) for entry in entries]
    for child in children:
        if _is_reparse_point(child):
            raise WorkspaceSafetyError("refusing to traverse a link or reparse point")
        if stat.S_ISDIR(os.lstat(child).st_mode):
            _remove_tree_without_links(child)
        else:
            child.unlink()
    path.rmdir()


def safe_delete_owned_workspace(
    path: Path,
    root: Path,
    *,
    expected_nonce: str,
) -> tuple[bool, str]:
    """Delete one direct child only after two marker reads and a link-free walk."""
    try:
        path, root = _require_direct_child(path, root)
        if not path.is_dir():
            return False, "workspace-missing"
        marker, marker_bytes = _read_marker(path, root=root)
        if marker["instanceNonce"] != expected_nonce:
            return False, "nonce-mismatch"
        _walk_without_reparse(path)
        marker_after, marker_bytes_after = _read_marker(path, root=root)
        if marker_bytes_after != marker_bytes or marker_after["instanceNonce"] != expected_nonce:
            return False, "marker-changed"
        _remove_tree_without_links(path)
        return True, "owned-workspace-removed"
    except WorkspaceSafetyError as exc:
        return False, str(exc)
    except OSError as exc:
        return False, f"delete-failed:{type(exc).__name__}"


def delete_owned_workspace(workspace: OwnedWorkspace) -> tuple[bool, str]:
    return safe_delete_owned_workspace(
        workspace.path, workspace.root, expected_nonce=workspace.nonce,
    )


def _parse_time(value: object) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return parsed.astimezone(timezone.utc)
    except ValueError:
        return None


def _checkpoint_present(path: Path) -> bool:
    candidate = path / "job-state.json"
    if not candidate.is_file() or _is_reparse_point(candidate):
        return False
    try:
        value = json.loads(candidate.read_text(encoding="utf-8"))
        return isinstance(value, dict) and "schemaVersion" in value
    except (OSError, UnicodeError, json.JSONDecodeError):
        return False


def _has_payload(paths: list[Path], workspace: Path) -> bool:
    marker = workspace / OWNER_MARKER
    return any(path.is_file() and path != marker for path in paths)


def scan_owned_workspaces(
    root: Path | None = None,
    *,
    now: datetime | None = None,
    retention: timedelta = timedelta(days=7),
    process_is_active: Callable[[dict[str, object]], bool] = owner_process_is_active,
) -> MaintenanceReport:
    """Scan one owned root; preserve anything whose identity or purpose is uncertain."""
    root = _absolute(root or default_work_root())
    if not root.exists():
        return MaintenanceReport(root_available=False, items=())
    try:
        root = _require_safe_root(root)
        with os.scandir(root) as entries:
            children = sorted((Path(entry.path) for entry in entries), key=lambda item: item.name.casefold())
    except (OSError, WorkspaceSafetyError):
        return MaintenanceReport(
            root_available=False,
            items=(MaintenanceItem(root.name, "preserved", "owned-root-unavailable-or-unsafe"),),
        )

    current = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    items: list[MaintenanceItem] = []
    for child in children:
        if _is_reparse_point(child):
            items.append(MaintenanceItem(child.name, "preserved", "link-or-reparse-point"))
            continue
        if not child.is_dir():
            items.append(MaintenanceItem(child.name, "preserved", "not-a-workspace-directory"))
            continue
        try:
            marker, _ = _read_marker(child, root=root)
        except WorkspaceSafetyError as exc:
            items.append(MaintenanceItem(child.name, "preserved", str(exc)))
            continue
        try:
            active = process_is_active(marker)
        except Exception:
            items.append(MaintenanceItem(child.name, "preserved", "owner-process-check-failed"))
            continue
        if active:
            items.append(MaintenanceItem(child.name, "preserved", "active-owner"))
            continue
        try:
            paths = _walk_without_reparse(child)
        except WorkspaceSafetyError as exc:
            items.append(MaintenanceItem(child.name, "preserved", str(exc)))
            continue
        nonce = str(marker["instanceNonce"])
        created = _parse_time(marker["createdAt"])
        expired = created is not None and current - created >= retention
        should_remove = False
        reason = "retention-window"
        if marker["state"] == "complete":
            should_remove, reason = True, "completed-workspace"
        elif _checkpoint_present(child):
            reason = "recoverable-checkpoint"
        elif marker["kind"] == "preview-workspace" and expired:
            should_remove, reason = True, "expired-preview"
        elif _has_payload(paths, child):
            reason = "failure-evidence"
        elif expired:
            should_remove, reason = True, "expired-empty-workspace"

        if not should_remove:
            items.append(MaintenanceItem(child.name, "preserved", reason))
            continue
        removed, delete_reason = safe_delete_owned_workspace(child, root, expected_nonce=nonce)
        items.append(MaintenanceItem(
            child.name,
            "removed" if removed else "preserved",
            reason if removed else delete_reason,
        ))
    return MaintenanceReport(root_available=True, items=tuple(items))


def run_startup_maintenance() -> MaintenanceReport:
    """Best-effort startup maintenance that never widens the owned root."""
    try:
        return scan_owned_workspaces()
    except Exception:
        return MaintenanceReport(root_available=False, items=())
