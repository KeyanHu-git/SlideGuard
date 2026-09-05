from __future__ import annotations

from pathlib import Path

import numpy as np
from PIL import Image

from .errors import InputError
from .geometry import NormalizedRect, ReferencePixelRect, effective_pixel_box, validate_expansion_percent


PercentBox = tuple[float, float, float, float]


def validate_crop_percent(value: PercentBox | None) -> None:
    if value is None:
        return
    left, top, right, bottom = value
    if not (0 <= left < right <= 100 and 0 <= top < bottom <= 100):
        raise InputError("crop-percent must satisfy 0 <= left < right <= 100 and 0 <= top < bottom <= 100")


def validate_expand_percent(value: PercentBox) -> None:
    validate_expansion_percent(value)


def crop_pixels(
    reference_png: Path,
    *,
    padding_px: int,
    crop_percent: PercentBox | None,
    expand_percent: PercentBox,
) -> tuple[int, int, int, int, int, int]:
    validate_crop_percent(crop_percent)
    validate_expand_percent(expand_percent)
    if padding_px < 0:
        raise InputError("padding-px cannot be negative")
    image = np.asarray(Image.open(reference_png).convert("RGB"), dtype=np.int16)
    height, width = image.shape[:2]
    if crop_percent is not None:
        return effective_pixel_box(
            NormalizedRect.from_percent(crop_percent),
            width,
            height,
            expand_percent=expand_percent,
            padding_px=padding_px,
        )
    else:
        corners = np.array([image[0, 0], image[0, -1], image[-1, 0], image[-1, -1]], dtype=np.int16)
        background = np.median(corners, axis=0)
        foreground = np.max(np.abs(image - background), axis=2) > 2
        ys, xs = np.where(foreground)
        if not len(xs):
            left, top, right, bottom = 0, 0, width, height
        else:
            left, top, right, bottom = int(xs.min()), int(ys.min()), int(xs.max()) + 1, int(ys.max()) + 1
    box_width = max(1, right - left)
    box_height = max(1, bottom - top)
    extra_left = round(box_width * expand_percent[0] / 100) + padding_px
    extra_top = round(box_height * expand_percent[1] / 100) + padding_px
    extra_right = round(box_width * expand_percent[2] / 100) + padding_px
    extra_bottom = round(box_height * expand_percent[3] / 100) + padding_px
    return (
        max(0, left - extra_left),
        max(0, top - extra_top),
        min(width, right + extra_right),
        min(height, bottom + extra_bottom),
        width,
        height,
    )


def pdf_box(pixel_box: tuple[int, int, int, int, int, int], page_width: float, page_height: float) -> list[float]:
    return ReferencePixelRect.from_tuple(pixel_box).to_pdf_box(page_width, page_height)


def svg_box(pixel_box: tuple[int, int, int, int, int, int], view_box: list[float]) -> list[float]:
    return ReferencePixelRect.from_tuple(pixel_box).to_svg_box(view_box)
