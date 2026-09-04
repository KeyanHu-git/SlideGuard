from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

pytest.importorskip("PySide6")

from PySide6.QtCore import QPoint, QRectF
from PySide6.QtGui import QColor, QImage, QPainter, QPixmap
from PySide6.QtWidgets import QApplication

from slideguard.geometry import FloatRect, NormalizedRect, ViewportTransform
from slideguard.gui import CropCanvas


@pytest.fixture(scope="module")
def application():
    return QApplication.instance() or QApplication([])


@pytest.mark.parametrize("device_pixel_ratio", [1.0, 1.25, 1.5, 2.0])
def test_high_dpi_screenshot_checks_layout_but_dip_mapping_stays_canonical(
    application, tmp_path, device_pixel_ratio
):
    del application
    canvas = CropCanvas()
    canvas.resize(913, 617)
    preview = QImage(3_200, 900, QImage.Format.Format_ARGB32)
    preview.fill(QColor("white"))
    canvas._pixmap = QPixmap.fromImage(preview)
    canvas.set_crop(NormalizedRect(0.1, 0.2, 0.9, 0.8))

    available = QRectF(canvas.rect()).adjusted(16, 16, -16, -16)
    viewport = FloatRect(
        available.left(),
        available.top(),
        available.left() + available.width(),
        available.top() + available.height(),
    )
    transform = ViewportTransform(3_200, 900, viewport)
    actual = canvas._image_rect()
    expected = transform.image_rect_in_viewport
    assert actual.left() == pytest.approx(expected.left)
    assert actual.top() == pytest.approx(expected.top)
    assert actual.width() == pytest.approx(expected.width)
    assert actual.height() == pytest.approx(expected.height)

    physical_width = round(canvas.width() * device_pixel_ratio)
    physical_height = round(canvas.height() * device_pixel_ratio)
    screenshot = QImage(physical_width, physical_height, QImage.Format.Format_ARGB32)
    screenshot.setDevicePixelRatio(device_pixel_ratio)
    screenshot.fill(QColor("transparent"))
    painter = QPainter(screenshot)
    try:
        # PySide's QPainter overload requires the target offset explicitly.
        canvas.render(painter, QPoint())
    finally:
        # An active painter aborts the Qt process while Python formats a failure.
        painter.end()
    screenshot_path = tmp_path / f"layout-{device_pixel_ratio}.png"
    assert screenshot.save(str(screenshot_path))
    assert screenshot_path.stat().st_size > 1_000
    # The backing store has integer physical pixels, so a fractional DPR can
    # differ by at most half a physical pixel after rounding.
    dip_tolerance = 0.5 / device_pixel_ratio + 1e-9
    assert abs(screenshot.deviceIndependentSize().width() - canvas.width()) <= dip_tolerance
    assert abs(screenshot.deviceIndependentSize().height() - canvas.height()) <= dip_tolerance
    canvas.close()
