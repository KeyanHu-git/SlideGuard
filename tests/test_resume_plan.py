from __future__ import annotations

import json
import os
import zipfile
from pathlib import Path

import pytest

from slideguard import PIPELINE_REVISION, __version__
from slideguard.checkpoint import (
    CHECKPOINT_FILENAME,
    CheckpointCursor,
    CheckpointIdentity,
    CheckpointJournal,
    CheckpointPhase,
    CheckpointStatus,
)
from slideguard.engine import ExportOptions, ExportTaskModel, build_export_task_model
from slideguard.resume import ResumePlanningService, build_resume_plan, format_resume_plan
from slideguard.util import checksum_lines, sha256_file
from slideguard.workspace import OwnedWorkspace, create_owned_workspace


def _pptx(path: Path, *, marker: str = "first") -> Path:
    presentation = b'''<p:presentation xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships" xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main"><p:sldIdLst><p:sldId id="256" r:id="rId1"/></p:sldIdLst><p:sldSz cx="12192000" cy="6858000"/></p:presentation>'''
    relationships = b'''<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="slide" Target="slides/slide1.xml"/></Relationships>'''
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("ppt/presentation.xml", presentation)
        archive.writestr("ppt/_rels/presentation.xml.rels", relationships)
        archive.writestr(
            "ppt/slides/slide1.xml",
            f"<p:sld xmlns:p='http://schemas.openxmlformats.org/presentationml/2006/main'><p:extLst>{marker}</p:extLst></p:sld>",
        )
    return path


def _setup(
    tmp_path: Path,
    *,
    options: ExportOptions | None = None,
) -> tuple[Path, ExportOptions, ExportTaskModel, OwnedWorkspace, CheckpointJournal]:
    source = _pptx(tmp_path / "paper.pptx")
    options = options or ExportOptions(slides="1", output_root=tmp_path / "published")
    task = build_export_task_model(source, options, slide_count=1)
    workspace = create_owned_workspace(
        tmp_path / "work",
        prefix="resume",
        task_id=task.job_id,
        kind="export-workspace",
        nonce="1" * 32,
    )
    identity = CheckpointIdentity.create(
        task_id=task.job_id,
        workspace_nonce=workspace.nonce,
        request_fingerprint=task.request_fingerprint,
        source_name=source.name,
        source_sha256=task.source_sha256,
        tool_version=__version__,
        pipeline_revision=PIPELINE_REVISION,
    )
    return source, options, task, workspace, CheckpointJournal(workspace, identity, task.slides)


def _through_validation(workspace: OwnedWorkspace, journal: CheckpointJournal) -> dict[str, Path]:
    journal.advance(CheckpointPhase.DISCOVER)
    journal.advance(CheckpointPhase.PREFLIGHT)
    journal.advance(CheckpointPhase.INVENTORY)
    slide = workspace.path / "slide-0001"
    slide.mkdir()
    native = slide / "powerpoint-native.pdf"
    reference = slide / "powerpoint-reference.png"
    native.write_bytes(b"native-pdf")
    reference.write_bytes(b"reference-png")
    cursor = CheckpointCursor(1, 1)
    journal.advance(
        CheckpointPhase.NATIVE_EXPORT,
        cursor=cursor,
        artifacts=[("native-pdf", native), ("reference-png", reference)],
    )
    package = workspace.path / "package"
    (package / "svg").mkdir(parents=True)
    (package / "png").mkdir()
    pdf = package / "paper--p0001-s0001.pdf"
    raw_svg = slide / "powerpoint-native.svg"
    svg = package / "svg" / "paper--p0001-s0001.svg"
    pdf.write_bytes(b"patched-pdf")
    raw_svg.write_text("<svg><path/></svg>", encoding="utf-8")
    svg.write_text("<svg><path/></svg>", encoding="utf-8")
    journal.advance(
        CheckpointPhase.PATCH,
        cursor=cursor,
        artifacts=[("pdf", pdf), ("raw-svg", raw_svg), ("svg", svg)],
    )
    png = package / "png" / "paper--p0001-s0001.png"
    evidence = package / "evidence" / "p0001-s0001" / "svg-640.png"
    evidence.parent.mkdir(parents=True)
    png.write_bytes(b"accepted-png")
    evidence.write_bytes(b"evidence")
    journal.advance(
        CheckpointPhase.VALIDATE,
        cursor=cursor,
        artifacts=[("png", png), ("evidence", evidence)],
    )
    return {
        "native": native,
        "reference": reference,
        "pdf": pdf,
        "raw_svg": raw_svg,
        "svg": svg,
        "png": png,
        "evidence": evidence,
        "package": package,
    }


def _through_publish_pending(
    workspace: OwnedWorkspace,
    journal: CheckpointJournal,
) -> dict[str, Path]:
    paths = _through_validation(workspace, journal)
    package = paths["package"]
    artifacts = [paths["pdf"], paths["svg"], paths["png"]]
    manifest = package / "manifest.json"
    manifest.write_text(
        json.dumps({
            "artifacts": [
                {
                    "path": item.relative_to(package).as_posix(),
                    "sha256": sha256_file(item),
                }
                for item in artifacts
            ]
        }),
        encoding="utf-8",
    )
    report = package / "qa-report.json"
    html = package / "report.html"
    junit = package / "junit.xml"
    report.write_text("{}", encoding="utf-8")
    html.write_text("<!doctype html>", encoding="utf-8")
    junit.write_text("<testsuite/>", encoding="utf-8")
    checksum = package / "checksums.sha256"
    checksum.write_text(
        checksum_lines([path for path in package.rglob("*") if path.is_file()], package),
        encoding="ascii",
    )
    journal.advance(
        CheckpointPhase.PACKAGE,
        artifacts=[("package-file", path) for path in package.rglob("*") if path.is_file()],
    )
    journal.advance(CheckpointPhase.PUBLISH, status=CheckpointStatus.PENDING)
    paths.update({
        "manifest": manifest,
        "report": report,
        "html": html,
        "junit": junit,
        "checksum": checksum,
    })
    return paths


def _plan(workspace: OwnedWorkspace, task: ExportTaskModel) -> dict:
    return build_resume_plan(workspace, task, compact_svg=False)


def test_complete_match_reuses_only_verified_stages_and_keeps_publication_atomic(tmp_path: Path):
    _source, _options, task, workspace, journal = _setup(tmp_path)
    _through_publish_pending(workspace, journal)

    before = {
        path.relative_to(workspace.path).as_posix(): (path.stat().st_size, sha256_file(path))
        for path in workspace.path.rglob("*")
        if path.is_file()
    }
    first = _plan(workspace, task)
    second = _plan(workspace, task)
    after = {
        path.relative_to(workspace.path).as_posix(): (path.stat().st_size, sha256_file(path))
        for path in workspace.path.rglob("*")
        if path.is_file()
    }

    assert first == second
    assert first["status"] == "resumable"
    assert first["reusedThroughSequence"] == 6
    assert first["resumeFromSequence"] == 7
    assert [item["action"] for item in first["steps"][:7]] == ["reuse"] * 7
    assert first["steps"][7]["reasonCode"] == "PENDING_STAGE_NOT_REUSABLE"
    assert first["publication"] == {
        "outputName": task.final_dir.name,
        "action": "publish-atomically",
        "reasonCode": "ATOMIC_PUBLISH_REQUIRED",
    }
    assert before == after
    assert not task.final_dir.exists()


def test_same_filename_with_changed_pptx_content_rejects_every_reuse(tmp_path: Path):
    source, options, _task, workspace, journal = _setup(tmp_path)
    journal.advance(CheckpointPhase.DISCOVER)
    _pptx(source, marker="changed")
    changed_task = build_export_task_model(source, options, slide_count=1)

    plan = _plan(workspace, changed_task)

    assert plan["status"] == "rejected"
    assert plan["error"]["code"] == "SOURCE_SHA256_MISMATCH"
    assert {item["action"] for item in plan["steps"]} == {"reject"}


def test_changing_only_one_crop_edge_rejects_old_request_identity(tmp_path: Path):
    source, _options, _task, workspace, journal = _setup(
        tmp_path,
        options=ExportOptions(
            slides="1",
            output_root=tmp_path / "published",
            crop_percent=(1.0, 2.0, 90.0, 91.0),
        ),
    )
    journal.advance(CheckpointPhase.DISCOVER)
    changed = build_export_task_model(
        source,
        ExportOptions(
            slides="1",
            output_root=tmp_path / "published",
            crop_percent=(1.1, 2.0, 90.0, 91.0),
        ),
        slide_count=1,
    )

    plan = _plan(workspace, changed)

    assert plan["status"] == "rejected"
    assert plan["error"]["code"] == "REQUEST_FINGERPRINT_MISMATCH"


def test_same_size_different_hash_recomputes_that_stage_and_every_downstream_stage(tmp_path: Path):
    _source, _options, task, workspace, journal = _setup(tmp_path)
    paths = _through_publish_pending(workspace, journal)
    paths["native"].write_bytes(b"xxxxx-yyyy")
    assert len(b"xxxxx-yyyy") == len(b"native-pdf")

    plan = _plan(workspace, task)

    native = plan["steps"][3]
    assert native["phase"] == "NATIVE_EXPORT"
    assert native["action"] == "recompute"
    assert native["reasonCode"] == "ARTIFACT_HASH_MISMATCH"
    assert native["artifacts"][0]["expectedBytes"] == native["artifacts"][0]["actualBytes"]
    assert all(item["action"] == "reuse" for item in plan["steps"][:3])
    assert all(item["action"] == "recompute" for item in plan["steps"][3:])


def test_missing_required_stage_record_invalidates_it_and_downstream_only(tmp_path: Path):
    _source, _options, task, workspace, journal = _setup(tmp_path)
    _through_validation(workspace, journal)
    state_path = workspace.path / CHECKPOINT_FILENAME
    document = json.loads(state_path.read_text(encoding="utf-8"))
    document["artifacts"] = [
        item for item in document["artifacts"] if item["kind"] != "raw-svg"
    ]
    state_path.write_text(json.dumps(document), encoding="utf-8")

    plan = _plan(workspace, task)

    assert plan["steps"][3]["action"] == "reuse"
    assert plan["steps"][4]["reasonCode"] == "STAGE_RECORD_MISSING"
    assert all(item["action"] == "recompute" for item in plan["steps"][4:])


def test_future_checkpoint_version_is_a_machine_readable_hard_rejection(tmp_path: Path):
    _source, _options, task, workspace, journal = _setup(tmp_path)
    journal.advance(CheckpointPhase.DISCOVER)
    state_path = workspace.path / CHECKPOINT_FILENAME
    document = json.loads(state_path.read_text(encoding="utf-8"))
    document["schemaVersion"] = "2.0"
    state_path.write_text(json.dumps(document), encoding="utf-8")

    plan = _plan(workspace, task)

    assert plan["status"] == "rejected"
    assert plan["error"] == {
        "code": "CHECKPOINT_VERSION_UNSUPPORTED",
        "stage": "resume-plan",
    }


def test_forged_complete_state_is_never_treated_as_a_published_result(tmp_path: Path):
    _source, _options, task, workspace, journal = _setup(tmp_path)
    _through_publish_pending(workspace, journal)
    state_path = workspace.path / CHECKPOINT_FILENAME
    document = json.loads(state_path.read_text(encoding="utf-8"))
    document["state"].update(sequence=8, status="complete")
    document["complete"] = True
    state_path.write_text(json.dumps(document), encoding="utf-8")

    plan = _plan(workspace, task)

    assert plan["status"] == "rejected"
    assert plan["error"]["code"] == "CHECKPOINT_COMPLETION_UNTRUSTED"
    assert not task.final_dir.exists()


def test_existing_formal_output_name_fails_closed_without_touching_either_tree(tmp_path: Path):
    _source, _options, task, workspace, journal = _setup(tmp_path)
    journal.advance(CheckpointPhase.DISCOVER)
    task.final_dir.mkdir(parents=True)
    sentinel = task.final_dir / "do-not-touch.txt"
    sentinel.write_text("published", encoding="utf-8")
    checkpoint_before = (workspace.path / CHECKPOINT_FILENAME).read_bytes()

    plan = _plan(workspace, task)

    assert plan["status"] == "rejected"
    assert plan["error"]["code"] == "OUTPUT_COLLISION"
    assert sentinel.read_text(encoding="utf-8") == "published"
    assert (workspace.path / CHECKPOINT_FILENAME).read_bytes() == checkpoint_before


def test_mtime_is_not_part_of_artifact_reuse_or_plan_identity(tmp_path: Path):
    _source, _options, task, workspace, journal = _setup(tmp_path)
    paths = _through_validation(workspace, journal)
    first = _plan(workspace, task)
    stat = paths["native"].stat()
    os.utime(paths["native"], (stat.st_atime + 100, stat.st_mtime + 100))

    second = _plan(workspace, task)

    assert first == second
    assert second["steps"][3]["action"] == "reuse"


def test_json_cli_and_application_service_return_the_same_plan(
    tmp_path: Path,
    capfd: pytest.CaptureFixture[str],
):
    from slideguard import cli

    source, _options, task, workspace, journal = _setup(tmp_path)
    journal.advance(CheckpointPhase.DISCOVER)
    request = {
        "schemaVersion": "1.0",
        "input": source.name,
        "outputRoot": "published",
    }
    request_path = tmp_path / "request.json"
    request_path.write_text(json.dumps(request), encoding="utf-8")
    expected = ResumePlanningService().execute(
        request,
        workspace_path=workspace.path,
        base_dir=tmp_path,
    )

    exit_code = cli.main([
        "resume-plan",
        str(request_path),
        "--workspace",
        str(workspace.path),
        "--json",
    ])
    captured = capfd.readouterr()

    assert exit_code == 0
    assert captured.err == ""
    assert json.loads(captured.out) == expected
    assert expected["taskId"] == task.job_id


def test_human_formatter_is_a_view_of_the_same_reason_codes(tmp_path: Path):
    _source, _options, task, workspace, journal = _setup(tmp_path)
    journal.advance(CheckpointPhase.DISCOVER)

    plan = _plan(workspace, task)
    rendered = format_resume_plan(plan)

    assert "RESUMABLE" in rendered
    assert plan["steps"][1]["reasonCode"] in rendered
    assert "atomic publish" in rendered
