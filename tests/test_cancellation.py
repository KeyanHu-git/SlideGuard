from __future__ import annotations

from pathlib import Path

import pytest

from slideguard.engine import _publish_package
from slideguard.errors import CancelledError


class CancelAfterCopy:
    def __init__(self) -> None:
        self.checks = 0

    def throw_if_cancelled(self) -> None:
        self.checks += 1
        if self.checks == 2:
            raise CancelledError("cancel after copy")


def test_cancel_between_staging_copy_and_atomic_publish_leaves_no_output(tmp_path: Path):
    package = tmp_path / "package"
    package.mkdir()
    (package / "manifest.json").write_text('{"jobId":"job"}', encoding="utf-8")
    output = tmp_path / "output"
    final = output / "job"

    with pytest.raises(CancelledError):
        _publish_package(package, output, final, "job", CancelAfterCopy())

    assert not final.exists()
    assert not list(output.glob(".sg-publish-*"))
