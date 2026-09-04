from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QEvent, Qt
from PySide6.QtGui import QImage, QKeyEvent, QPixmap
from PySide6.QtWidgets import QApplication
import pytest

from slideguard.geometry import NormalizedRect
from slideguard.gui import CropCanvas, SlideGuardWindow
from slideguard.gui_state import GuiDraftStore


@pytest.fixture(scope="module")
def application():
    app = QApplication.instance() or QApplication([])
    yield app


def make_canvas(application) -> CropCanvas:
    canvas = CropCanvas()
    canvas._pixmap = QPixmap.fromImage(QImage(1600, 900, QImage.Format.Format_ARGB32))
    canvas.set_crop(NormalizedRect(0.1, 0.1, 0.9, 0.9))
    return canvas


@pytest.mark.parametrize(
    ("key", "modifiers", "expected_dx", "expected_dy"),
    [
        (Qt.Key.Key_Right, Qt.KeyboardModifier.NoModifier, 1 / 4000, 0),
        (Qt.Key.Key_Down, Qt.KeyboardModifier.ShiftModifier, 0, 10 / 2250),
    ],
)
def test_canvas_keyboard_nudge_is_reference_pixel_based(
    application, key, modifiers, expected_dx, expected_dy
):
    canvas = make_canvas(application)
    events: list[str] = []
    canvas.edit_started.connect(lambda: events.append("start"))
    canvas.edit_finished.connect(lambda: events.append("finish"))
    before = canvas._crop

    canvas.keyPressEvent(QKeyEvent(QEvent.Type.KeyPress, key, modifiers))
    canvas.keyReleaseEvent(QKeyEvent(QEvent.Type.KeyRelease, key, modifiers))
    canvas.keyPressEvent(QKeyEvent(QEvent.Type.KeyPress, Qt.Key.Key_Return, Qt.KeyboardModifier.NoModifier))

    assert canvas._crop.left == pytest.approx(before.left + expected_dx)
    assert canvas._crop.top == pytest.approx(before.top + expected_dy)
    assert events == ["start", "finish"]


def test_escape_restores_keyboard_edit_origin(application):
    canvas = make_canvas(application)
    before = canvas._crop
    canvas.keyPressEvent(
        QKeyEvent(QEvent.Type.KeyPress, Qt.Key.Key_Right, Qt.KeyboardModifier.ShiftModifier)
    )
    assert canvas._crop != before

    canvas.keyPressEvent(
        QKeyEvent(QEvent.Type.KeyPress, Qt.Key.Key_Escape, Qt.KeyboardModifier.NoModifier)
    )

    assert canvas._crop == before


def test_keyboard_session_becomes_one_undo_step(application):
    window = SlideGuardWindow()
    window.canvas._pixmap = QPixmap.fromImage(QImage(1600, 900, QImage.Format.Format_ARGB32))
    before = window._capture_editor_state()

    for _ in range(3):
        window.canvas.keyPressEvent(
            QKeyEvent(QEvent.Type.KeyPress, Qt.Key.Key_Right, Qt.KeyboardModifier.NoModifier)
        )
    window.canvas.keyPressEvent(
        QKeyEvent(QEvent.Type.KeyPress, Qt.Key.Key_Return, Qt.KeyboardModifier.NoModifier)
    )
    assert window._capture_editor_state() != before

    window._undo()
    assert window._capture_editor_state() == before
    assert not window._history.can_undo
    window.close()


def test_window_presets_are_single_undoable_edits(application):
    window = SlideGuardWindow()
    initial = window._capture_editor_state()

    window.crop_preset_combo.setCurrentIndex(3)
    window._apply_crop_preset()
    assert window._capture_editor_state().bounds_percent == (10.0, 10.0, 90.0, 90.0)

    window._undo()
    assert window._capture_editor_state() == initial
    window._redo()
    assert window._capture_editor_state().bounds_percent == (10.0, 10.0, 90.0, 90.0)
    window.close()


def test_expansion_preset_keeps_crop_and_sets_all_four_edges(application):
    window = SlideGuardWindow()
    before = window._capture_editor_state().bounds_percent

    window.expand_preset_combo.setCurrentIndex(2)
    window._apply_expand_preset()

    state = window._capture_editor_state()
    assert state.bounds_percent == before
    assert state.expand_percent == (2.0, 2.0, 2.0, 2.0)
    window.close()


def test_normal_close_keeps_an_edited_draft_for_resume(application, tmp_path):
    source = tmp_path / "figure.pptx"
    source.write_bytes(b"test")
    digest = "c" * 64
    window = SlideGuardWindow()
    window._draft_store = GuiDraftStore(tmp_path / "drafts")
    window._source = source
    window._source_sha = digest
    window.bound_spins["left"].setValue(6.0)

    window.close()

    assert window._draft_store.load(digest) is not None
    window._draft_store.discard(digest)


def test_successful_export_removes_draft_and_pending_rewrite(application, tmp_path):
    source = tmp_path / "figure.pptx"
    source.write_bytes(b"test")
    digest = "d" * 64
    window = SlideGuardWindow()
    window._draft_store = GuiDraftStore(tmp_path / "drafts")
    window._source = source
    window._source_sha = digest
    window.bound_spins["left"].setValue(6.0)
    window._draft_timer.stop()
    window._save_draft()
    assert window._draft_store.load(digest) is not None

    window._export_running = True
    window._export_finished(
        {
            "status": "succeeded",
            "output": {"packagePath": str(tmp_path / "package")},
            "verdict": "PASS",
        }
    )

    assert window._draft_store.load(digest) is None
    assert not window._draft_timer.isActive()
    window.close()
