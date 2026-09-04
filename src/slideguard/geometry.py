from __future__ import annotations

import math
from dataclasses import dataclass

from .errors import InputError


PercentBox = tuple[float, float, float, float]
PixelBox = tuple[int, int, int, int, int, int]
REFERENCE_PIXEL_TOLERANCE = 0.5


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


@dataclass(frozen=True, slots=True)
class FloatRect:
    """An axis-aligned rectangle in one explicitly named coordinate space."""

    left: float
    top: float
    right: float
    bottom: float

    def __post_init__(self) -> None:
        values = (self.left, self.top, self.right, self.bottom)
        if not all(math.isfinite(value) for value in values):
            raise InputError("Rectangle coordinates must be finite", stage="validation")
        if self.left >= self.right or self.top >= self.bottom:
            raise InputError("Rectangle must have positive width and height", stage="validation")

    @property
    def width(self) -> float:
        return self.right - self.left

    @property
    def height(self) -> float:
        return self.bottom - self.top

    @property
    def center(self) -> tuple[float, float]:
        return ((self.left + self.right) / 2.0, (self.top + self.bottom) / 2.0)


@dataclass(frozen=True, slots=True)
class ViewportTransform:
    """Pure mapping between reference pixels, fitted scene DIP and viewport DIP.

    ``scene`` is the image after aspect-preserving fit at zoom 1, with its
    top-left at (0, 0). ``viewport`` is a DIP rectangle and may be inset inside
    a widget. Zoom is around the fitted image centre; pan is a DIP translation.
    Device-pixel ratio is deliberately absent because input and layout geometry
    must stay in device-independent pixels.
    """

    reference_width: int
    reference_height: int
    viewport: FloatRect
    zoom: float = 1.0
    pan_x_dip: float = 0.0
    pan_y_dip: float = 0.0

    def __post_init__(self) -> None:
        if self.reference_width <= 0 or self.reference_height <= 0:
            raise InputError("Reference dimensions must be positive", stage="validation")
        values = (self.zoom, self.pan_x_dip, self.pan_y_dip)
        if not all(math.isfinite(value) for value in values) or self.zoom <= 0:
            raise InputError("Zoom must be positive and pan must be finite", stage="validation")

    @property
    def fit_scale(self) -> float:
        return min(
            self.viewport.width / self.reference_width,
            self.viewport.height / self.reference_height,
        )

    @property
    def scene_rect(self) -> FloatRect:
        return FloatRect(
            0.0,
            0.0,
            self.reference_width * self.fit_scale,
            self.reference_height * self.fit_scale,
        )

    @property
    def image_rect_in_viewport(self) -> FloatRect:
        scene = self.scene_rect
        viewport_cx, viewport_cy = self.viewport.center
        width = scene.width * self.zoom
        height = scene.height * self.zoom
        center_x = viewport_cx + self.pan_x_dip
        center_y = viewport_cy + self.pan_y_dip
        return FloatRect(
            center_x - width / 2.0,
            center_y - height / 2.0,
            center_x + width / 2.0,
            center_y + height / 2.0,
        )

    def normalized_to_reference(self, point: tuple[float, float]) -> tuple[float, float]:
        x, y = _finite_point(point)
        return x * self.reference_width, y * self.reference_height

    def reference_to_normalized(self, point: tuple[float, float]) -> tuple[float, float]:
        x, y = _finite_point(point)
        return x / self.reference_width, y / self.reference_height

    def reference_to_scene(self, point: tuple[float, float]) -> tuple[float, float]:
        x, y = _finite_point(point)
        return x * self.fit_scale, y * self.fit_scale

    def scene_to_reference(self, point: tuple[float, float]) -> tuple[float, float]:
        x, y = _finite_point(point)
        return x / self.fit_scale, y / self.fit_scale

    def scene_to_viewport(self, point: tuple[float, float]) -> tuple[float, float]:
        x, y = _finite_point(point)
        scene_cx, scene_cy = self.scene_rect.center
        viewport_cx, viewport_cy = self.viewport.center
        return (
            viewport_cx + self.pan_x_dip + (x - scene_cx) * self.zoom,
            viewport_cy + self.pan_y_dip + (y - scene_cy) * self.zoom,
        )

    def viewport_to_scene(self, point: tuple[float, float]) -> tuple[float, float]:
        x, y = _finite_point(point)
        scene_cx, scene_cy = self.scene_rect.center
        viewport_cx, viewport_cy = self.viewport.center
        return (
            scene_cx + (x - viewport_cx - self.pan_x_dip) / self.zoom,
            scene_cy + (y - viewport_cy - self.pan_y_dip) / self.zoom,
        )

    def normalized_to_viewport(self, point: tuple[float, float]) -> tuple[float, float]:
        return self.scene_to_viewport(self.reference_to_scene(self.normalized_to_reference(point)))

    def viewport_to_normalized(
        self,
        point: tuple[float, float],
        *,
        clamp: bool = False,
    ) -> tuple[float, float]:
        normalized = self.reference_to_normalized(self.scene_to_reference(self.viewport_to_scene(point)))
        if not clamp:
            return normalized
        return tuple(min(1.0, max(0.0, value)) for value in normalized)

    def normalized_rect_to_viewport(self, rect: NormalizedRect) -> FloatRect:
        left, top = self.normalized_to_viewport((rect.left, rect.top))
        right, bottom = self.normalized_to_viewport((rect.right, rect.bottom))
        return FloatRect(left, top, right, bottom)

    def viewport_rect_to_normalized(self, rect: FloatRect) -> NormalizedRect:
        left, top = self.viewport_to_normalized((rect.left, rect.top), clamp=True)
        right, bottom = self.viewport_to_normalized((rect.right, rect.bottom), clamp=True)
        return NormalizedRect(left, top, right, bottom)


@dataclass(frozen=True, slots=True)
class ReferencePixelRect:
    """One exclusive-edge crop rectangle in a reference raster."""

    left: int
    top: int
    right: int
    bottom: int
    reference_width: int
    reference_height: int

    def __post_init__(self) -> None:
        if self.reference_width <= 0 or self.reference_height <= 0:
            raise InputError("Reference dimensions must be positive", stage="validation")
        if not (
            0 <= self.left < self.right <= self.reference_width
            and 0 <= self.top < self.bottom <= self.reference_height
        ):
            raise InputError("Pixel crop must be inside the reference image", stage="validation")

    @classmethod
    def from_tuple(cls, value: PixelBox) -> "ReferencePixelRect":
        return cls(*value)

    def to_tuple(self) -> PixelBox:
        return (
            self.left,
            self.top,
            self.right,
            self.bottom,
            self.reference_width,
            self.reference_height,
        )

    def to_normalized(self) -> NormalizedRect:
        return NormalizedRect.from_pixels(
            self.left,
            self.top,
            self.right,
            self.bottom,
            self.reference_width,
            self.reference_height,
        )

    def to_png_box(self) -> tuple[int, int, int, int]:
        return self.left, self.top, self.right, self.bottom

    def to_pdf_box(self, page_width: float, page_height: float) -> list[float]:
        _positive_size(page_width, page_height, "PDF page")
        return [
            self.left * page_width / self.reference_width,
            page_height - self.bottom * page_height / self.reference_height,
            self.right * page_width / self.reference_width,
            page_height - self.top * page_height / self.reference_height,
        ]

    def to_svg_box(self, source_view_box: list[float]) -> list[float]:
        vx, vy, vw, vh = _view_box(source_view_box)
        return [
            vx + self.left * vw / self.reference_width,
            vy + self.top * vh / self.reference_height,
            (self.right - self.left) * vw / self.reference_width,
            (self.bottom - self.top) * vh / self.reference_height,
        ]


def normalized_from_pdf_box(box: list[float], page_width: float, page_height: float) -> NormalizedRect:
    """Convert a PDF y-up page box back to the canonical y-down rectangle."""
    _positive_size(page_width, page_height, "PDF page")
    if len(box) != 4 or not all(math.isfinite(float(value)) for value in box):
        raise InputError("PDF box needs four finite values", stage="validation")
    x0, y0, x1, y1 = (float(value) for value in box)
    return NormalizedRect(
        *(
            _clamp_unit_roundoff(value)
            for value in (
                x0 / page_width,
                1.0 - y1 / page_height,
                x1 / page_width,
                1.0 - y0 / page_height,
            )
        )
    )


def normalized_from_svg_box(box: list[float], source_view_box: list[float]) -> NormalizedRect:
    """Convert an SVG crop viewBox back relative to its original viewBox."""
    vx, vy, vw, vh = _view_box(source_view_box)
    if len(box) != 4 or not all(math.isfinite(float(value)) for value in box):
        raise InputError("SVG box needs four finite values", stage="validation")
    x, y, width, height = (float(value) for value in box)
    return NormalizedRect(
        *(
            _clamp_unit_roundoff(value)
            for value in (
                (x - vx) / vw,
                (y - vy) / vh,
                (x + width - vx) / vw,
                (y + height - vy) / vh,
            )
        )
    )


def _clamp_unit_roundoff(value: float, *, tolerance: float = 1e-12) -> float:
    """Clamp only arithmetic noise at the canonical 0..1 boundaries.

    Real out-of-range boxes must still be rejected by ``NormalizedRect``;
    this only absorbs the final ULP introduced by affine round trips.
    """
    if -tolerance <= value <= 0.0:
        return 0.0
    if 1.0 <= value <= 1.0 + tolerance:
        return 1.0
    return value


def _finite_point(point: tuple[float, float]) -> tuple[float, float]:
    if len(point) != 2:
        raise InputError("Point needs two coordinates", stage="validation")
    x, y = (float(value) for value in point)
    if not math.isfinite(x) or not math.isfinite(y):
        raise InputError("Point coordinates must be finite", stage="validation")
    return x, y


def _positive_size(width: float, height: float, label: str) -> tuple[float, float]:
    width = float(width)
    height = float(height)
    if not math.isfinite(width) or not math.isfinite(height) or width <= 0 or height <= 0:
        raise InputError(f"{label} dimensions must be positive and finite", stage="validation")
    return width, height


def _view_box(value: list[float]) -> tuple[float, float, float, float]:
    if len(value) != 4:
        raise InputError("SVG viewBox needs four values", stage="validation")
    vx, vy, vw, vh = (float(item) for item in value)
    if not all(math.isfinite(item) for item in (vx, vy, vw, vh)) or vw <= 0 or vh <= 0:
        raise InputError("SVG viewBox dimensions must be positive and finite", stage="validation")
    return vx, vy, vw, vh


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
