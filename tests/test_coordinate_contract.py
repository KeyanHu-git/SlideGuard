from __future__ import annotations

import hashlib
import random
from pathlib import Path

import pytest
from PIL import Image
from pypdf import PdfReader, PdfWriter
from pypdf.generic import DecodedStreamObject, NameObject

from slideguard.crop import crop_pixels
from slideguard.errors import InputError
from slideguard.geometry import (
    REFERENCE_PIXEL_TOLERANCE,
    FloatRect,
    NormalizedRect,
    ReferencePixelRect,
    ViewportTransform,
    effective_pixel_box,
    normalized_from_pdf_box,
    normalized_from_svg_box,
)
from slideguard.model import FeatureInventory, Verdict
from slideguard.pdf_pipeline import restore_pdf_images
from slideguard.qa import validate_pdf_structure, validate_svg_vector_invariant
from slideguard.svg_pipeline import restore_svg_images


SEED = 20260904


class NoMediaPackage:
    def media_candidates(self, _slide: int) -> list[object]:
        return []


def _edge_error_in_reference_pixels(
    expected: NormalizedRect,
    actual: NormalizedRect,
    width: int,
    height: int,
) -> float:
    return max(
        abs(expected.left - actual.left) * width,
        abs(expected.right - actual.right) * width,
        abs(expected.top - actual.top) * height,
        abs(expected.bottom - actual.bottom) * height,
    )


def test_random_coordinate_spaces_round_trip_within_half_reference_pixel():
    generator = random.Random(SEED)
    worst_error = 0.0
    for _ in range(2_000):
        reference_width = generator.randint(32, 20_000)
        reference_height = generator.randint(32, 20_000)
        viewport_width = generator.uniform(120.0, 5_000.0)
        viewport_height = generator.uniform(90.0, 3_500.0)
        viewport_left = generator.uniform(-200.0, 200.0)
        viewport_top = generator.uniform(-200.0, 200.0)
        viewport = FloatRect(
            viewport_left,
            viewport_top,
            viewport_left + viewport_width,
            viewport_top + viewport_height,
        )
        transform = ViewportTransform(
            reference_width,
            reference_height,
            viewport,
            zoom=10 ** generator.uniform(-1.0, 1.2),
            pan_x_dip=generator.uniform(-2.0, 2.0) * viewport_width,
            pan_y_dip=generator.uniform(-2.0, 2.0) * viewport_height,
        )
        left = generator.randint(0, reference_width - 2)
        right = generator.randint(left + 1, reference_width)
        top = generator.randint(0, reference_height - 2)
        bottom = generator.randint(top + 1, reference_height)
        expected = NormalizedRect.from_pixels(
            left, top, right, bottom, reference_width, reference_height
        )

        viewport_rect = transform.normalized_rect_to_viewport(expected)
        actual = transform.viewport_rect_to_normalized(viewport_rect)
        error = _edge_error_in_reference_pixels(
            expected, actual, reference_width, reference_height
        )
        worst_error = max(worst_error, error)

        reference_point = (generator.uniform(0, reference_width), generator.uniform(0, reference_height))
        scene_point = transform.reference_to_scene(reference_point)
        viewport_point = transform.scene_to_viewport(scene_point)
        recovered = transform.scene_to_reference(transform.viewport_to_scene(viewport_point))
        point_error = max(
            abs(reference_point[0] - recovered[0]),
            abs(reference_point[1] - recovered[1]),
        )
        worst_error = max(worst_error, point_error)

    assert worst_error <= REFERENCE_PIXEL_TOLERANCE


def test_random_crops_project_to_the_same_pdf_svg_and_png_region():
    generator = random.Random(SEED)
    worst_error = 0.0
    for _ in range(2_000):
        width = generator.randint(32, 20_000)
        height = generator.randint(32, 20_000)
        left = generator.randint(0, width - 2)
        right = generator.randint(left + 1, width)
        top = generator.randint(0, height - 2)
        bottom = generator.randint(top + 1, height)
        crop = effective_pixel_box(
            NormalizedRect.from_pixels(left, top, right, bottom, width, height),
            width,
            height,
            expand_percent=tuple(generator.uniform(0.0, 20.0) for _ in range(4)),
            padding_px=generator.randint(0, 64),
        )
        projection = ReferencePixelRect.from_tuple(crop)
        expected = projection.to_normalized()
        page_width = generator.uniform(10.0, 2_000.0)
        page_height = generator.uniform(10.0, 2_000.0)
        source_view_box = [
            generator.uniform(-1_000.0, 1_000.0),
            generator.uniform(-1_000.0, 1_000.0),
            generator.uniform(10.0, 5_000.0),
            generator.uniform(10.0, 5_000.0),
        ]

        pdf_region = normalized_from_pdf_box(
            projection.to_pdf_box(page_width, page_height), page_width, page_height
        )
        svg_region = normalized_from_svg_box(
            projection.to_svg_box(source_view_box), source_view_box
        )
        png_region = NormalizedRect.from_pixels(
            *projection.to_png_box(), width, height
        )
        for actual in (pdf_region, svg_region, png_region):
            worst_error = max(
                worst_error,
                _edge_error_in_reference_pixels(expected, actual, width, height),
            )

    assert worst_error <= REFERENCE_PIXEL_TOLERANCE


def _write_vector_pdf(path: Path, width: float, height: float, content: bytes) -> None:
    writer = PdfWriter()
    page = writer.add_blank_page(width=width, height=height)
    stream = DecodedStreamObject()
    stream.set_data(content)
    page[NameObject("/Contents")] = writer._add_object(stream)
    writer.write(str(path))


def test_production_pipelines_share_crop_and_preserve_vector_invariants(tmp_path: Path):
    reference = tmp_path / "reference.png"
    Image.new("RGB", (1_600, 800), "white").save(reference)
    pixels = crop_pixels(
        reference,
        padding_px=7,
        crop_percent=(7.5, 12.5, 91.25, 86.25),
        expand_percent=(2.0, 3.0, 4.0, 5.0),
    )
    projection = ReferencePixelRect.from_tuple(pixels)

    native_pdf = tmp_path / "native.pdf"
    final_pdf = tmp_path / "final.pdf"
    pdf_content = b"q 2 w [6 4] 0 d 80 60 m 720 340 l S Q"
    _write_vector_pdf(native_pdf, 800.0, 400.0, pdf_content)
    pdf_result = restore_pdf_images(
        native_pdf,
        NoMediaPackage(),
        1,
        reference,
        final_pdf,
        max_dimension=None,
        jpeg_quality=95,
        max_bytes=None,
        padding_px=7,
        crop_percent=(7.5, 12.5, 91.25, 86.25),
        expand_percent=(2.0, 3.0, 4.0, 5.0),
    )

    native_svg = tmp_path / "native.svg"
    final_svg = tmp_path / "final.svg"
    source_view_box = [-10.0, 20.0, 800.0, 400.0]
    native_svg.write_text(
        """<svg xmlns="http://www.w3.org/2000/svg" viewBox="-10 20 800 400" width="800" height="400">
<defs><filter id="shadow"><feGaussianBlur stdDeviation="2"/></filter></defs>
<rect x="-10" y="20" width="800" height="400" fill="white"/>
<path id="dash" d="M 80 80 L 700 330" fill="none" stroke="#000" stroke-dasharray="6 4" filter="url(#shadow)"/>
</svg>""",
        encoding="utf-8",
    )
    svg_result = restore_svg_images(
        native_svg,
        NoMediaPackage(),
        1,
        reference,
        final_svg,
        padding_px=7,
        crop_percent=(7.5, 12.5, 91.25, 86.25),
        expand_percent=(2.0, 3.0, 4.0, 5.0),
    )

    expected = projection.to_normalized()
    pdf_region = normalized_from_pdf_box(pdf_result.crop_box, 800.0, 400.0)
    svg_region = normalized_from_svg_box(svg_result.view_box, source_view_box)
    png_crop = Image.open(reference).crop(projection.to_png_box())
    png_region = NormalizedRect.from_pixels(
        *projection.to_png_box(), 1_600, 800
    )
    for region in (pdf_region, svg_region, png_region):
        assert _edge_error_in_reference_pixels(expected, region, 1_600, 800) <= REFERENCE_PIXEL_TOLERANCE
    assert png_crop.size == (projection.right - projection.left, projection.bottom - projection.top)

    native_stream = PdfReader(str(native_pdf)).pages[0].get_contents().get_data()
    final_stream = PdfReader(str(final_pdf)).pages[0].get_contents().get_data()
    assert final_stream == native_stream == pdf_content
    assert pdf_result.content_hash == hashlib.sha256(pdf_content).hexdigest()

    inventory = FeatureInventory(
        slide=1,
        slide_part="ppt/slides/slide1.xml",
        line_count=1,
        dashed_line_count=1,
    )
    assert all(
        finding.status is Verdict.PASS
        for finding in validate_pdf_structure(final_pdf, native_pdf, inventory, pdf_result.crop_box)
    )
    assert all(
        finding.status is Verdict.PASS
        for finding in validate_svg_vector_invariant(final_svg, native_svg, inventory)
    )
    final_text = final_svg.read_text(encoding="utf-8")
    assert 'stroke-dasharray="6 4"' in final_text
    assert 'filter="url(#shadow)"' in final_text


@pytest.mark.parametrize(
    "bad_transform",
    [
        lambda: ViewportTransform(0, 100, FloatRect(0, 0, 100, 100)),
        lambda: ViewportTransform(100, 100, FloatRect(0, 0, 100, 100), zoom=0),
        lambda: ViewportTransform(100, 100, FloatRect(0, 0, 100, 100), pan_x_dip=float("nan")),
    ],
)
def test_coordinate_transform_rejects_invalid_geometry(bad_transform):
    with pytest.raises(InputError):
        bad_transform()
