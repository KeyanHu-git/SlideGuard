import json
from pathlib import Path


def test_core_matrix_has_twelve_distinct_cases():
    path = Path(__file__).parents[1] / "fixtures" / "manifests" / "core-matrix.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    ids = [item["id"] for item in data["cases"]]
    assert len(ids) >= 12
    assert len(ids) == len(set(ids))
    assert all(item["failureCodes"] for item in data["cases"])


def test_powerpoint_worker_uses_slide_range_enum():
    worker = Path(__file__).parents[1] / "src" / "slideguard" / "resources" / "powerpoint_worker.ps1"
    content = worker.read_text(encoding="utf-8")
    assert "$printRange, 4" in content
    assert "$printRange, 3" not in content
