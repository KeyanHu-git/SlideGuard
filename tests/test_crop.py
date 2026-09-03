from pathlib import Path

from PIL import Image

from slideguard.crop import crop_pixels, pdf_box, svg_box


def test_manual_crop_and_expansion(tmp_path: Path):
    reference = tmp_path / "reference.png"
    Image.new("RGB", (1000, 500), "white").save(reference)
    pixels = crop_pixels(
        reference, padding_px=0, crop_percent=(10, 20, 90, 80),
        expand_percent=(5, 10, 5, 10),
    )
    assert pixels == (60, 70, 940, 430, 1000, 500)
    assert pdf_box(pixels, 100, 50) == [6.0, 7.0, 94.0, 43.0]
    assert svg_box(pixels, [0, 0, 100, 50]) == [6.0, 7.0, 88.0, 36.0]


def test_auto_crop_keeps_fixed_pixel_padding(tmp_path: Path):
    reference = tmp_path / "reference.png"
    image = Image.new("RGB", (100, 80), "white")
    for x in range(30, 70):
        for y in range(20, 60):
            image.putpixel((x, y), (0, 0, 0))
    image.save(reference)
    assert crop_pixels(
        reference, padding_px=2, crop_percent=None,
        expand_percent=(0, 0, 0, 0),
    ) == (28, 18, 72, 62, 100, 80)
