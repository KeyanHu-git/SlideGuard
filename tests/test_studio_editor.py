from pathlib import Path

import pytest
from PIL import Image, ImageDraw

from slideguard.crop import crop_pixels
from slideguard.geometry import FloatRect, ViewportTransform
from slideguard.studio.editor import CropEditor


def ready_editor():
    editor = CropEditor()
    editor.install_reference((400, 200, 3600, 1800, 4000, 2250))
    return editor


def test_default_never_invents_five_percent_crop():
    editor = CropEditor()
    assert editor.crop.mode == "auto"
    assert editor.crop.bounds_percent == (0, 0, 100, 100)
    with pytest.raises(ValueError):
        editor.request(Path("source.pptx"), 1, Path("out"))


@pytest.mark.parametrize("margin", [0, 1, 2, 5, 20, 100])
@pytest.mark.parametrize("manual", [False, True])
def test_exact_preview_export_box_agreement(tmp_path, margin, manual):
    path = tmp_path / "reference.png"
    image = Image.new("RGB", (800, 450), "white")
    ImageDraw.Draw(image).rectangle((50, 30, 699, 349), fill="black")
    image.save(path)
    editor = CropEditor()
    editor.install_reference(crop_pixels(path, padding_px=0, crop_percent=None, expand_percent=(0,) * 4))
    if manual:
        editor.mode("manual")
    editor.margin(-1, margin)
    assert editor.pixel_box == crop_pixels(path, padding_px=0,
        crop_percent=editor.crop.bounds_percent if manual else None, expand_percent=(margin,) * 4)


@pytest.mark.parametrize("handle", ["nw", "n", "ne", "e", "se", "s", "sw", "w"])
def test_gesture_one_undo_step(handle):
    editor = ready_editor()
    before = editor.state
    editor.begin()
    editor.resize(handle, .2, .3)
    editor.resize(handle, .3, .4)
    editor.end()
    editor.undo()
    assert editor.state == before
    editor.undo()  # A no-op, not an exception.
    assert editor.state == before


def test_cancel_gesture_and_independent_margin():
    editor = ready_editor()
    editor.margin(-1, 2)
    editor.margin(0, 5)
    assert editor.crop.expand_percent == (5, 2, 2, 2)
    before = editor.state
    editor.begin()
    editor.move(.1, .1)
    editor.end(cancel=True)
    assert editor.state == before


@pytest.mark.parametrize("value", [float("nan"), float("inf"), -1, 101])
def test_invalid_margin_is_transactional(value):
    editor = ready_editor()
    before = editor.state
    with pytest.raises(Exception):
        editor.margin(-1, value)
    assert editor.state == before


@pytest.mark.parametrize("zoom", [.15, 1, 2, 8, 32])
def test_view_transform_does_not_change_request(zoom):
    editor = ready_editor()
    before = editor.request(Path("source.pptx"), 1, Path("out"))
    transform = ViewportTransform(4000, 2250, FloatRect(0, 0, 900, 600), zoom, 37, -120)
    point = (.37, .62)
    assert transform.viewport_to_normalized(transform.normalized_to_viewport(point)) == pytest.approx(point)
    assert editor.request(Path("source.pptx"), 1, Path("out")) == before


def test_request_is_detached_and_uses_existing_schema():
    editor = ready_editor()
    request = editor.request(Path("s.pptx"), 2, Path("out"))
    assert "boundsPercent" not in request["crop"]
    editor.margin(-1, 5)
    assert request["crop"]["expandPercent"]["left"] == 0
    assert request["quality"]["pdfMaxBytes"] == 2_500_000
