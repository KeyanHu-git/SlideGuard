import threading
import time

import pytest
from PIL import Image

from slideguard import render
from slideguard.render import _remove_chromium_workdir, _svg_pixel_size, _wait_for_chromium_screenshot


def test_svg_pixel_size_preserves_fractional_aspect_ratio(tmp_path):
    svg = tmp_path / "sample.svg"
    svg.write_text(
        '<svg xmlns="http://www.w3.org/2000/svg" width="852.24" height="412.08" '
        'viewBox="0.24 2.88 852.24 412.08"/>',
        encoding="utf-8",
    )
    assert _svg_pixel_size(svg, 1600) == (1600, 774)


def test_chromium_screenshot_waits_for_a_detached_child_to_finish_png(tmp_path):
    screenshot = tmp_path / "delayed.png"

    def delayed_writer() -> None:
        time.sleep(0.03)
        Image.new("RGBA", (640, 320), (0, 0, 0, 0)).save(screenshot)

    worker = threading.Thread(target=delayed_writer)
    worker.start()
    try:
        _wait_for_chromium_screenshot(
            screenshot,
            (640, 320),
            timeout=1.0,
            poll_interval=0.005,
        )
    finally:
        worker.join(timeout=1)


def test_chromium_screenshot_timeout_remains_a_hard_failure(tmp_path):
    with pytest.raises(RuntimeError, match="complete SVG screenshot"):
        _wait_for_chromium_screenshot(
            tmp_path / "never-created.png",
            (640, 320),
            timeout=0.01,
            poll_interval=0.001,
        )


def test_chromium_screenshot_rejects_a_completed_wrong_size(tmp_path):
    screenshot = tmp_path / "wrong-size.png"
    Image.new("RGBA", (320, 160), (0, 0, 0, 0)).save(screenshot)

    with pytest.raises(RuntimeError, match="size mismatch"):
        _wait_for_chromium_screenshot(screenshot, (640, 320), timeout=0.01)


def test_chromium_workdir_cleanup_retries_windows_file_locks(tmp_path, monkeypatch):
    work = tmp_path / "profile-root"
    work.mkdir()
    attempts = 0

    def transient_lock(path):
        nonlocal attempts
        attempts += 1
        if attempts < 3:
            raise PermissionError("browser still owns a cache file")
        path.rmdir()

    monkeypatch.setattr(render.shutil, "rmtree", transient_lock)
    _remove_chromium_workdir(work, timeout=1.0, poll_interval=0.001)
    assert attempts == 3
    assert not work.exists()
