from __future__ import annotations

import json
import os
import platform
import shutil
import subprocess
import sys
import uuid
from dataclasses import asdict, dataclass
from importlib.metadata import PackageNotFoundError, version as package_version
from pathlib import Path

from . import PIPELINE_REVISION, __version__
from .errors import BudgetError, EnvironmentError, FidelityError
from .model import ArtifactRecord, Finding, JobReport, Severity, Verdict
from .ooxml import PptxPackage
from .pdf_pipeline import PdfPatchResult, restore_pdf_images
from .powerpoint import export_reference, probe
from .qa import coverage_findings, validate_multiscale_pdf, validate_pdf_structure, validate_svg_renders, validate_svg_structure, validate_svg_vector_invariant
from .reporting import write_reports
from .render import svg_renderer_info
from .svg_pipeline import SvgPatchResult, convert_pdf_to_svg, restore_svg_images
from .util import checksum_lines, default_work_root, ensure_within, native_long_path, parse_slides, safe_slug, sha256_file, stable_json, utc_now, write_json


@dataclass(slots=True)
class ExportOptions:
    slides: str = "1"
    output_root: Path | None = None
    padding_px: int = 16
    crop_percent: tuple[float, float, float, float] | None = None
    expand_percent: tuple[float, float, float, float] = (0.0, 0.0, 0.0, 0.0)
    reference_width: int = 4000
    pdf_max_bytes: int | None = None
    pdf_max_image_dimension: int | None = None
    pdf_jpeg_quality: int = 95
    svg_max_bytes: int | None = None
    dpis: tuple[int, ...] = (72, 96, 120, 144, 192, 300, 600)
    svg_widths: tuple[int, ...] = (640, 1600, 3840)
    strict: bool = True


def doctor(work_root: Path | None = None) -> dict:
    from .util import require_executable

    work_root = work_root or default_work_root() / "doctor"
    result = {
        "tool": {"name": "SlideGuard", "version": __version__},
        "platform": {"system": platform.system(), "release": platform.release(), "version": platform.version(), "machine": platform.machine()},
        "python": {"executable": sys.executable, "version": sys.version.split()[0]},
        "executables": {},
        "powerpoint": None,
        "ok": True,
        "errors": [],
    }
    for executable in ("powershell", "pdftocairo", "pdftoppm", "pdfinfo"):
        try:
            result["executables"][executable] = require_executable(executable)
        except Exception as exc:
            result["ok"] = False
            result["errors"].append(str(exc))
    try:
        result["svgRenderer"] = svg_renderer_info()
    except Exception as exc:
        result["ok"] = False
        result["errors"].append(str(exc))
    try:
        result["powerpoint"] = probe(work_root)
    except Exception as exc:
        result["ok"] = False
        result["errors"].append(str(exc))
    packages = {
        "lxml": "lxml",
        "numpy": "numpy",
        "PIL": "Pillow",
        "pypdf": "pypdf",
        "jsonschema": "jsonschema",
        "skimage": "scikit-image",
    }
    for module, distribution in packages.items():
        try:
            __import__(module)
            result.setdefault("pythonPackages", {})[module] = package_version(distribution)
        except (ImportError, PackageNotFoundError) as exc:
            result["ok"] = False
            result["errors"].append(f"Python package {module}: {exc}")
    return result


def _budget_profiles(options: ExportOptions) -> list[tuple[int | None, int]]:
    if options.pdf_max_bytes is None:
        return [(options.pdf_max_image_dimension, options.pdf_jpeg_quality)]
    if options.pdf_max_image_dimension is not None:
        return [(options.pdf_max_image_dimension, options.pdf_jpeg_quality)]
    return [(2400, 95), (2200, 92), (2000, 90), (1800, 88), (1600, 86), (1400, 84), (1200, 82), (1000, 80), (900, 76), (800, 72)]


def _patch_pdf_with_budget(
    native_pdf: Path,
    pptx: PptxPackage,
    slide: int,
    reference_png: Path,
    output_pdf: Path,
    options: ExportOptions,
) -> tuple[PdfPatchResult, dict]:
    failures = []
    for dimension, quality in _budget_profiles(options):
        candidate = output_pdf.with_name(f"candidate-{dimension or 'source'}-q{quality}.pdf")
        try:
            result = restore_pdf_images(
                native_pdf, pptx, slide, reference_png, candidate,
                max_dimension=dimension, jpeg_quality=quality,
                max_bytes=options.pdf_max_bytes, padding_px=options.padding_px,
                crop_percent=options.crop_percent, expand_percent=options.expand_percent,
            )
            candidate.replace(output_pdf)
            return result, {"maxImageDimension": dimension, "jpegQuality": quality, "attempts": failures + [{"status": "accepted", "dimension": dimension, "quality": quality, "bytes": result.output_bytes}]}
        except BudgetError as exc:
            failures.append({"status": "over-budget", "dimension": dimension, "quality": quality, "message": str(exc)})
    raise BudgetError(f"No fidelity profile met the PDF budget after {len(failures)} attempts")


def _patch_svg_with_budget(
    raw_svg: Path,
    pptx: PptxPackage,
    slide: int,
    reference_png: Path,
    output_svg: Path,
    options: ExportOptions,
) -> tuple[SvgPatchResult, dict]:
    profiles = [(1800, 90), (1600, 88), (1400, 86), (1200, 84), (1000, 82), (900, 78), (800, 74), (700, 70)]
    attempts = []
    assert options.svg_max_bytes is not None
    for dimension, quality in profiles:
        candidate = output_svg.with_name(f"candidate-{dimension}-q{quality}.svg")
        result = restore_svg_images(
            raw_svg, pptx, slide, reference_png, candidate,
            padding_px=options.padding_px, max_image_dimension=dimension,
            jpeg_quality=quality,
            crop_percent=options.crop_percent, expand_percent=options.expand_percent,
        )
        accepted = result.output_bytes < options.svg_max_bytes
        attempts.append({"status": "accepted" if accepted else "over-budget", "dimension": dimension, "quality": quality, "bytes": result.output_bytes})
        if accepted:
            candidate.replace(output_svg)
            return result, {"maxImageDimension": dimension, "jpegQuality": quality, "attempts": attempts}
        candidate.unlink(missing_ok=True)
    raise BudgetError(f"No SVG profile met the strict < {options.svg_max_bytes} byte budget")


def _relative_artifact(kind: str, path: Path, package_dir: Path, slide: int, producer: str, metadata: dict | None = None) -> ArtifactRecord:
    return ArtifactRecord(kind, path.relative_to(package_dir).as_posix(), sha256_file(path), path.stat().st_size, slide, producer, metadata or {})


def export_job(input_pptx: Path, options: ExportOptions) -> tuple[Path, JobReport]:
    source = input_pptx.resolve()
    package = PptxPackage.open(source)
    slides = parse_slides(options.slides, package.slide_count)
    source_hash_before = sha256_file(source)
    config = asdict(options)
    config["output_root"] = str(options.output_root) if options.output_root else None
    config["dpis"] = list(options.dpis)
    config["svg_widths"] = list(options.svg_widths)
    config["pipeline_revision"] = PIPELINE_REVISION
    config_hash = __import__("hashlib").sha256(stable_json(config).encode("utf-8")).hexdigest()
    slug = safe_slug(source.stem)
    job_id = f"{slug}--{source_hash_before[:8]}--{config_hash[:8]}"
    work_dir = default_work_root() / f"{source_hash_before[:8]}-{config_hash[:8]}-{uuid.uuid4().hex[:6]}"
    package_dir = work_dir / "package"
    package_dir.mkdir(parents=True, exist_ok=False)
    (package_dir / "svg").mkdir()
    (package_dir / "png").mkdir()
    (package_dir / "evidence").mkdir()

    environment = doctor(work_dir / "doctor")
    if not environment["ok"]:
        raise EnvironmentError("; ".join(environment["errors"]))
    features = [package.inventory(slide) for slide in slides]
    findings: list[Finding] = []
    artifacts: list[ArtifactRecord] = []
    slide_manifest = []

    for ordinal, (slide, inventory) in enumerate(zip(slides, features), 1):
        slide_work = work_dir / f"slide-{slide:04d}"
        slide_work.mkdir(parents=True)
        export = export_reference(source, slide, slide_work, options.reference_width)
        native_pdf = Path(export["nativePdf"])
        reference_png = Path(export["referencePng"])
        stem = f"{slug}--p{ordinal:04d}-s{slide:04d}"
        final_pdf = package_dir / f"{stem}.pdf"
        pdf_result, profile = _patch_pdf_with_budget(native_pdf, package, slide, reference_png, final_pdf, options)
        artifacts.append(_relative_artifact("pdf", final_pdf, package_dir, slide, "powerpoint-native+image-restore", {**asdict(pdf_result), **profile}))

        raw_svg = slide_work / "powerpoint-native.svg"
        final_svg = package_dir / "svg" / f"{stem}.svg"
        convert_pdf_to_svg(native_pdf, raw_svg)
        svg_result = restore_svg_images(
            raw_svg, package, slide, reference_png, final_svg,
            padding_px=options.padding_px, crop_percent=options.crop_percent,
            expand_percent=options.expand_percent,
        )
        artifacts.append(_relative_artifact("svg", final_svg, package_dir, slide, "pdftocairo+image-restore", asdict(svg_result)))

        if options.svg_max_bytes is not None:
            compact_dir = package_dir / "svg-compact"
            compact_dir.mkdir(exist_ok=True)
            compact_svg = compact_dir / f"{stem}--under-{options.svg_max_bytes}.svg"
            compact_result, compact_profile = _patch_svg_with_budget(
                raw_svg, package, slide, reference_png, compact_svg, options,
            )
            artifacts.append(_relative_artifact(
                "svg-compact", compact_svg, package_dir, slide,
                "pdftocairo+budgeted-image-restore", {**asdict(compact_result), **compact_profile},
            ))
            findings.extend(validate_svg_structure(compact_svg, inventory))
            findings.extend(validate_svg_vector_invariant(compact_svg, raw_svg, inventory))
            compact_evidence = package_dir / "evidence" / f"p{ordinal:04d}-s{slide:04d}-compact"
            compact_findings, _ = validate_svg_renders(
                compact_svg, compact_evidence, inventory, list(options.svg_widths),
                reference_png, pdf_result.crop_box,
                float(export["slideWidthPt"]), float(export["slideHeightPt"]),
            )
            findings.extend(compact_findings)

        findings.extend(validate_pdf_structure(final_pdf, native_pdf, inventory, pdf_result.crop_box))
        findings.extend(validate_svg_structure(final_svg, inventory))
        findings.extend(validate_svg_vector_invariant(final_svg, raw_svg, inventory))
        evidence_dir = package_dir / "evidence" / f"p{ordinal:04d}-s{slide:04d}"
        findings.extend(validate_multiscale_pdf(
            final_pdf, native_pdf, evidence_dir, inventory, pdf_result.crop_box,
            float(export["slideWidthPt"]), float(export["slideHeightPt"]), list(options.dpis),
        ))
        svg_findings, png_source = validate_svg_renders(
            final_svg, evidence_dir, inventory, list(options.svg_widths),
            reference_png, pdf_result.crop_box,
            float(export["slideWidthPt"]), float(export["slideHeightPt"]),
        )
        findings.extend(svg_findings)
        final_png = package_dir / "png" / f"{stem}.png"
        shutil.copy2(png_source, final_png)
        artifacts.append(_relative_artifact("png", final_png, package_dir, slide, "accepted-svg-raster"))

        successful = {"pdf-content-stream", "asset-stream", "svg-alpha"}
        findings.extend(coverage_findings(inventory, successful))
        if pdf_result.unmatched_images:
            findings.append(Finding(
                code="IMAGE_UNMATCHED_CANDIDATE", status=Verdict.PASS_WITH_SOURCE_WARNINGS,
                severity=Severity.WARNING, message=f"{pdf_result.unmatched_images} large PDF image(s) were kept unchanged because no source match was safe",
                validator="asset-stream@1.0", slide=slide, actual=pdf_result.unmatched_images,
            ))
        slide_manifest.append({"outputOrdinal": ordinal, "sourceSlide": slide, "slidePart": inventory.slide_part, "stem": stem})

    source_hash_after = sha256_file(source)
    findings.append(Finding(
        code="SOURCE_IMMUTABLE", status=Verdict.PASS if source_hash_before == source_hash_after else Verdict.FAIL,
        severity=Severity.INFO if source_hash_before == source_hash_after else Severity.ERROR,
        message="Source PPTX SHA-256 is unchanged" if source_hash_before == source_hash_after else "Source PPTX changed during export",
        validator="source-integrity@1.0", expected=source_hash_before, actual=source_hash_after,
    ))
    verdict = Verdict.FAIL if any(item.status == Verdict.FAIL for item in findings) else (
        Verdict.PASS_WITH_SOURCE_WARNINGS if any(item.status == Verdict.PASS_WITH_SOURCE_WARNINGS for item in findings) else Verdict.PASS
    )
    for finding in findings:
        normalized = []
        for item in finding.evidence:
            evidence_path = Path(item)
            try:
                normalized.append(evidence_path.relative_to(package_dir).as_posix())
            except ValueError:
                normalized.append(item)
        finding.evidence = normalized
    report = JobReport(
        schema_version="1.0", tool_version=__version__, job_id=job_id,
        source_path=str(source), source_sha256_before=source_hash_before, source_sha256_after=source_hash_after,
        config=config, environment=environment, features=features, artifacts=artifacts,
        findings=findings, verdict=verdict, created_at=utc_now(),
    )
    manifest = {
        "schemaVersion": "1.0", "toolVersion": __version__, "pipelineRevision": PIPELINE_REVISION, "jobId": job_id,
        "source": {"name": source.name, "sha256": source_hash_before, "slideCount": package.slide_count},
        "slides": slide_manifest, "artifacts": [asdict(item) for item in artifacts],
        "verdict": verdict.value,
    }
    write_json(package_dir / "manifest.json", manifest)
    write_reports(report, package_dir)
    checksum_targets = [path for path in package_dir.rglob("*") if path.is_file() and path.name != "checksums.sha256"]
    (package_dir / "checksums.sha256").write_text(checksum_lines(checksum_targets, package_dir), encoding="utf-8")

    if options.strict and verdict == Verdict.FAIL:
        raise FidelityError(f"QA failed; evidence remains at {package_dir}")

    output_root = (options.output_root or (source.parent / "slideguard-output")).resolve()
    output_root.mkdir(parents=True, exist_ok=True)
    final_dir = output_root / job_id
    publish_dir = output_root / f".sg-publish-{uuid.uuid4().hex[:8]}"
    shutil.copytree(native_long_path(package_dir), native_long_path(publish_dir))
    if final_dir.exists():
        old_manifest = final_dir / "manifest.json"
        if old_manifest.exists() and json.loads(old_manifest.read_text(encoding="utf-8"))["jobId"] == job_id:
            shutil.rmtree(native_long_path(publish_dir))
        else:
            shutil.rmtree(native_long_path(publish_dir))
            raise FidelityError(f"Output collision: {final_dir}")
    else:
        os.replace(native_long_path(publish_dir), native_long_path(final_dir))
    ensure_within(work_dir, default_work_root())
    shutil.rmtree(work_dir)
    return final_dir, report
