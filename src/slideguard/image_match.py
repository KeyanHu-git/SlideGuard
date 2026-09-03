from __future__ import annotations

import io

import numpy as np
from PIL import Image


def crop_native(raw: bytes, crop: tuple[int, int, int, int]) -> Image.Image:
    image = Image.open(io.BytesIO(raw)).convert("RGBA")
    left, top, right, bottom = crop
    box = (
        round(image.width * left / 100000),
        round(image.height * top / 100000),
        round(image.width * (1 - right / 100000)),
        round(image.height * (1 - bottom / 100000)),
    )
    return image.crop(box)


def resize_limit(image: Image.Image, max_dimension: int | None) -> Image.Image:
    if not max_dimension or max(image.size) <= max_dimension:
        return image
    ratio = max_dimension / max(image.size)
    size = (max(1, round(image.width * ratio)), max(1, round(image.height * ratio)))
    return image.resize(size, Image.Resampling.LANCZOS)


def premultiplied_rgb(image: Image.Image, size: tuple[int, int]) -> np.ndarray:
    rgba = np.asarray(image.convert("RGBA").resize(size, Image.Resampling.LANCZOS), dtype=np.float32)
    return rgba[:, :, :3] * (rgba[:, :, 3:4] / 255.0)


def correlation(first: np.ndarray, second: np.ndarray) -> float:
    first = first.reshape(-1).astype(np.float64)
    second = second.reshape(-1).astype(np.float64)
    first -= first.mean()
    second -= second.mean()
    denominator = np.linalg.norm(first) * np.linalg.norm(second)
    return float(first.dot(second) / denominator) if denominator else -1.0


def best_candidate(
    target: Image.Image,
    candidates: list[tuple[str, tuple[int, int, int, int], bytes]],
) -> tuple[float, str, tuple[int, int, int, int], bytes] | None:
    if not candidates:
        return None
    target_array = premultiplied_rgb(target, target.size)
    scored = []
    for name, crop, raw in candidates:
        source = crop_native(raw, crop)
        scored.append((correlation(target_array, premultiplied_rgb(source, target.size)), name, crop, raw))
    return max(scored, key=lambda item: item[0])

