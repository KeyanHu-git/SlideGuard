from __future__ import annotations

import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from slideguard import cli
from slideguard import engine
from slideguard import workspace as workspace_module
from slideguard.workspace import (
    OWNER_MARKER,
    create_owned_workspace,
    delete_owned_workspace,
    mark_workspace_complete,
    safe_delete_owned_workspace,
    scan_owned_workspaces,
)


OLD = "2020-01-01T00:00:00Z"
NOW = datetime(2026, 9, 4, tzinfo=timezone.utc)


def _rewrite_marker(path: Path, **changes: object) -> dict[str, object]:
    marker_path = path / OWNER_MARKER
    document = json.loads(marker_path.read_text(encoding="utf-8"))
    document.update(changes)
    marker_path.write_text(
        json.dumps(document, sort_keys=True, separators=(",", ":")), encoding="utf-8",
    )
    return document


def _scan(root: Path, *, active: bool = False):
    return scan_owned_workspaces(
        root,
        now=NOW,
        retention=timedelta(days=7),
        process_is_active=lambda _marker: active,
    )


def test_owner_marker_binds_nonce_task_and_root_without_storing_root_path(tmp_path: Path):
    root = tmp_path / "SlideGuard" / "w"
    owned = create_owned_workspace(
        root,
        prefix="export-a1b2",
        task_id="paper--a1b2--c3d4",
        kind="export-workspace",
        nonce="1" * 32,
    )

    marker = json.loads((owned.path / OWNER_MARKER).read_text(encoding="utf-8"))
    assert marker["owner"] == "SlideGuard"
    assert marker["instanceNonce"] == "1" * 32
    assert marker["taskId"] == "paper--a1b2--c3d4"
    assert marker["rootFingerprint"] != str(root)
    assert str(tmp_path) not in json.dumps(marker)

    assert safe_delete_owned_workspace(
        owned.path, root, expected_nonce="2" * 32,
    ) == (False, "nonce-mismatch")
    assert owned.path.exists()
    assert delete_owned_workspace(owned)[0] is True


@pytest.mark.parametrize(
    ("marker_body", "reason"),
    [
        (None, "owner marker is missing or unreadable"),
        ("not-json", "owner marker is missing or unreadable"),
        (json.dumps({"owner": "SlideGuard"}), "owner marker fields do not match the schema"),
    ],
)
def test_scan_never_deletes_missing_or_invalid_marker(
    tmp_path: Path, marker_body: str | None, reason: str,
):
    root = tmp_path / "w"
    candidate = root / "unknown"
    candidate.mkdir(parents=True)
    evidence = candidate / "evidence.bin"
    evidence.write_bytes(b"keep")
    if marker_body is not None:
        (candidate / OWNER_MARKER).write_text(marker_body, encoding="utf-8")

    report = _scan(root)

    assert candidate.exists() and evidence.exists()
    assert report.items == (workspace_module.MaintenanceItem("unknown", "preserved", reason),)


def test_marker_copied_from_another_workspace_fails_nonce_name_binding(tmp_path: Path):
    root = tmp_path / "w"
    first = create_owned_workspace(
        root, prefix="first", task_id="one", kind="export-workspace", nonce="1" * 32,
    )
    second = create_owned_workspace(
        root, prefix="second", task_id="two", kind="export-workspace", nonce="2" * 32,
    )
    (second.path / OWNER_MARKER).write_bytes((first.path / OWNER_MARKER).read_bytes())

    report = _scan(root)
    second_result = next(item for item in report.items if item.name == second.path.name)

    assert second.path.exists()
    assert second_result.action == "preserved"
    assert second_result.reason == "owner marker nonce does not match the workspace name"


def test_active_owner_is_preserved_even_after_marked_complete(tmp_path: Path):
    owned = create_owned_workspace(
        tmp_path / "w", prefix="active", task_id="job", kind="export-workspace",
    )
    assert mark_workspace_complete(owned)

    report = _scan(owned.root, active=True)

    assert owned.path.exists()
    assert report.items[0].reason == "active-owner"


def test_owner_process_check_failure_is_not_permission_to_delete(tmp_path: Path):
    owned = create_owned_workspace(
        tmp_path / "w", prefix="unknown-owner", task_id="job", kind="export-workspace",
    )
    assert mark_workspace_complete(owned)

    def cannot_inspect(_marker: dict[str, object]) -> bool:
        raise PermissionError("injected")

    report = scan_owned_workspaces(owned.root, now=NOW, process_is_active=cannot_inspect)

    assert owned.path.exists()
    assert report.items[0].reason == "owner-process-check-failed"


def test_inactive_completed_workspace_is_removed(tmp_path: Path):
    owned = create_owned_workspace(
        tmp_path / "w", prefix="complete", task_id="job", kind="export-workspace",
    )
    (owned.path / "nested").mkdir()
    (owned.path / "nested" / "temporary.bin").write_bytes(b"temporary")
    assert mark_workspace_complete(owned)

    report = _scan(owned.root)

    assert not owned.path.exists()
    assert report.items[0].action == "removed"
    assert report.items[0].reason == "completed-workspace"


def test_expired_preview_and_empty_workspace_are_removed_but_failure_evidence_is_kept(tmp_path: Path):
    root = tmp_path / "w"
    preview = create_owned_workspace(
        root, prefix="preview", task_id="preview", kind="preview-workspace",
    )
    (preview.path / "render.png").write_bytes(b"discardable-preview")
    _rewrite_marker(preview.path, createdAt=OLD)

    empty = create_owned_workspace(
        root, prefix="empty", task_id="empty", kind="export-workspace",
    )
    _rewrite_marker(empty.path, createdAt=OLD)

    evidence = create_owned_workspace(
        root, prefix="failed", task_id="failed", kind="export-workspace",
    )
    (evidence.path / "powerpoint-status.json").write_text("{}", encoding="utf-8")
    _rewrite_marker(evidence.path, createdAt=OLD)

    report = _scan(root)
    reasons = {item.name: (item.action, item.reason) for item in report.items}

    assert not preview.path.exists()
    assert not empty.path.exists()
    assert evidence.path.exists()
    assert reasons[preview.path.name] == ("removed", "expired-preview")
    assert reasons[empty.path.name] == ("removed", "expired-empty-workspace")
    assert reasons[evidence.path.name] == ("preserved", "failure-evidence")


def test_checkpoint_and_gui_draft_are_preserved(tmp_path: Path):
    app_root = tmp_path / "SlideGuard"
    root = app_root / "w"
    owned = create_owned_workspace(
        root, prefix="resume", task_id="resume", kind="export-workspace",
    )
    (owned.path / "job-state.json").write_text('{"schemaVersion":1}', encoding="utf-8")
    _rewrite_marker(owned.path, createdAt=OLD)
    draft = app_root / "gui-drafts" / "a.json"
    draft.parent.mkdir(parents=True)
    draft.write_text("draft", encoding="utf-8")

    report = _scan(root)

    assert owned.path.exists()
    assert report.items[0].reason == "recoverable-checkpoint"
    assert draft.read_text(encoding="utf-8") == "draft"


def test_reparse_point_anywhere_in_tree_refuses_all_deletion(tmp_path: Path, monkeypatch):
    owned = create_owned_workspace(
        tmp_path / "w", prefix="linked", task_id="job", kind="export-workspace",
    )
    trap = owned.path / "junction-trap"
    trap.mkdir()
    outside = tmp_path / "outside.txt"
    outside.write_text("must survive", encoding="utf-8")
    real_detector = workspace_module._is_reparse_point
    monkeypatch.setattr(
        workspace_module,
        "_is_reparse_point",
        lambda path: Path(path) == trap or real_detector(path),
    )

    removed, reason = delete_owned_workspace(owned)

    assert removed is False
    assert "link or reparse point" in reason
    assert owned.path.exists()
    assert outside.read_text(encoding="utf-8") == "must survive"


def test_marker_change_between_validation_and_delete_is_rejected(tmp_path: Path, monkeypatch):
    owned = create_owned_workspace(
        tmp_path / "w", prefix="race", task_id="job", kind="export-workspace",
        nonce="3" * 32,
    )
    original_walk = workspace_module._walk_without_reparse

    def change_after_walk(path: Path):
        result = original_walk(path)
        _rewrite_marker(path, taskId="changed-during-delete")
        return result

    monkeypatch.setattr(workspace_module, "_walk_without_reparse", change_after_walk)

    removed, reason = delete_owned_workspace(owned)

    assert (removed, reason) == (False, "marker-changed")
    assert owned.path.exists()


def test_marker_write_failure_never_triggers_recursive_cleanup(tmp_path: Path, monkeypatch):
    root = tmp_path / "w"

    def fail_replace(_source: object, _destination: object) -> None:
        raise OSError("injected marker replace failure")

    monkeypatch.setattr(workspace_module.os, "replace", fail_replace)

    with pytest.raises(OSError, match="injected"):
        create_owned_workspace(
            root, prefix="failure", task_id="job", kind="export-workspace",
            nonce="5" * 32,
        )

    assert list(root.iterdir()) == []


def test_unsafe_owned_root_is_not_scanned(tmp_path: Path, monkeypatch):
    root = tmp_path / "w"
    root.mkdir()
    (root / "candidate").mkdir()
    real_detector = workspace_module._is_reparse_point
    monkeypatch.setattr(
        workspace_module,
        "_is_reparse_point",
        lambda path: Path(path) == root or real_detector(path),
    )

    report = _scan(root)

    assert report.root_available is False
    assert (root / "candidate").exists()


def test_unavailable_start_token_fails_closed_for_a_live_pid(monkeypatch):
    marker = {"processId": 123, "processStartToken": "unavailable"}
    monkeypatch.setattr(workspace_module, "_process_exists", lambda _pid: True)
    monkeypatch.setattr(workspace_module, "_process_start_token", lambda _pid: None)

    assert workspace_module.owner_process_is_active(marker) is True


def test_standalone_doctor_uses_and_removes_an_owned_workspace(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    monkeypatch.setattr("slideguard.util.require_executable", lambda name: f"C:/tools/{name}.exe")
    monkeypatch.setattr(engine, "svg_renderer_info", lambda: {"name": "test"})
    monkeypatch.setattr(engine, "package_version", lambda _name: "1.0")

    result = engine.doctor(probe_powerpoint=False)

    assert result["ok"] is True
    root = tmp_path / "SlideGuard" / "w"
    assert root.is_dir()
    assert list(root.iterdir()) == []


def test_real_cli_entry_runs_maintenance_but_embedded_calls_do_not(monkeypatch, capsys):
    calls: list[str] = []
    monkeypatch.setattr(cli, "run_startup_maintenance", lambda: calls.append("scan"))
    monkeypatch.setattr(sys, "argv", ["slideguard", "--version"])

    with pytest.raises(SystemExit) as exit_info:
        cli.main()
    assert exit_info.value.code == 0
    assert calls == ["scan"]

    calls.clear()
    with pytest.raises(SystemExit) as exit_info:
        cli.main(["--version"])
    assert exit_info.value.code == 0
    assert calls == []
    capsys.readouterr()
