from __future__ import annotations

import hashlib
import importlib.util
import json
import sys
import tempfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "interruption_matrix.py"
SPEC = importlib.util.spec_from_file_location("slideguard_interruption_matrix", SCRIPT)
assert SPEC is not None and SPEC.loader is not None
matrix = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = matrix
SPEC.loader.exec_module(matrix)


def test_boundary_catalog_is_complete_and_has_stable_sequences():
    catalog = matrix.boundaries()

    assert len(catalog) == 12
    assert [item.sequence for item in catalog] == list(range(12))
    assert [item.label for item in catalog] == [
        "discover",
        "preflight",
        "inventory",
        "native-export:p1",
        "patch:p1",
        "validate:p1",
        "native-export:p2",
        "patch:p2",
        "validate:p2",
        "package",
        "publish:pending",
        "publish:complete",
    ]


def test_full_interruption_matrix_is_deterministic_and_fail_closed(tmp_path: Path):
    commit = "c" * 40
    with tempfile.TemporaryDirectory(prefix="sg-im-") as temporary:
        report = matrix.run_matrix(Path(temporary) / "w", commit_sha=commit)

    assert report["verdict"] == "PASS"
    assert report["seed"] == 20260904
    assert report["commitSha"] == commit
    assert report["coverage"] == {
        "checkpointBoundaries": 12,
        "faultKindsPerBoundary": 3,
        "publicationRenameMoments": ["before", "after"],
        "publicationRenameFaultKinds": ["cooperative-cancel", "process-terminated"],
        "compoundCorruptionCases": 1,
        "concurrentReadOnlyPlannerCases": 1,
        "cases": 42,
    }
    checkpoint_cases = [case for case in report["cases"] if "expectedCommittedSequence" in case]
    assert len(checkpoint_cases) == 36
    assert {
        case["faultKind"] for case in checkpoint_cases
    } == {"cooperative-cancel", "python-exception", "process-terminated"}
    assert all(case["finalDirectoryState"] == "absent" for case in checkpoint_cases)
    assert all(case["verdict"] == "PASS" for case in checkpoint_cases)
    assert all(case["resumePlanDeterministic"] is True for case in checkpoint_cases)
    assert all(case["resumePlanReadOnly"] is True for case in checkpoint_cases)
    assert all(case["resumeDecisionCorrect"] is True for case in checkpoint_cases)
    assert all(
        case["uncommittedStageArtifactIgnored"] is True
        for case in checkpoint_cases
        if case["faultKind"] != "process-terminated"
    )

    hard_cases = [case for case in checkpoint_cases if case["faultKind"] == "process-terminated"]
    assert len(hard_cases) == 12
    assert all(case["workerReturnCode"] != 0 for case in hard_cases)
    assert all(case["temporaryCheckpointIgnored"] is True for case in hard_cases)
    assert all(case["durableUncommittedTempFiles"] == 1 for case in hard_cases)

    publish_cases = [case for case in report["cases"] if "finalDirectoryStateAtInterrupt" in case]
    assert len(publish_cases) == 4
    assert {case["injectionPoint"] for case in publish_cases} == {
        "publish:before-atomic-rename",
        "publish:after-atomic-rename",
    }
    assert {case["faultKind"] for case in publish_cases} == {
        "cooperative-cancel",
        "process-terminated",
    }
    for case in publish_cases:
        expected_state = "absent" if "before" in case["injectionPoint"] else "complete"
        assert case["finalDirectoryStateAtInterrupt"] == expected_state
    terminated_publish = [case for case in publish_cases if case["faultKind"] == "process-terminated"]
    assert all(case["workerReturnCode"] != 0 for case in terminated_publish)
    assert all(case["recoveredPackageMatchesCleanRun"] is True for case in terminated_publish)
    assert all(case["publishedManifestVerdict"] == "PASS" for case in terminated_publish)

    compound = next(
        case for case in report["cases"]
        if case["faultKind"] == "checkpoint-and-artifact-corruption"
    )
    assert compound["resumePlanStatus"] == "rejected"
    assert compound["resumePlanReasonCode"] == "CHECKPOINT_READ_FAILED"
    assert compound["reusedThroughSequence"] is None

    concurrent = next(
        case for case in report["cases"]
        if case["faultKind"] == "concurrent-read-only-planners"
    )
    assert concurrent["readerCount"] == 2
    assert concurrent["plansByteIdentical"] is True
    assert concurrent["workspaceReadOnly"] is True

    environment = report["environment"]
    fingerprint_input = dict(environment)
    fingerprint = fingerprint_input.pop("fingerprint")
    expected = "sha256:" + hashlib.sha256(matrix.stable_json(fingerprint_input).encode("utf-8")).hexdigest()
    assert fingerprint == expected

    serialized = matrix.stable_json(report)
    assert json.loads(serialized) == report
    assert str(tmp_path) not in serialized
