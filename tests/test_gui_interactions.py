from __future__ import annotations

import os
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

pytest.importorskip("PySide6")

from PySide6.QtCore import QEvent, Qt
from PySide6.QtGui import QImage, QKeyEvent, QPixmap
from PySide6.QtWidgets import QApplication

from slideguard.geometry import NormalizedRect
from slideguard.cancellation import CancellationToken
from slideguard.gui import CropCanvas, SlideGuardWindow
from slideguard.gui_state import CropPresetStore, CropSpec, GuiDraftStore


@pytest.fixture(scope="module")
def application():
    app = QApplication.instance() or QApplication([])
    yield app


def make_canvas(application) -> CropCanvas:
    canvas = CropCanvas()
    canvas._pixmap = QPixmap.fromImage(QImage(1600, 900, QImage.Format.Format_ARGB32))
    canvas.set_crop(NormalizedRect(0.1, 0.1, 0.9, 0.9))
    return canvas


def test_cancel_button_sets_thread_safe_token(application):
    window = SlideGuardWindow()
    window._export_running = True
    window._export_token = CancellationToken()
    window.cancel_button.setEnabled(True)

    window._cancel_export()

    assert window._export_token.is_cancelled is True
    assert window.cancel_button.isEnabled() is False
    assert "不会发布" in window.status.text()


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

    window.crop_preset_combo.setCurrentIndex(
        window.crop_preset_combo.findData("builtin:paper-safe")
    )
    window._apply_crop_preset()
    assert window._capture_editor_state().crop == CropSpec(
        "auto", (0.0, 0.0, 100.0, 100.0), (2.0, 2.0, 2.0, 2.0), 16
    )

    window._undo()
    assert window._capture_editor_state() == initial
    window._redo()
    assert window._capture_editor_state().crop == CropSpec(
        "auto", (0.0, 0.0, 100.0, 100.0), (2.0, 2.0, 2.0, 2.0), 16
    )
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


def test_custom_preset_is_loaded_and_applied_as_one_crop_spec(application, tmp_path):
    window = SlideGuardWindow()
    window._preset_store = CropPresetStore(tmp_path / "presets.json")
    expected = CropSpec("manual", (4.0, 3.0, 96.0, 97.0), (1.0, 2.0, 1.0, 2.0), 8)
    preset = window._preset_store.save("AAAI figure", expected)
    window._refresh_crop_presets(preset.preset_id)

    window._apply_crop_preset()

    assert window._capture_editor_state().crop == expected
    assert window.delete_preset_button.isEnabled()
    window.close()


def test_page_change_saves_crop_and_copy_uses_explicit_page_selection(application):
    window = SlideGuardWindow()
    window._source = Path("figure.pptx")
    window.slide_spin.setRange(1, 6)
    window.bound_spins["left"].setValue(7.0)

    window.slide_spin.setValue(2)
    assert window._capture_editor_state().bounds_percent[0] == 5.0
    window.bound_spins["left"].setValue(9.0)
    window.copy_pages_edit.setText("3,5-6")
    window._copy_crop_to_pages()

    window.slide_spin.setValue(3)
    assert window._capture_editor_state().bounds_percent[0] == 9.0
    window.slide_spin.setValue(1)
    assert window._capture_editor_state().bounds_percent[0] == 7.0
    window._source = None
    window.close()


def test_gui_request_crop_is_serialized_only_by_crop_spec(application):
    window = SlideGuardWindow()
    window._source = Path("figure.pptx")
    window.crop_preset_combo.setCurrentIndex(window.crop_preset_combo.findData("builtin:tight"))
    window._apply_crop_preset()

    crop_document = window._request_document()["crop"]

    assert crop_document == window._capture_crop_spec().to_request_document()
    assert "boundsPercent" not in crop_document
    window._source = None
    window.close()


def test_copied_page_crop_specs_are_written_to_the_gui_draft(application, tmp_path):
    source = tmp_path / "figure.pptx"
    source.write_bytes(b"test")
    digest = "e" * 64
    window = SlideGuardWindow()
    window._draft_store = GuiDraftStore(tmp_path / "drafts")
    window._source = source
    window._source_sha = digest
    window.slide_spin.setRange(1, 4)
    window.bound_spins["left"].setValue(8.0)
    window.copy_pages_edit.setText("2-3")
    window._copy_crop_to_pages()

    window._draft_timer.stop()
    window._save_draft()
    restored = window._draft_store.load(digest)

    assert restored is not None
    page_crops = dict(restored.page_crops)
    assert page_crops[1] == page_crops[2] == page_crops[3]
    window._source = None
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
