from __future__ import annotations

import os
from pathlib import Path

import pytest


os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PySide6")

from PySide6.QtCore import QCoreApplication

import slideguard.gui as gui


def test_desktop_worker_returns_the_application_resume_plan_without_translation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    _app = QCoreApplication.instance() or QCoreApplication([])
    expected = {
        "schemaVersion": "1.0",
        "kind": "slideguard-resume-plan",
        "status": "resumable",
        "planKey": "sha256:" + "a" * 64,
    }
    calls = []

    class Service:
        def execute(self, document, *, base_dir, workspace_path):
            calls.append((document, base_dir, workspace_path))
            return expected

    monkeypatch.setattr(gui, "ResumePlanningService", Service)
    document = {"schemaVersion": "1.0", "input": "paper.pptx"}
    worker = gui.ResumePlanWorker(document, tmp_path, tmp_path / "workspace")
    results = []
    worker.finished.connect(results.append)

    worker.run()

    assert results == [expected]
    assert calls == [(document, tmp_path, tmp_path / "workspace")]
