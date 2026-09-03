from __future__ import annotations

import hashlib
import io
import zlib
from dataclasses import dataclass
from pathlib import Path

from PIL import Image
from pypdf import PdfReader, PdfWriter
from pypdf.generic import NameObject, NumberObject, RectangleObject

from .errors import BudgetError, FidelityError
from .crop import PercentBox, crop_pixels, pdf_box
from .image_match import best_candidate, crop_native, resize_limit
from .ooxml import PptxPackage


@dataclass(slots=True)
class PdfPatchResult:
    content_hash: str
    patched_images: int
    upgraded_masks: int
    unmatched_images: int
    crop_box: list[float]
    output_bytes: int


def _set_rgb(stream, image: Image.Image, jpeg_quality: int, lossless: bool) -> None:
    rgb = image.convert("RGB")
    if lossless:
        stream._data = zlib.compress(rgb.tobytes(), 9)
        stream[NameObject("/Filter")] = NameObject("/FlateDecode")
    else:
        buffer = io.BytesIO()
        rgb.save(buffer, format="JPEG", quality=jpeg_quality, optimize=True, progressive=False, subsampling=0)
        stream._data = buffer.getvalue()
        stream[NameObject("/Filter")] = NameObject("/DCTDecode")
    stream.pop(NameObject("/DecodeParms"), None)
    stream[NameObject("/Width")] = NumberObject(rgb.width)
    stream[NameObject("/Height")] = NumberObject(rgb.height)
    stream[NameObject("/ColorSpace")] = NameObject("/DeviceRGB")
    stream[NameObject("/BitsPerComponent")] = NumberObject(8)


def _set_gray(stream, alpha: Image.Image) -> None:
    gray = alpha.convert("L")
    stream._data = zlib.compress(gray.tobytes(), 9)
    stream[NameObject("/Filter")] = NameObject("/FlateDecode")
    stream.pop(NameObject("/DecodeParms"), None)
    stream[NameObject("/Width")] = NumberObject(gray.width)
    stream[NameObject("/Height")] = NumberObject(gray.height)
    stream[NameObject("/ColorSpace")] = NameObject("/DeviceGray")
    stream[NameObject("/BitsPerComponent")] = NumberObject(8)


def restore_pdf_images(
    native_pdf: Path,
    pptx: PptxPackage,
    slide: int,
    reference_png: Path,
    output_pdf: Path,
    *,
    max_dimension: int | None,
    jpeg_quality: int,
    max_bytes: int | None,
    padding_px: int,
    crop_percent: PercentBox | None,
    expand_percent: PercentBox,
) -> PdfPatchResult:
    candidates = pptx.media_candidates(slide)
    reader = PdfReader(str(native_pdf))
    if len(reader.pages) != 1:
        raise FidelityError(f"Expected a one-page native PDF, found {len(reader.pages)}")
    source_page = reader.pages[0]
    content_before = source_page.get_contents().get_data()
    matches = []
    unmatched = []
    for item in source_page.images:
        image = item.image.convert("RGBA")
        is_content = item.image.format in {"JPEG", "JPEG2000"} or (image.width > 300 and image.height > 300)
        if not is_content:
            continue
        best = best_candidate(image, candidates)
        threshold = 0.84 if item.image.format in {"JPEG", "JPEG2000"} else 0.90
        if best is None or best[0] < threshold:
            unmatched.append(item.name)
        else:
            matches.append((item.name, best))

    writer = PdfWriter()
    writer.clone_document_from_reader(reader)
    writer_images = {item.name: item for item in writer.pages[0].images}
    patched = 0
    upgraded_masks = 0
    for pdf_name, best in matches:
        score, media_name, crop, raw = best
        del score, media_name
        native = resize_limit(crop_native(raw, crop), max_dimension)
        writer_image = writer_images[pdf_name]
        stream = writer_image.indirect_reference.get_object()
        alpha = native.getchannel("A")
        has_alpha = alpha.getextrema() != (255, 255)
        source_format = Image.open(io.BytesIO(raw)).format
        lossless = max_bytes is None and (has_alpha or source_format == "PNG")
        _set_rgb(stream, native, jpeg_quality, lossless=lossless)
        smask = stream.get("/SMask")
        if has_alpha and smask is not None:
            _set_gray(smask.get_object(), alpha)
            upgraded_masks += 1
        patched += 1

    page = writer.pages[0]
    pixels = crop_pixels(
        reference_png, padding_px=padding_px,
        crop_percent=crop_percent, expand_percent=expand_percent,
    )
    tight_box = RectangleObject(pdf_box(pixels, float(page.mediabox.width), float(page.mediabox.height)))
    page.mediabox = tight_box
    page.cropbox = tight_box
    page.trimbox = tight_box
    page.bleedbox = tight_box
    page.artbox = tight_box
    output_pdf.parent.mkdir(parents=True, exist_ok=True)
    with output_pdf.open("wb") as stream:
        writer.write(stream)

    verify = PdfReader(str(output_pdf))
    content_after = verify.pages[0].get_contents().get_data()
    before_hash = hashlib.sha256(content_before).hexdigest()
    after_hash = hashlib.sha256(content_after).hexdigest()
    if before_hash != after_hash:
        output_pdf.unlink(missing_ok=True)
        raise FidelityError("STRUCTURE_CONTENT_STREAM_CHANGED")
    size = output_pdf.stat().st_size
    if max_bytes is not None and size >= max_bytes:
        output_pdf.unlink(missing_ok=True)
        raise BudgetError(f"Output is {size} bytes; strict limit is < {max_bytes}")
    return PdfPatchResult(
        content_hash=before_hash,
        patched_images=patched,
        upgraded_masks=upgraded_masks,
        unmatched_images=len(unmatched),
        crop_box=[float(value) for value in tight_box],
        output_bytes=size,
    )
