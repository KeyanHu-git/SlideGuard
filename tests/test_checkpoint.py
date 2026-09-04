from __future__ import annotations

import builtins
import errno
import json
import zipfile
from pathlib import Path

import pytest

from slideguard import checkpoint as checkpoint_module
from slideguard import engine
from slideguard.checkpoint import (
    CHECKPOINT_FILENAME,
    CheckpointArtifactError,
    CheckpointCursor,
    CheckpointIdentity,
    CheckpointIdentityError,
    CheckpointJournal,
    CheckpointPathError,
    CheckpointPhase,
    CheckpointReadError,
    CheckpointSchemaError,
    CheckpointStatus,
    CheckpointTransitionError,
    CheckpointVersionError,
    CheckpointWriteError,
    load_checkpoint,
)
from slideguard.errors import EnvironmentError
from slideguard.workspace import OWNER_MARKER, OwnedWorkspace, create_owned_workspace


SOURCE_HASH = "a" * 64
REQUEST_FINGERPRINT = "sha256:" + "b" * 64


def _pptx(path: Path) -> Path:
    presentation = b'''<p:presentation xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships" xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main"><p:sldIdLst><p:sldId id="256" r:id="rId1"/></p:sldIdLst><p:sldSz cx="12192000" cy="6858000"/></p:presentation>'''
    relationships = b'''<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="slide" Target="slides/slide1.xml"/></Relationships>'''
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("ppt/presentation.xml", presentation)
        archive.writestr("ppt/_rels/presentation.xml.rels", relationships)
        archive.writestr(
            "ppt/slides/slide1.xml",
            "<p:sld xmlns:p='http://schemas.openxmlformats.org/presentationml/2006/main'/>",
        )
    return path


def _workspace(tmp_path: Path, *, nonce: str = "1" * 32):
    return create_owned_workspace(
        tmp_path / "w",
        prefix="checkpoint",
        task_id="paper--aaaaaaaa--bbbbbbbb",
        kind="export-workspace",
        nonce=nonce,
    )


def _identity(workspace, *, source_hash: str = SOURCE_HASH):
    return CheckpointIdentity.create(
        task_id=workspace.task_id,
        workspace_nonce=workspace.nonce,
        request_fingerprint=REQUEST_FINGERPRINT,
        source_name="paper.pptx",
        source_sha256=source_hash,
        tool_version="0.2.0.dev0",
        pipeline_revision="2026-09-04.test",
    )


def _journal(tmp_path: Path, *, slides=(2, 5)):
    workspace = _workspace(tmp_path)
    return workspace, CheckpointJournal(workspace, _identity(workspace), slides)


def _advance_to_inventory(journal: CheckpointJournal) -> None:
    journal.advance(CheckpointPhase.DISCOVER)
    journal.advance(CheckpointPhase.PREFLIGHT)
    journal.advance(CheckpointPhase.INVENTORY)


def test_schema_model_and_full_state_machine_round_trip(tmp_path: Path):
    workspace, journal = _journal(tmp_path)
    artifact = workspace.path / "slide-0002" / "native.pdf"
    artifact.parent.mkdir()
    artifact.write_bytes(b"native-pdf")

    _advance_to_inventory(journal)
    journal.advance(
        CheckpointPhase.NATIVE_EXPORT,
        cursor=CheckpointCursor(1, 2),
        artifacts=[("native-pdf", artifact)],
    )
    journal.advance(CheckpointPhase.PATCH, cursor=CheckpointCursor(1, 2))
    journal.advance(CheckpointPhase.VALIDATE, cursor=CheckpointCursor(1, 2))
    journal.advance(CheckpointPhase.NATIVE_EXPORT, cursor=CheckpointCursor(2, 5))
    journal.advance(CheckpointPhase.PATCH, cursor=CheckpointCursor(2, 5))
    journal.advance(CheckpointPhase.VALIDATE, cursor=CheckpointCursor(2, 5))
    journal.advance(CheckpointPhase.PACKAGE)
    journal.advance(CheckpointPhase.PUBLISH, status=CheckpointStatus.PENDING)
    final = journal.advance(CheckpointPhase.PUBLISH)

    loaded = load_checkpoint(workspace, expected_identity=_identity(workspace))
    assert loaded == final
    assert loaded.complete is True
    assert loaded.state.sequence == 11
    assert loaded.artifacts[0].path == "slide-0002/native.pdf"
    assert set(loaded.to_document()) == {
        "schemaVersion", "kind", "taskId", "workspaceNonce", "requestFingerprint",
        "resumeKey", "source", "tool", "selectedSlides", "state", "artifacts",
        "complete", "writtenAt",
    }


def test_resume_identity_excludes_nonce_and_time_but_binds_source_and_request(tmp_path: Path):
    first = _workspace(tmp_path / "first", nonce="1" * 32)
    second = _workspace(tmp_path / "second", nonce="2" * 32)
    first_identity = _identity(first)
    second_identity = _identity(second)

    assert first_identity.resume_key == second_identity.resume_key
    changed_source = _identity(second, source_hash="c" * 64)
    assert changed_source.resume_key != first_identity.resume_key
    assert first_identity.workspace_nonce != second_identity.workspace_nonce


@pytest.mark.parametrize("failure", ["partial", "flush", "disk-full", "permission"])
def test_atomic_write_failures_preserve_previous_snapshot_and_control_files(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, failure: str,
):
    workspace, journal = _journal(tmp_path, slides=(1,))
    first = journal.advance(CheckpointPhase.DISCOVER)
    checkpoint_path = workspace.path / CHECKPOINT_FILENAME
    original = checkpoint_path.read_bytes()
    manifest = workspace.path / "manifest.json"
    manifest.write_text('{"published":true}', encoding="utf-8")

    if failure == "partial":
        def partial_then_fail(path: Path, payload: bytes) -> None:
            path.write_bytes(payload[: max(1, len(payload) // 2)])
            raise OSError(errno.EIO, "injected partial write")

        monkeypatch.setattr(checkpoint_module, "_write_temp_file", partial_then_fail)
    elif failure == "flush":
        monkeypatch.setattr(
            checkpoint_module.os,
            "replace",
            lambda *_args: (_ for _ in ()).throw(OSError(errno.EIO, "injected replace failure")),
        )
    elif failure == "disk-full":
        monkeypatch.setattr(
            checkpoint_module.os,
            "fsync",
            lambda *_args: (_ for _ in ()).throw(OSError(errno.ENOSPC, "injected disk full")),
        )
    else:
        real_open = builtins.open

        def deny_checkpoint(path, mode="r", *args, **kwargs):
            if mode == "xb" and ".job-state." in str(path):
                raise PermissionError(errno.EACCES, "injected access denied")
            return real_open(path, mode, *args, **kwargs)

        monkeypatch.setattr(builtins, "open", deny_checkpoint)

    with pytest.raises(CheckpointWriteError) as error:
        journal.advance(CheckpointPhase.PREFLIGHT)

    assert error.value.code == "CHECKPOINT_WRITE_FAILED"
    assert checkpoint_path.read_bytes() == original
    assert load_checkpoint(workspace, verify_artifacts=False).state == first.state
    assert manifest.read_text(encoding="utf-8") == '{"published":true}'
    assert list(workspace.path.glob(".job-state.*.tmp")) == []


def test_replace_without_next_stage_exposes_last_complete_state(tmp_path: Path):
    workspace, journal = _journal(tmp_path, slides=(1,))
    journal.advance(CheckpointPhase.DISCOVER)
    expected = journal.advance(CheckpointPhase.PREFLIGHT)

    loaded = load_checkpoint(workspace, verify_artifacts=False)

    assert loaded.state == expected.state
    assert loaded.state.phase == CheckpointPhase.PREFLIGHT
    assert loaded.complete is False


@pytest.mark.parametrize(
    ("mutate", "error_type", "code"),
    [
        (lambda document: document.update(schemaVersion="2.0"), CheckpointVersionError, "CHECKPOINT_VERSION_UNSUPPORTED"),
        (lambda document: document.update(unexpected=True), CheckpointSchemaError, "CHECKPOINT_SCHEMA_INVALID"),
        (lambda document: document["source"].update(name="C:/Users/name/paper.pptx"), CheckpointPathError, "CHECKPOINT_PATH_UNSAFE"),
        (lambda document: document.update(resumeKey="sha256:" + "0" * 64), CheckpointIdentityError, "CHECKPOINT_IDENTITY_MISMATCH"),
        (lambda document: document["state"].update(sequence=99), CheckpointTransitionError, "CHECKPOINT_TRANSITION_INVALID"),
    ],
)
def test_invalid_checkpoint_documents_fail_with_stable_codes(
    tmp_path: Path, mutate, error_type, code: str,
):
    workspace, journal = _journal(tmp_path, slides=(1,))
    journal.advance(CheckpointPhase.DISCOVER)
    path = workspace.path / CHECKPOINT_FILENAME
    document = json.loads(path.read_text(encoding="utf-8"))
    mutate(document)
    path.write_text(json.dumps(document), encoding="utf-8")

    with pytest.raises(error_type) as error:
        load_checkpoint(workspace, verify_artifacts=False)

    assert error.value.code == code


@pytest.mark.parametrize(
    "payload",
    [
        b'{"schemaVersion":"1.0"',
        b'\xff\xfe{bad}',
        b'{"schemaVersion":"1.0","schemaVersion":"1.0"}',
    ],
)
def test_truncated_non_utf8_and_duplicate_key_json_are_never_accepted(tmp_path: Path, payload: bytes):
    workspace, journal = _journal(tmp_path, slides=(1,))
    journal.advance(CheckpointPhase.DISCOVER)
    (workspace.path / CHECKPOINT_FILENAME).write_bytes(payload)

    with pytest.raises(CheckpointReadError) as error:
        load_checkpoint(workspace, verify_artifacts=False)

    assert error.value.code == "CHECKPOINT_READ_FAILED"


def test_absolute_artifact_path_is_rejected_before_schema_details_can_echo_it(tmp_path: Path):
    workspace, journal = _journal(tmp_path, slides=(1,))
    journal.advance(CheckpointPhase.DISCOVER)
    path = workspace.path / CHECKPOINT_FILENAME
    document = json.loads(path.read_text(encoding="utf-8"))
    document["artifacts"] = [{
        "kind": "native-pdf",
        "path": "C:/Users/name/private.pdf",
        "bytes": 1,
        "sha256": "0" * 64,
        "phase": "DISCOVER",
        "sequence": 0,
    }]
    path.write_text(json.dumps(document), encoding="utf-8")

    with pytest.raises(CheckpointPathError) as error:
        load_checkpoint(workspace, verify_artifacts=False)

    assert error.value.code == "CHECKPOINT_PATH_UNSAFE"
    assert "Users" not in str(error.value)


def test_windows_alternate_data_stream_artifact_path_is_rejected_before_access(tmp_path: Path):
    workspace, journal = _journal(tmp_path, slides=(1,))
    journal.advance(CheckpointPhase.DISCOVER)
    path = workspace.path / CHECKPOINT_FILENAME
    document = json.loads(path.read_text(encoding="utf-8"))
    document["artifacts"] = [{
        "kind": "native-pdf",
        "path": "slide-0001/native.pdf:stream",
        "bytes": 1,
        "sha256": "0" * 64,
        "phase": "DISCOVER",
        "sequence": 0,
    }]
    path.write_text(json.dumps(document), encoding="utf-8")

    with pytest.raises(CheckpointPathError) as error:
        load_checkpoint(workspace, verify_artifacts=False)

    assert error.value.code == "CHECKPOINT_PATH_UNSAFE"


def test_artifact_size_hash_and_immutability_are_verified(tmp_path: Path):
    workspace, journal = _journal(tmp_path, slides=(1,))
    artifact = workspace.path / "native.pdf"
    artifact.write_bytes(b"first")
    journal.advance(CheckpointPhase.DISCOVER)
    journal.advance(CheckpointPhase.PREFLIGHT)
    journal.advance(CheckpointPhase.INVENTORY)
    journal.advance(
        CheckpointPhase.NATIVE_EXPORT,
        cursor=CheckpointCursor(1, 1),
        artifacts=[("native-pdf", artifact)],
    )
    artifact.write_bytes(b"changed")

    with pytest.raises(CheckpointArtifactError) as load_error:
        load_checkpoint(workspace)
    assert load_error.value.code == "CHECKPOINT_ARTIFACT_INVALID"

    with pytest.raises(CheckpointArtifactError) as advance_error:
        journal.advance(
            CheckpointPhase.PATCH,
            cursor=CheckpointCursor(1, 1),
            artifacts=[("native-pdf", artifact)],
        )
    assert advance_error.value.code == "CHECKPOINT_ARTIFACT_INVALID"


def test_artifact_phase_and_sequence_cannot_claim_future_work(tmp_path: Path):
    workspace, journal = _journal(tmp_path, slides=(1,))
    artifact = workspace.path / "future.bin"
    artifact.write_bytes(b"future")
    journal.advance(CheckpointPhase.DISCOVER)
    path = workspace.path / CHECKPOINT_FILENAME
    document = json.loads(path.read_text(encoding="utf-8"))
    document["artifacts"] = [{
        "kind": "package-file",
        "path": "future.bin",
        "bytes": artifact.stat().st_size,
        "sha256": checkpoint_module.sha256_file(artifact),
        "phase": "PUBLISH",
        "sequence": 0,
    }]
    path.write_text(json.dumps(document), encoding="utf-8")

    with pytest.raises(CheckpointTransitionError) as error:
        load_checkpoint(workspace, verify_artifacts=False)

    assert error.value.code == "CHECKPOINT_TRANSITION_INVALID"


def test_checkpoint_cannot_bind_to_a_different_workspace_nonce(tmp_path: Path):
    workspace, journal = _journal(tmp_path, slides=(1,))
    journal.advance(CheckpointPhase.DISCOVER)
    different = CheckpointIdentity.create(
        task_id=workspace.task_id,
        workspace_nonce="9" * 32,
        request_fingerprint=REQUEST_FINGERPRINT,
        source_name="paper.pptx",
        source_sha256=SOURCE_HASH,
        tool_version="0.2.0.dev0",
        pipeline_revision="2026-09-04.test",
    )

    with pytest.raises(CheckpointIdentityError) as error:
        load_checkpoint(workspace, expected_identity=different, verify_artifacts=False)

    assert error.value.code == "CHECKPOINT_IDENTITY_MISMATCH"


@pytest.mark.parametrize(
    ("phase", "cursor"),
    [
        (CheckpointPhase.PREFLIGHT, None),
        (CheckpointPhase.NATIVE_EXPORT, CheckpointCursor(1, 2)),
        (CheckpointPhase.PUBLISH, None),
    ],
)
def test_state_machine_rejects_skipped_or_unbound_transitions(tmp_path: Path, phase, cursor):
    _workspace_value, journal = _journal(tmp_path, slides=(1,))

    with pytest.raises(CheckpointTransitionError) as error:
        journal.advance(phase, cursor=cursor)

    assert error.value.code == "CHECKPOINT_TRANSITION_INVALID"


def test_export_engine_persists_discover_checkpoint_before_preflight_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
):
    source = _pptx(tmp_path / "private-paper.pptx")
    work_root = tmp_path / "SlideGuard" / "w"
    monkeypatch.setattr(engine, "default_work_root", lambda: work_root)
    monkeypatch.setattr(engine, "doctor", lambda *_args, **_kwargs: {"ok": False, "errors": ["missing dependency"]})

    with pytest.raises(EnvironmentError):
        engine.export_job(source, engine.ExportOptions(slides="1", output_root=tmp_path / "output"))

    workspaces = list(work_root.iterdir())
    assert len(workspaces) == 1
    marker = json.loads((workspaces[0] / OWNER_MARKER).read_text(encoding="utf-8"))
    workspace = OwnedWorkspace(
        path=workspaces[0],
        root=work_root,
        nonce=marker["instanceNonce"],
        task_id=marker["taskId"],
        kind=marker["kind"],
    )
    checkpoint = load_checkpoint(workspace, verify_artifacts=True)
    document_text = (workspace.path / CHECKPOINT_FILENAME).read_text(encoding="utf-8")

    assert checkpoint.state == checkpoint_module.CheckpointState(
        sequence=0,
        phase=CheckpointPhase.DISCOVER,
        status=CheckpointStatus.COMPLETE,
    )
    assert checkpoint.identity.source_name == source.name
    assert checkpoint.identity.source_sha256 == checkpoint_module.sha256_file(source)
    assert str(tmp_path) not in document_text


def test_export_engine_advances_checkpoint_through_atomic_publish(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
):
    source = _pptx(tmp_path / "paper.pptx")
    work_root = tmp_path / "SlideGuard" / "w"
    monkeypatch.setattr(engine, "default_work_root", lambda: work_root)
    monkeypatch.setattr(
        engine,
        "doctor",
        lambda *_args, **_kwargs: {"ok": True, "errors": [], "powerpoint": None},
    )

    def fake_export(_source, slide, slide_work, _width, **_kwargs):
        native = slide_work / "native.pdf"
        reference = slide_work / "reference.png"
        native.write_bytes(b"native-pdf")
        reference.write_bytes(b"reference-png")
        return {
            "nativePdf": str(native),
            "referencePng": str(reference),
            "powerpoint": {"version": "test"},
            "slideWidthPt": 100.0,
            "slideHeightPt": 80.0,
        }

    def fake_patch_pdf(_native, _package, _slide, _reference, output, _options):
        output.write_bytes(b"patched-pdf")
        return engine.PdfPatchResult("content", 0, 0, 0, [0.0, 0.0, 100.0, 80.0], 11), {}

    def fake_convert(_native, output):
        output.write_text("<svg/>", encoding="utf-8")

    def fake_restore_svg(_raw, _package, _slide, _reference, output, **_kwargs):
        output.write_text("<svg><path d='M0 0'/></svg>", encoding="utf-8")
        return engine.SvgPatchResult(0, 0, 0, 0, [0.0, 0.0, 100.0, 80.0], output.stat().st_size)

    def fake_pdf_render(_pdf, _native, evidence, *_args, **_kwargs):
        evidence.mkdir(parents=True, exist_ok=True)
        (evidence / "pdf-72.png").write_bytes(b"pdf-render")
        return []

    def fake_svg_render(_svg, evidence, *_args, **_kwargs):
        evidence.mkdir(parents=True, exist_ok=True)
        accepted = evidence / "svg-640.png"
        accepted.write_bytes(b"svg-render")
        return [], accepted

    monkeypatch.setattr(engine, "export_reference", fake_export)
    monkeypatch.setattr(engine, "_patch_pdf_with_budget", fake_patch_pdf)
    monkeypatch.setattr(engine, "convert_pdf_to_svg", fake_convert)
    monkeypatch.setattr(engine, "restore_svg_images", fake_restore_svg)
    monkeypatch.setattr(engine, "validate_multiscale_pdf", fake_pdf_render)
    monkeypatch.setattr(engine, "validate_svg_renders", fake_svg_render)
    monkeypatch.setattr(engine, "validate_pdf_structure", lambda *_args, **_kwargs: [])
    monkeypatch.setattr(engine, "validate_svg_structure", lambda *_args, **_kwargs: [])
    monkeypatch.setattr(engine, "validate_svg_vector_invariant", lambda *_args, **_kwargs: [])
    monkeypatch.setattr(engine, "coverage_findings", lambda *_args, **_kwargs: [])
    monkeypatch.setattr(engine, "mark_workspace_complete", lambda _workspace: False)

    output, report = engine.export_job(
        source,
        engine.ExportOptions(slides="1", output_root=tmp_path / "output"),
    )

    assert report.verdict.value == "PASS"
    assert (output / "manifest.json").is_file()
    assert not (output / CHECKPOINT_FILENAME).exists()
    workspaces = list(work_root.iterdir())
    assert len(workspaces) == 1
    marker = json.loads((workspaces[0] / OWNER_MARKER).read_text(encoding="utf-8"))
    workspace = OwnedWorkspace(
        path=workspaces[0], root=work_root, nonce=marker["instanceNonce"],
        task_id=marker["taskId"], kind=marker["kind"],
    )
    checkpoint = load_checkpoint(workspace)

    assert checkpoint.state.phase == CheckpointPhase.PUBLISH
    assert checkpoint.state.status == CheckpointStatus.COMPLETE
    assert checkpoint.state.sequence == 8
    assert checkpoint.complete is True
    assert {item.kind for item in checkpoint.artifacts} >= {
        "native-pdf", "reference-png", "pdf", "raw-svg", "svg", "png", "evidence",
    }
    assert all(not Path(item.path).is_absolute() for item in checkpoint.artifacts)
