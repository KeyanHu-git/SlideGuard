from __future__ import annotations

import json
import zipfile
from pathlib import Path

from slideguard import PIPELINE_REVISION, __version__
from slideguard.application import ExportService
from slideguard.batch import BatchService, is_retryable_result
from slideguard.cli import main
from slideguard.contracts import CAPABILITIES, failed_result, prepare_request, validate_document, validated_result
from slideguard.errors import BudgetError, ExportError, InputError
from slideguard.util import checksum_lines, sha256_file


def _pptx(path: Path) -> Path:
    presentation = b'''<p:presentation xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships" xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main"><p:sldIdLst><p:sldId id="256" r:id="rId1"/></p:sldIdLst><p:sldSz cx="12192000" cy="6858000"/></p:presentation>'''
    rels = b'''<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="slide" Target="slides/slide1.xml"/></Relationships>'''
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("ppt/presentation.xml", presentation)
        archive.writestr("ppt/_rels/presentation.xml.rels", rels)
        archive.writestr("ppt/slides/slide1.xml", "<p:sld xmlns:p='http://schemas.openxmlformats.org/presentationml/2006/main'/>")
    return path


def _job(source: Path, **extra) -> dict:
    result = {
        "schemaVersion": "1.0",
        "taskId": source.stem,
        "input": source.name,
        "outputRoot": "out",
        "behavior": {"dryRun": True},
    }
    result.update(extra)
    return result


def _batch(jobs: list[dict], **behavior) -> dict:
    return {
        "schemaVersion": "1.0",
        "batchId": "batch-test",
        "jobs": jobs,
        "behavior": behavior,
    }


def test_continue_isolates_invalid_item_and_preserves_order(tmp_path: Path):
    first = _pptx(tmp_path / "first.pptx")
    third = _pptx(tmp_path / "third.pptx")
    document = _batch([_job(first), {"schemaVersion": "1.0"}, _job(third)], strategy="continue")

    result = BatchService().execute(document, base_dir=tmp_path)

    validate_document(result, "batch-result.schema.json")
    assert result["status"] == "partial"
    assert result["exitCode"] == 30
    assert result["counts"] == {"succeeded": 2, "failed": 1, "skipped": 0, "reused": 0}
    assert [item["itemIndex"] for item in result["results"]] == [0, 1, 2]
    assert result["results"][1]["result"]["error"]["code"] == "INPUT_INVALID"
    assert result["results"][2]["result"]["status"] == "validated"


def test_fail_fast_marks_remaining_items_as_skipped(tmp_path: Path):
    source = _pptx(tmp_path / "later.pptx")
    document = _batch([{"schemaVersion": "1.0"}, _job(source)], strategy="fail-fast")

    result = BatchService().execute(document, base_dir=tmp_path)

    assert result["status"] == "failed"
    assert result["counts"] == {"succeeded": 0, "failed": 1, "skipped": 1, "reused": 0}
    assert result["results"][1]["status"] == "skipped"
    assert result["results"][1]["result"] is None


class _RetryService:
    def __init__(self) -> None:
        self.calls = 0

    def execute(self, document, *, base_dir, event_sink=None):
        self.calls += 1
        prepared = prepare_request(document, base_dir=base_dir)
        if self.calls == 1:
            return failed_result(
                ExportError(
                    "PowerPoint timed out",
                    stage="powerpoint-timeout",
                    details={"transient": True},
                ),
                prepared=prepared,
            )
        return validated_result(prepared)


def test_only_transient_failures_retry_and_every_attempt_is_recorded(tmp_path: Path):
    source = _pptx(tmp_path / "retry.pptx")
    service = _RetryService()
    sleeps = []
    result = BatchService(service, sleep=sleeps.append).execute(
        _batch([_job(source)], maxAttempts=3, retryDelayMs=25),
        base_dir=tmp_path,
    )

    item = result["results"][0]
    assert service.calls == 2
    assert sleeps == [0.025]
    assert item["attempts"] == 2
    assert [attempt["errorCode"] for attempt in item["attemptLog"]] == ["EXPORT_FAILED", None]
    assert item["result"]["status"] == "validated"
    assert is_retryable_result(failed_result(InputError("bad"))) is False
    assert is_retryable_result(failed_result(BudgetError("too large"))) is False


class _PublishingService:
    def __init__(self) -> None:
        self.calls = 0

    def execute(self, document, *, base_dir, event_sink=None):
        self.calls += 1
        prepared = prepare_request(document, base_dir=base_dir)
        output_root = prepared.output_root
        assert output_root is not None
        package = output_root / f"job-{self.calls}"
        package.mkdir(parents=True)
        artifact = package / "figure.svg"
        artifact.write_text(f"<svg><!--{self.calls}--></svg>", encoding="utf-8")
        report = package / "qa-report.json"
        report.write_text("{}", encoding="utf-8")
        manifest = {
            "schemaVersion": "1.0",
            "toolVersion": __version__,
            "pipelineRevision": prepared.raw.get("pipelineRevision", None),
            "jobId": f"job-{self.calls}",
            "source": {"name": prepared.source.name, "sha256": prepared.source_sha256, "slideCount": 1},
            "slides": [],
            "artifacts": [
                {
                    "kind": "svg",
                    "path": "figure.svg",
                    "sha256": sha256_file(artifact),
                    "bytes": artifact.stat().st_size,
                    "slide": 1,
                    "producer": "test",
                    "metadata": {},
                }
            ],
            "verdict": "PASS",
        }
        manifest["pipelineRevision"] = PIPELINE_REVISION
        manifest_path = package / "manifest.json"
        manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
        checksummed = [artifact, report, manifest_path]
        (package / "checksums.sha256").write_text(checksum_lines(checksummed, package), encoding="utf-8")
        result = {
            "schemaVersion": "1.0",
            "taskId": prepared.task_id,
            "status": "succeeded",
            "exitCode": 0,
            "toolVersion": __version__,
            "pipelineRevision": PIPELINE_REVISION,
            "configFingerprint": prepared.config_fingerprint,
            "effectiveSlides": list(prepared.effective_slides),
            "source": {"path": str(prepared.source), "sha256": prepared.source_sha256},
            "output": {"packagePath": str(package), "manifestPath": "manifest.json", "reportPath": "qa-report.json"},
            "jobId": f"job-{self.calls}",
            "verdict": "PASS",
            "artifacts": [
                {
                    "kind": "svg",
                    "relativePath": "figure.svg",
                    "sha256": sha256_file(artifact),
                    "bytes": artifact.stat().st_size,
                    "slide": 1,
                }
            ],
            "capabilities": dict(CAPABILITIES),
            "error": None,
        }
        validate_document(result, "export-result.schema.json")
        return result


def test_safe_reuse_checks_hashes_and_keeps_damaged_package(tmp_path: Path):
    source = _pptx(tmp_path / "reuse.pptx")
    job = _job(source, idempotencyKey="paper-figure-1", behavior={"dryRun": False})
    service = _PublishingService()
    runner = BatchService(service)

    first = runner.execute(_batch([job], reuseExisting=True), base_dir=tmp_path)
    second = runner.execute(_batch([job], reuseExisting=True), base_dir=tmp_path)

    assert service.calls == 1
    assert first["results"][0]["reused"] is False
    assert second["results"][0]["reused"] is True
    assert second["results"][0]["attempts"] == 0
    old_package = Path(first["results"][0]["result"]["output"]["packagePath"])
    (old_package / "figure.svg").write_text("damaged", encoding="utf-8")

    third = runner.execute(_batch([job], reuseExisting=True), base_dir=tmp_path)

    assert service.calls == 2
    assert third["results"][0]["reused"] is False
    new_package = Path(third["results"][0]["result"]["output"]["packagePath"])
    assert new_package != old_package
    assert old_package.exists()
    assert ".slideguard-rerun-" in str(new_package)


def test_safe_reuse_rejects_package_with_unlisted_file(tmp_path: Path):
    source = _pptx(tmp_path / "unlisted.pptx")
    job = _job(source, idempotencyKey="unlisted-file", behavior={"dryRun": False})
    service = _PublishingService()
    runner = BatchService(service)

    first = runner.execute(_batch([job], reuseExisting=True), base_dir=tmp_path)
    old_package = Path(first["results"][0]["result"]["output"]["packagePath"])
    (old_package / "not-in-checksums.bin").write_bytes(b"unexpected")
    second = runner.execute(_batch([job], reuseExisting=True), base_dir=tmp_path)

    assert service.calls == 2
    assert second["results"][0]["reused"] is False


def test_explicit_key_conflict_does_not_execute_second_job(tmp_path: Path):
    first_source = _pptx(tmp_path / "one.pptx")
    second_source = _pptx(tmp_path / "two.pptx")
    with zipfile.ZipFile(second_source, "a") as archive:
        archive.writestr("docProps/custom.xml", "<different/>")
    service = _PublishingService()
    runner = BatchService(service)
    first_job = _job(first_source, idempotencyKey="shared-key", behavior={"dryRun": False})
    second_job = _job(second_source, idempotencyKey="shared-key", behavior={"dryRun": False})

    runner.execute(_batch([first_job]), base_dir=tmp_path)
    result = runner.execute(_batch([second_job]), base_dir=tmp_path)

    assert service.calls == 1
    item = result["results"][0]
    assert item["result"]["error"]["code"] == "IDEMPOTENCY_CONFLICT"
    assert item["result"]["exitCode"] == 31


def test_batch_cli_emits_one_document_and_aggregate_exit(tmp_path: Path, capsys):
    source = _pptx(tmp_path / "cli.pptx")
    request_path = tmp_path / "batch.json"
    request_path.write_text(
        json.dumps(_batch([_job(source), {"schemaVersion": "1.0"}], strategy="continue")),
        encoding="utf-8",
    )

    exit_code = main(["batch", str(request_path)])
    captured = capsys.readouterr()
    result = json.loads(captured.out)

    assert exit_code == 30
    assert result["status"] == "partial"
    assert len(captured.out.strip().splitlines()) == 1
    assert captured.err == ""


def test_batch_envelope_rejects_more_than_one_hundred_jobs(tmp_path: Path):
    source = _pptx(tmp_path / "many.pptx")
    result = BatchService().execute(
        _batch([_job(source)] * 101),
        base_dir=tmp_path,
    )

    assert result["status"] == "failed"
    assert result["results"] == []
    assert result["error"]["code"] == "INPUT_INVALID"


def test_invalid_batch_behavior_is_a_contract_error(tmp_path: Path):
    source = _pptx(tmp_path / "bad-behavior.pptx")
    document = _batch([_job(source)])
    document["behavior"] = "continue"

    result = BatchService().execute(document, base_dir=tmp_path)

    validate_document(result, "batch-result.schema.json")
    assert result["exitCode"] == 30
    assert result["error"]["details"]["schema"] == "batch-request.schema.json"


def test_batch_argument_error_uses_batch_result_shape(capsys):
    exit_code = main(["batch", "--unknown"])
    result = json.loads(capsys.readouterr().out)

    assert exit_code == 30
    assert result["status"] == "failed"
    assert "counts" in result
    assert "capabilities" not in result
    validate_document(result, "batch-result.schema.json")
