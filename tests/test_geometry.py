from __future__ import annotations

import math
import random

import pytest

from slideguard.errors import InputError
from slideguard.geometry import NormalizedRect, effective_pixel_box, move_normalized_rect, resize_normalized_rect


def test_normalized_rect_rejects_non_finite_and_crossed_edges():
    for values in (
        (0, 0, math.nan, 1),
        (0, 0, math.inf, 1),
        (0.8, 0, 0.2, 1),
        (-0.1, 0, 1, 1),
    ):
        with pytest.raises(InputError):
            NormalizedRect(*values)


def test_normalized_pixel_round_trip_is_exact_for_pixel_derived_rectangles():
    generator = random.Random(20260904)
    for _ in range(500):
        width = generator.randint(320, 8000)
        height = generator.randint(240, 5000)
        left = generator.randint(0, width - 2)
        right = generator.randint(left + 1, width)
        top = generator.randint(0, height - 2)
        bottom = generator.randint(top + 1, height)
        rect = NormalizedRect.from_pixels(left, top, right, bottom, width, height)
        assert rect.to_pixels(width, height) == (left, top, right, bottom)


def test_effective_box_expands_each_edge_relative_to_crop_dimensions_and_clamps():
    rect = NormalizedRect(0.1, 0.2, 0.9, 0.8)
    assert effective_pixel_box(
        rect, 1000, 500,
        expand_percent=(5, 10, 5, 10),
        padding_px=0,
    ) == (60, 70, 940, 430, 1000, 500)

    assert effective_pixel_box(
        NormalizedRect(0.01, 0.01, 0.99, 0.99), 100, 100,
        expand_percent=(20, 20, 20, 20),
        padding_px=10,
    ) == (0, 0, 100, 100, 100, 100)


@pytest.mark.parametrize("handle", ["nw", "n", "ne", "e", "se", "s", "sw", "w"])
@pytest.mark.parametrize("point", [(0.0, 0.0), (1.0, 1.0), (-1.0, 2.0)])
def test_resize_reducer_keeps_all_handles_valid_at_page_edges(handle, point):
    narrow = NormalizedRect(0.99998, 0.9998, 0.99999, 0.9999)
    actual = resize_normalized_rect(
        narrow, handle, point[0], point[1],
        reference_width=4000, reference_height=2250, minimum_pixels=4,
    )
    assert 0 <= actual.left < actual.right <= 1
    assert 0 <= actual.top < actual.bottom <= 1
    if handle in {"nw", "w", "sw", "ne", "e", "se"}:
        assert actual.right - actual.left + 1e-12 >= 4 / 4000
    if handle in {"nw", "n", "ne", "sw", "s", "se"}:
        assert actual.bottom - actual.top + 1e-12 >= 4 / 2250


def test_move_reducer_preserves_size_and_clamps_to_page():
    rect = NormalizedRect(0.2, 0.3, 0.6, 0.8)
    moved = move_normalized_rect(rect, 2.0, -2.0)
    assert moved.left == pytest.approx(0.6)
    assert moved.top == 0.0
    assert moved.right == 1.0
    assert moved.bottom == 0.5
