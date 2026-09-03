from __future__ import annotations

import base64
import io
from dataclasses import dataclass
from pathlib import Path

from lxml import etree
from PIL import Image

from .image_match import best_candidate, crop_native, resize_limit
from .crop import PercentBox, crop_pixels, svg_box
from .ooxml import PptxPackage
from .util import require_executable, run_checked


SVG_NS = "http://www.w3.org/2000/svg"
XLINK_NS = "http://www.w3.org/1999/xlink"
NS = {"svg": SVG_NS, "xlink": XLINK_NS}
HREF = f"{{{XLINK_NS}}}href"


@dataclass(slots=True)
class SvgPatchResult:
    patched_images: int
    upgraded_masks: int
    unmatched_images: int
    removed_page_backgrounds: int
    view_box: list[float]
    output_bytes: int


def _decode_data_url(url: str) -> bytes:
    return base64.b64decode(url.split(",", 1)[1])


def _encode_png(image: Image.Image) -> str:
    stream = io.BytesIO()
    image.save(stream, format="PNG", optimize=False, compress_level=6)
    return "data:image/png;base64," + base64.b64encode(stream.getvalue()).decode("ascii")


def _encode_rgb(image: Image.Image, jpeg_quality: int | None) -> str:
    if jpeg_quality is None:
        return _encode_png(image.convert("RGB"))
    stream = io.BytesIO()
    image.convert("RGB").save(
        stream, format="JPEG", quality=jpeg_quality, optimize=True,
        progressive=False, subsampling=0,
    )
    return "data:image/jpeg;base64," + base64.b64encode(stream.getvalue()).decode("ascii")


def convert_pdf_to_svg(native_pdf: Path, output_svg: Path) -> None:
    converter = require_executable("pdftocairo")
    output_svg.parent.mkdir(parents=True, exist_ok=True)
    run_checked([converter, "-svg", str(native_pdf), str(output_svg)], timeout=300)
    if not output_svg.exists():
        raise RuntimeError("pdftocairo returned without creating SVG")


def restore_svg_images(
    source_svg: Path,
    pptx: PptxPackage,
    slide: int,
    reference_png: Path,
    output_svg: Path,
    *,
    padding_px: int,
    max_image_dimension: int | None = None,
    jpeg_quality: int | None = None,
    crop_percent: PercentBox | None = None,
    expand_percent: PercentBox = (0.0, 0.0, 0.0, 0.0),
) -> SvgPatchResult:
    tree = etree.parse(str(source_svg), etree.XMLParser(resolve_entities=False, no_network=True, huge_tree=True))
    root = tree.getroot()
    view_box = [float(value) for value in root.get("viewBox").replace(",", " ").split()]
    vx, vy, vw, vh = view_box
    removed = 0
    white_fills = {"white", "#fff", "#ffffff", "rgb(100%,100%,100%)"}
    for element in list(root):
        if etree.QName(element).localname != "rect":
            continue
        fill = element.get("fill", "").lower().replace(" ", "")
        opacity = float(element.get("fill-opacity", "1"))
        try:
            x, y = float(element.get("x", "0")), float(element.get("y", "0"))
            width, height = float(element.get("width", "0")), float(element.get("height", "0"))
        except ValueError:
            continue
        if fill in white_fills and opacity >= 0.999 and x <= vx and y <= vy and x + width >= vx + vw and y + height >= vy + vh:
            root.remove(element)
            removed += 1

    images = {element.get("id"): element for element in root.xpath("//svg:image[@id]", namespaces=NS)}
    transforms: dict[str, set[str]] = {}
    for use in root.xpath("//svg:use[@xlink:href]", namespaces=NS):
        ref = use.get(HREF, "")
        if ref.startswith("#"):
            transforms.setdefault(ref[1:], set()).add(use.get("transform", ""))
    candidates = pptx.media_candidates(slide)
    patched = 0
    masks = 0
    unmatched = 0
    for image_id, element in images.items():
        href = element.get(HREF, "")
        if not href.startswith("data:"):
            continue
        try:
            raw = _decode_data_url(href)
            target = Image.open(io.BytesIO(raw))
            target_format, target_mode, target_size = target.format, target.mode, target.size
        except Exception:
            continue
        is_content = target_format in {"JPEG", "JPEG2000"} or (
            target_mode in {"RGB", "RGBA"} and target_size[0] > 300 and target_size[1] > 300 and len(raw) > 10000
        )
        if not is_content:
            continue
        best = best_candidate(target.convert("RGBA"), candidates)
        # PowerPoint stores an RGB plane plus a separate grayscale mask for
        # transparent PNGs. RGB values below transparent pixels are therefore
        # not premultiplied and correlate less strongly despite a correct match.
        threshold = 0.84 if target_format in {"JPEG", "JPEG2000"} else 0.65
        if best is None or best[0] < threshold:
            unmatched += 1
            continue
        native = resize_limit(crop_native(best[3], best[2]), max_image_dimension)
        alpha = native.getchannel("A")
        rgb = native.convert("RGB")
        element.set(HREF, _encode_rgb(rgb, jpeg_quality))
        element.set("preserveAspectRatio", "none")
        if alpha.getextrema() != (255, 255):
            own_transforms = transforms.get(image_id, set())
            for other_id, other in images.items():
                if other_id == image_id or other.get("width") != element.get("width") or other.get("height") != element.get("height"):
                    continue
                if not own_transforms.intersection(transforms.get(other_id, set())):
                    continue
                try:
                    other_image = Image.open(io.BytesIO(_decode_data_url(other.get(HREF, ""))))
                except Exception:
                    continue
                if other_image.mode == "L":
                    other.set(HREF, _encode_png(alpha))
                    other.set("preserveAspectRatio", "none")
                    masks += 1
                    break
        patched += 1

    pixels = crop_pixels(
        reference_png, padding_px=padding_px,
        crop_percent=crop_percent, expand_percent=expand_percent,
    )
    view_box = svg_box(pixels, [vx, vy, vw, vh])
    root.set("viewBox", " ".join(f"{value:.5f}" for value in view_box))
    root.set("width", f"{view_box[2]:.5f}")
    root.set("height", f"{view_box[3]:.5f}")
    output_svg.parent.mkdir(parents=True, exist_ok=True)
    tree.write(str(output_svg), encoding="UTF-8", xml_declaration=True, pretty_print=False)
    return SvgPatchResult(patched, masks, unmatched, removed, view_box, output_svg.stat().st_size)
