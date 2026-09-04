from __future__ import annotations

import math
from dataclasses import dataclass

from .errors import InputError


PercentBox = tuple[float, float, float, float]
PixelBox = tuple[int, int, int, int, int, int]


def canonical_float(value: float | int) -> float:
    number = float(value)
    if not math.isfinite(number):
        raise InputError("Numeric values must be finite", stage="validation")
    return 0.0 if number == 0 else number


@dataclass(frozen=True, slots=True)
class NormalizedRect:
    """A slide rectangle in stable 0..1 coordinates with exclusive right/bottom."""

    left: float
    top: float
    right: float
    bottom: float

    def __post_init__(self) -> None:
        values = (self.left, self.top, self.right, self.bottom)
        if not all(math.isfinite(value) for value in values):
            raise InputError("Crop coordinates must be finite", stage="validation")
        if not (0 <= self.left < self.right <= 1 and 0 <= self.top < self.bottom <= 1):
            raise InputError(
                "Crop rectangle must satisfy 0 <= left < right <= 1 and 0 <= top < bottom <= 1",
                stage="validation",
            )

    @classmethod
    def from_percent(cls, value: PercentBox) -> "NormalizedRect":
        return cls(*(canonical_float(item) / 100.0 for item in value))

    @classmethod
    def from_pixels(
        cls,
        left: int,
        top: int,
        right: int,
        bottom: int,
        width: int,
        height: int,
    ) -> "NormalizedRect":
        if width <= 0 or height <= 0:
            raise InputError("Reference dimensions must be positive", stage="validation")
        return cls(left / width, top / height, right / width, bottom / height)

    def to_percent(self) -> PercentBox:
        return tuple(value * 100.0 for value in (self.left, self.top, self.right, self.bottom))

    def to_pixels(self, width: int, height: int) -> tuple[int, int, int, int]:
        if width <= 0 or height <= 0:
            raise InputError("Reference dimensions must be positive", stage="validation")
        return (
            round(width * self.left),
            round(height * self.top),
            round(width * self.right),
            round(height * self.bottom),
        )


def validate_expansion_percent(value: PercentBox) -> None:
    if len(value) != 4 or not all(math.isfinite(item) and 0 <= item <= 100 for item in value):
        raise InputError("Expansion needs four finite values between 0 and 100", stage="validation")


def effective_pixel_box(
    rect: NormalizedRect,
    width: int,
    height: int,
    *,
    expand_percent: PercentBox,
    padding_px: int,
) -> PixelBox:
    """Apply per-edge expansion relative to the crop size, then fixed padding."""
    validate_expansion_percent(expand_percent)
    if padding_px < 0:
        raise InputError("padding-px cannot be negative", stage="validation")
    left, top, right, bottom = rect.to_pixels(width, height)
    crop_width = max(1, right - left)
    crop_height = max(1, bottom - top)
    extra_left = round(crop_width * expand_percent[0] / 100) + padding_px
    extra_top = round(crop_height * expand_percent[1] / 100) + padding_px
    extra_right = round(crop_width * expand_percent[2] / 100) + padding_px
    extra_bottom = round(crop_height * expand_percent[3] / 100) + padding_px
    return (
        max(0, left - extra_left),
        max(0, top - extra_top),
        min(width, right + extra_right),
        min(height, bottom + extra_bottom),
        width,
        height,
    )


def resize_normalized_rect(
    rect: NormalizedRect,
    handle: str,
    x: float,
    y: float,
    *,
    reference_width: int,
    reference_height: int,
    minimum_pixels: int = 4,
) -> NormalizedRect:
    """Resize one or two edges and always return a page-clamped valid rectangle."""
    if handle not in {"nw", "n", "ne", "e", "se", "s", "sw", "w"}:
        raise InputError(f"Unknown crop handle: {handle}", stage="validation")
    if reference_width <= 0 or reference_height <= 0 or minimum_pixels <= 0:
        raise InputError("Reference dimensions and minimum size must be positive", stage="validation")
    x = min(1.0, max(0.0, canonical_float(x)))
    y = min(1.0, max(0.0, canonical_float(y)))
    min_x = min(1.0, minimum_pixels / reference_width)
    min_y = min(1.0, minimum_pixels / reference_height)
    left, top, right, bottom = rect.left, rect.top, rect.right, rect.bottom

    if handle in {"nw", "w", "sw"}:
        if right < min_x:
            right = min_x
        left = min(x, right - min_x)
    if handle in {"ne", "e", "se"}:
        if left > 1.0 - min_x:
            left = 1.0 - min_x
        right = max(x, left + min_x)
    if handle in {"nw", "n", "ne"}:
        if bottom < min_y:
            bottom = min_y
        top = min(y, bottom - min_y)
    if handle in {"sw", "s", "se"}:
        if top > 1.0 - min_y:
            top = 1.0 - min_y
        bottom = max(y, top + min_y)
    return NormalizedRect(left, top, right, bottom)


def move_normalized_rect(rect: NormalizedRect, dx: float, dy: float) -> NormalizedRect:
    width = rect.right - rect.left
    height = rect.bottom - rect.top
    left = min(1.0 - width, max(0.0, rect.left + canonical_float(dx)))
    top = min(1.0 - height, max(0.0, rect.top + canonical_float(dy)))
    return NormalizedRect(left, top, left + width, top + height)
