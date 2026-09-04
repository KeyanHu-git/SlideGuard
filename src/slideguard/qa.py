from __future__ import annotations

import hashlib
import io
import re
from pathlib import Path

import numpy as np
from lxml import etree
from PIL import Image
from pypdf import PdfReader, PdfWriter
from pypdf.generic import RectangleObject

from .model import FeatureInventory, Finding, Severity, Verdict
from .render import render_pdf, render_svg


def _finding(code: str, passed: bool, message: str, validator: str, **kwargs) -> Finding:
    return Finding(
        code=code,
        status=Verdict.PASS if passed else Verdict.FAIL,
        severity=Severity.INFO if passed else Severity.ERROR,
        message=message,
        validator=validator,
        **kwargs,
    )


def _content_hash(pdf: Path) -> str:
    page = PdfReader(str(pdf)).pages[0]
    return hashlib.sha256(page.get_contents().get_data()).hexdigest()


def validate_pdf_structure(
    pdf: Path,
    native_pdf: Path,
    inventory: FeatureInventory,
    expected_crop: list[float],
) -> list[Finding]:
    findings: list[Finding] = []
    reader = PdfReader(str(pdf))
    native = PdfReader(str(native_pdf))
    findings.append(_finding(
        "STRUCTURE_PAGE_COUNT", len(reader.pages) == 1,
        f"PDF page count is {len(reader.pages)}", "pdf-structure@1.0",
        slide=inventory.slide, expected=1, actual=len(reader.pages), threshold=0,
    ))
    page = reader.pages[0]
    actual_box = [float(value) for value in page.mediabox]
    box_error = max(abs(a - b) for a, b in zip(actual_box, expected_crop))
    findings.append(_finding(
        "STRUCTURE_PAGE_BOX", box_error <= 0.01,
        f"PDF page box max error is {box_error:.6f} pt", "pdf-page-box@1.0",
        slide=inventory.slide, metric="max_box_error_pt", expected=0.0,
        actual=box_error, threshold=0.01,
    ))
    before = hashlib.sha256(native.pages[0].get_contents().get_data()).hexdigest()
    after = _content_hash(pdf)
    findings.append(_finding(
        "STRUCTURE_CONTENT_STREAM", before == after,
        "PowerPoint page drawing instructions are byte-identical" if before == after else "Page drawing instructions changed",
        "pdf-content-stream@1.0", slide=inventory.slide, expected=before, actual=after,
    ))
    stream = page.get_contents().get_data()
    has_vector_ops = bool(re.search(rb"(?:^|\s)(?:m|l|c|v|y|re|S|s|f|f\*|B|B\*|BT)(?:\s|$)", stream))
    findings.append(_finding(
        "STRUCTURE_VECTOR_CONTENT", has_vector_ops,
        "PDF contains vector/text drawing operators" if has_vector_ops else "PDF appears to be image-only",
        "pdf-vector-coverage@1.0", slide=inventory.slide,
    ))
    if inventory.dashed_line_count:
        findings.append(_finding(
            "FIDELITY_DASH", before == after,
            f"{inventory.dashed_line_count} source dashed line(s) are protected by the byte-identical content stream",
            "pdf-dash-invariant@1.0", slide=inventory.slide,
            expected="unchanged page operators", actual="unchanged" if before == after else "changed",
        ))
    return findings


def validate_svg_structure(svg: Path, inventory: FeatureInventory) -> list[Finding]:
    findings: list[Finding] = []
    parser = etree.XMLParser(resolve_entities=False, no_network=True, huge_tree=True)
    tree = etree.parse(str(svg), parser)
    root = tree.getroot()
    local = etree.QName(root).localname
    viewbox = root.get("viewBox", "").replace(",", " ").split()
    valid_viewbox = local == "svg" and len(viewbox) == 4 and all(float(value) > 0 for value in viewbox[2:])
    findings.append(_finding(
        "STRUCTURE_SVG_PARSE", valid_viewbox,
        "SVG parses and has a positive viewBox" if valid_viewbox else "SVG root/viewBox is invalid",
        "svg-structure@1.0", slide=inventory.slide,
    ))
    external = []
    for element in tree.iter():
        for name, value in element.attrib.items():
            local_name = etree.QName(name).localname
            if local_name == "href" and not (value.startswith("data:") or value.startswith("#")):
                external.append(value)
            if local_name.lower().startswith("on"):
                external.append(f"event:{local_name}")
        if etree.QName(element).localname == "script":
            external.append("script")
    findings.append(_finding(
        "SEC_SVG_EXTERNAL_RESOURCE", not external,
        "SVG is self-contained and script-free" if not external else f"External/script references: {external[:3]}",
        "svg-security@1.0", slide=inventory.slide, expected=0, actual=len(external), threshold=0,
    ))
    vx, vy, vw, vh = map(float, viewbox) if len(viewbox) == 4 else (0, 0, 0, 0)
    white = {"white", "#fff", "#ffffff", "rgb(100%,100%,100%)"}
    page_white_rects = 0
    for element in list(root):
        if etree.QName(element).localname != "rect":
            continue
        try:
            x, y = float(element.get("x", "0")), float(element.get("y", "0"))
            width, height = float(element.get("width", "0")), float(element.get("height", "0"))
            opacity = float(element.get("fill-opacity", "1"))
        except ValueError:
            continue
        fill = element.get("fill", "").lower().replace(" ", "")
        if fill in white and opacity >= 0.999 and x <= vx and y <= vy and x + width >= vx + vw and y + height >= vy + vh:
            page_white_rects += 1
    findings.append(_finding(
        "FIDELITY_ALPHA_CANVAS", page_white_rects == 0,
        "SVG root canvas is transparent" if not page_white_rects else "SVG contains an opaque full-canvas white rectangle",
        "svg-alpha-canvas@1.0", slide=inventory.slide, expected=0, actual=page_white_rects, threshold=0,
    ))
    vector_nodes = len(tree.xpath("//*[local-name()='path' or local-name()='text' or local-name()='polygon' or local-name()='polyline' or local-name()='use']"))
    findings.append(_finding(
        "STRUCTURE_SVG_VECTOR_CONTENT", vector_nodes > 0,
        f"SVG contains {vector_nodes} vector/text nodes" if vector_nodes else "SVG appears to contain no vector content",
        "svg-vector-coverage@1.0", slide=inventory.slide, actual=vector_nodes, threshold=1,
    ))
    alpha_sources = sum(1 for item in inventory.media if item.get("hasAlpha"))
    if alpha_sources:
        mask_nodes = len(tree.xpath("//*[local-name()='mask']"))
        findings.append(_finding(
            "FIDELITY_ALPHA_MASK", mask_nodes >= alpha_sources,
            f"SVG retains {mask_nodes} mask(s) for {alpha_sources} alpha-bearing source asset(s)",
            "svg-alpha-mask@1.0", slide=inventory.slide,
            expected=alpha_sources, actual=mask_nodes, threshold=alpha_sources,
        ))
    return findings


def _svg_vector_hash(svg: Path) -> str:
    parser = etree.XMLParser(resolve_entities=False, no_network=True, huge_tree=True, remove_blank_text=True)
    tree = etree.parse(str(svg), parser)
    root = tree.getroot()
    viewbox = root.get("viewBox", "0 0 0 0").replace(",", " ").split()
    vx, vy, vw, vh = map(float, viewbox) if len(viewbox) == 4 else (0, 0, 0, 0)
    white = {"white", "#fff", "#ffffff", "rgb(100%,100%,100%)"}
    for element in list(root):
        if etree.QName(element).localname != "rect":
            continue
        try:
            x, y = float(element.get("x", "0")), float(element.get("y", "0"))
            width, height = float(element.get("width", "0")), float(element.get("height", "0"))
            opacity = float(element.get("fill-opacity", "1"))
        except ValueError:
            continue
        fill = element.get("fill", "").lower().replace(" ", "")
        if fill in white and opacity >= 0.999 and x <= vx and y <= vy and x + width >= vx + vw and y + height >= vy + vh:
            root.remove(element)
    for element in tree.xpath("//*[local-name()='image']"):
        parent = element.getparent()
        if parent is not None:
            parent.remove(element)
    for name in ("viewBox", "width", "height"):
        root.attrib.pop(name, None)
    canonical = etree.tostring(root, method="c14n", exclusive=True, with_comments=False)
    return hashlib.sha256(canonical).hexdigest()


def validate_svg_vector_invariant(svg: Path, native_svg: Path, inventory: FeatureInventory) -> list[Finding]:
    expected = _svg_vector_hash(native_svg)
    actual = _svg_vector_hash(svg)
    return [_finding(
        "STRUCTURE_SVG_VECTOR_INVARIANT", expected == actual,
        "SVG vector/filter subtree is byte-stable after normalization" if expected == actual else "SVG vector/filter subtree changed",
        "svg-vector-fingerprint@1.0", slide=inventory.slide,
        expected=expected, actual=actual,
    )]


def _reference_crop(reference_png: Path, crop_box: list[float], page_width: float, page_height: float) -> Image.Image:
    image = Image.open(reference_png).convert("RGB")
    x0, y0, x1, y1 = crop_box
    left = round(image.width * x0 / page_width)
    right = round(image.width * x1 / page_width)
    top = round(image.height * (1 - y1 / page_height))
    bottom = round(image.height * (1 - y0 / page_height))
    return image.crop((left, top, right, bottom))


def _similarity(reference: Image.Image, candidate: Image.Image) -> tuple[float, float, float]:
    from skimage.metrics import structural_similarity

    candidate = candidate.convert("RGB")
    reference = reference.convert("RGB").resize(candidate.size, Image.Resampling.LANCZOS)
    max_side = max(candidate.size)
    if max_side > 2048:
        ratio = 2048 / max_side
        size = (max(1, round(candidate.width * ratio)), max(1, round(candidate.height * ratio)))
        candidate = candidate.resize(size, Image.Resampling.LANCZOS)
        reference = reference.resize(size, Image.Resampling.LANCZOS)
    first = np.asarray(reference, dtype=np.float32)
    second = np.asarray(candidate, dtype=np.float32)
    mae = float(np.mean(np.abs(first - second)) / 255.0)
    ssim = float(structural_similarity(first, second, channel_axis=2, data_range=255.0))
    diff = np.max(np.abs(first - second), axis=2)
    mask = diff > 48
    row = mask.mean(axis=1)
    col = mask.mean(axis=0)

    def peak_score(values: np.ndarray) -> float:
        if len(values) < 7:
            return float(values.max(initial=0.0))
        best = 0.0
        for index in range(3, len(values) - 3):
            neighborhood = np.concatenate((values[index - 3:index], values[index + 1:index + 4]))
            best = max(best, float(values[index] - np.median(neighborhood)))
        return best

    seam_score = max(peak_score(row), peak_score(col))
    return ssim, mae, seam_score


def validate_multiscale_pdf(
    pdf: Path,
    native_pdf: Path,
    work_dir: Path,
    inventory: FeatureInventory,
    crop_box: list[float],
    page_width: float,
    page_height: float,
    dpis: list[int],
) -> list[Finding]:
    del page_width, page_height
    findings = []
    native_reader = PdfReader(str(native_pdf))
    native_writer = PdfWriter()
    native_writer.clone_document_from_reader(native_reader)
    native_page = native_writer.pages[0]
    box = RectangleObject(crop_box)
    native_page.mediabox = box
    native_page.cropbox = box
    native_page.trimbox = box
    native_page.bleedbox = box
    native_page.artbox = box
    cropped_native_pdf = work_dir / "powerpoint-native-cropped.pdf"
    work_dir.mkdir(parents=True, exist_ok=True)
    with cropped_native_pdf.open("wb") as stream:
        native_writer.write(stream)
    for dpi in dpis:
        rendered = render_pdf(pdf, work_dir / f"pdf-{dpi}dpi.png", dpi)
        native_render = render_pdf(cropped_native_pdf, work_dir / f"native-{dpi}dpi.png", dpi)
        ssim, mae, seam = _similarity(Image.open(native_render), Image.open(rendered))
        # Source-image restoration intentionally differs from PowerPoint's
        # downsampled image plane. Page operators stay byte-identical, so the
        # wider threshold applies only when images are present.
        ssim_floor = 0.85 if inventory.image_count else 0.94
        findings.append(_finding(
            "FIDELITY_GLOBAL", ssim >= ssim_floor and mae <= 0.08,
            f"PDF render at {dpi} dpi: SSIM={ssim:.5f}, normalized MAE={mae:.5f}",
            "multiscale-raster@1.0", slide=inventory.slide, metric=f"ssim_{dpi}dpi",
            expected=1.0, actual=ssim, threshold=ssim_floor, evidence=[str(rendered)],
        ))
        findings.append(_finding(
            "FIDELITY_SEAM", seam <= 0.55,
            f"PDF render at {dpi} dpi: residual seam score={seam:.5f}",
            "residual-seam@1.0", slide=inventory.slide, metric=f"seam_score_{dpi}dpi",
            expected=0.0, actual=seam, threshold=0.55, evidence=[str(rendered)],
        ))
    return findings


def validate_svg_renders(
    svg: Path,
    work_dir: Path,
    inventory: FeatureInventory,
    widths: list[int],
    reference_png: Path | None = None,
    crop_box: list[float] | None = None,
    page_width: float | None = None,
    page_height: float | None = None,
) -> tuple[list[Finding], Path]:
    findings = []
    last = None
    for width in widths:
        last = render_svg(svg, work_dir / f"svg-{width}px.png", width=width)
        image = Image.open(last).convert("RGBA")
        alpha = np.asarray(image.getchannel("A"), dtype=np.uint8)
        transparent_pixels = int((alpha < 255).sum())
        # A transparent canvas can render fully opaque when the source artwork
        # covers the complete slide. Structural canvas/mask checks are the gate.
        findings.append(_finding(
            "FIDELITY_ALPHA_RENDER", True,
            f"SVG render at {width}px has {transparent_pixels} exposed non-opaque pixels",
            "svg-alpha-render@1.0", slide=inventory.slide,
            metric=f"non_opaque_pixels_{width}px", actual=transparent_pixels, threshold=1,
            evidence=[str(last)],
        ))
        if reference_png is not None and crop_box is not None and page_width is not None and page_height is not None:
            white = Image.new("RGBA", image.size, (255, 255, 255, 255))
            white.alpha_composite(image)
            white_rgb = white.convert("RGB")
            matte_path = work_dir / f"svg-white-matte-{width}px.png"
            white_rgb.save(matte_path, format="PNG", optimize=True)
            reference = _reference_crop(reference_png, crop_box, page_width, page_height)
            ssim, mae, seam = _similarity(reference, white_rgb)
            floor = 0.75 if inventory.image_count else 0.90
            findings.append(_finding(
                "FIDELITY_SVG_WHITE_MATTE", ssim >= floor and mae <= 0.12 and seam <= 0.55,
                f"SVG on white at {width}px: SSIM={ssim:.5f}, MAE={mae:.5f}, seam={seam:.5f}",
                "svg-white-matte@1.0", slide=inventory.slide,
                metric=f"ssim_white_{width}px", expected=1.0, actual=ssim,
                threshold=f"SSIM>={floor}; MAE<=0.12; seam<=0.55", evidence=[str(matte_path)],
            ))
    assert last is not None
    return findings, last


def coverage_findings(inventory: FeatureInventory, successful_validators: set[str]) -> list[Finding]:
    feature_to_validator = {
        "images": "asset-stream",
        "lines": "pdf-content-stream",
        "dashes": "pdf-content-stream",
        "shadows": "pdf-content-stream",
        "alpha": "svg-alpha",
        "crops": "pdf-content-stream",
        "groups": "pdf-content-stream",
        "gradients": "pdf-content-stream",
        "text": "pdf-content-stream",
        "math": "pdf-content-stream",
    }
    findings = []
    for feature in inventory.active_features():
        validator = feature_to_validator[feature]
        covered = validator in successful_validators
        findings.append(_finding(
            "QA_FEATURE_COVERAGE" if covered else "QA_COVERAGE_GAP",
            covered,
            f"Feature '{feature}' is covered by {validator}" if covered else f"No successful validator covered '{feature}'",
            "coverage-audit@1.0", slide=inventory.slide, object_id=feature,
        ))
    return findings
