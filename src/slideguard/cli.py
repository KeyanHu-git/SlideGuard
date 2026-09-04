from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict
from pathlib import Path
from typing import Any

from . import __version__
from .application import ExportService
from .contracts import emergency_result, failed_result, load_request
from .engine import ExportOptions, doctor, export_job
from .errors import EnvironmentError, InputError, SlideGuardError
from .verify import verify_package


class SlideGuardArgumentParser(argparse.ArgumentParser):
    def error(self, message: str) -> None:
        raise InputError(
            f"Invalid command arguments: {message}",
            stage="validation",
            details={"parserMessage": message},
        )


def _csv_ints(value: str) -> tuple[int, ...]:
    try:
        values = tuple(int(item.strip()) for item in value.split(",") if item.strip())
    except ValueError as exc:
        raise argparse.ArgumentTypeError("Expected comma-separated integers") from exc
    if not values or any(item <= 0 for item in values):
        raise argparse.ArgumentTypeError("Values must be positive integers")
    return values


def _crop_percent(value: str) -> tuple[float, float, float, float]:
    try:
        values = tuple(float(item.strip()) for item in value.split(","))
    except ValueError as exc:
        raise argparse.ArgumentTypeError("Expected left,top,right,bottom percentages") from exc
    if len(values) != 4 or not (0 <= values[0] < values[2] <= 100 and 0 <= values[1] < values[3] <= 100):
        raise argparse.ArgumentTypeError("Crop must satisfy 0 <= left < right <= 100 and 0 <= top < bottom <= 100")
    return values


def _expand_percent(value: str) -> tuple[float, float, float, float]:
    try:
        values = tuple(float(item.strip()) for item in value.split(","))
    except ValueError as exc:
        raise argparse.ArgumentTypeError("Expected one percentage or left,top,right,bottom") from exc
    if len(values) == 1:
        values = values * 4
    if len(values) != 4 or any(item < 0 or item > 100 for item in values):
        raise argparse.ArgumentTypeError("Expansion needs one or four values between 0 and 100")
    return values


def build_parser() -> argparse.ArgumentParser:
    parser = SlideGuardArgumentParser(prog="slideguard", description="Provable high-fidelity PowerPoint figure export")
    parser.add_argument("--version", action="version", version=f"SlideGuard {__version__}")
    sub = parser.add_subparsers(dest="command", required=True)

    doctor_parser = sub.add_parser("doctor", help="Check PowerPoint and renderer capabilities")
    doctor_parser.add_argument("--json", action="store_true", help="Print machine-readable JSON")

    export_parser = sub.add_parser("export", help="Export and verify one or more slides")
    export_parser.add_argument("input", type=Path)
    export_parser.add_argument("--slides", default="1", help="1-based selection such as 1,3-5 or all")
    export_parser.add_argument("--out", type=Path, default=None)
    export_parser.add_argument("--padding-px", type=int, default=16)
    export_parser.add_argument("--crop-percent", type=_crop_percent, default=None, help="Manual left,top,right,bottom crop as slide percentages")
    export_parser.add_argument("--expand-percent", type=_expand_percent, default=(0.0, 0.0, 0.0, 0.0), help="Expand each crop edge by one value or left,top,right,bottom percentages")
    export_parser.add_argument("--reference-width", type=int, default=4000)
    export_parser.add_argument("--pdf-max-bytes", type=int, default=None, help="Strict upper bound: output must be smaller than this")
    export_parser.add_argument("--pdf-max-image-dimension", type=int, default=None)
    export_parser.add_argument("--pdf-jpeg-quality", type=int, default=95)
    export_parser.add_argument("--svg-max-bytes", type=int, default=None, help="Also create a compact SVG strictly smaller than this")
    export_parser.add_argument("--dpis", type=_csv_ints, default=(72, 96, 120, 144, 192, 300, 600))
    export_parser.add_argument("--svg-widths", type=_csv_ints, default=(640, 1600, 3840))
    export_parser.add_argument("--no-strict", action="store_true", help="Publish a failed package for diagnostics")
    export_parser.add_argument("--json", action="store_true", help="Print one machine-readable result document")

    job_parser = sub.add_parser("job", help="Run a versioned JSON request from a UTF-8 file or stdin")
    job_parser.add_argument("request", nargs="?", default="-", help="Request JSON path, or - for stdin")

    gui_parser = sub.add_parser("gui", help="Open the visual crop and export window")
    gui_parser.add_argument("input", nargs="?", type=Path, default=None)

    verify_parser = sub.add_parser("verify", help="Verify hashes in an existing package")
    verify_parser.add_argument("manifest", type=Path)
    fixture_parser = sub.add_parser("fixtures", help="Build deterministic PowerPoint torture fixtures")
    fixture_parser.add_argument("--out", type=Path, required=True)
    return parser


def _print_json(document: dict[str, Any], *, stream: Any | None = None) -> None:
    destination = stream or sys.stdout
    try:
        payload = json.dumps(document, ensure_ascii=False, allow_nan=False, separators=(",", ":"))
    except (TypeError, ValueError) as exc:
        payload = json.dumps(emergency_result(exc), ensure_ascii=True, allow_nan=False, separators=(",", ":"))
    print(payload, file=destination, flush=True)


def _safe_failure(error: BaseException) -> dict[str, Any]:
    try:
        return failed_result(error)
    except Exception as fallback_error:
        return emergency_result(fallback_error)


def _configure_utf8_streams() -> None:
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if callable(reconfigure):
            try:
                reconfigure(encoding="utf-8", errors="strict")
            except (OSError, ValueError):
                pass


def _run_machine(document: dict[str, Any], *, base_dir: Path) -> int:
    progress = document.get("behavior", {}).get("progress") == "jsonl" if isinstance(document.get("behavior"), dict) else False
    sink = (lambda event: _print_json(event, stream=sys.stderr)) if progress else None
    try:
        result = ExportService().execute(document, base_dir=base_dir, event_sink=sink)
    except Exception as exc:
        result = _safe_failure(exc)
    _print_json(result)
    return int(result["exitCode"])


def _document_from_export_args(args: argparse.Namespace) -> dict[str, Any]:
    crop: dict[str, Any] = {
        "mode": "manual" if args.crop_percent else "auto",
        "expandPercent": {
            name: value for name, value in zip(("left", "top", "right", "bottom"), args.expand_percent)
        },
        "paddingPx": args.padding_px,
    }
    if args.crop_percent:
        crop["boundsPercent"] = {
            name: value for name, value in zip(("left", "top", "right", "bottom"), args.crop_percent)
        }
    document: dict[str, Any] = {
        "schemaVersion": "1.0",
        "input": str(args.input),
        "slides": args.slides,
        "crop": crop,
        "quality": {
            "referenceWidth": args.reference_width,
            "pdfMaxBytes": args.pdf_max_bytes,
            "pdfMaxImageDimension": args.pdf_max_image_dimension,
            "pdfJpegQuality": args.pdf_jpeg_quality,
            "svgMaxBytes": args.svg_max_bytes,
            "dpis": list(args.dpis),
            "svgWidths": list(args.svg_widths),
        },
        "behavior": {"strict": not args.no_strict, "dryRun": False, "progress": "none"},
    }
    if args.out:
        document["outputRoot"] = str(args.out)
    return document


def main(argv: list[str] | None = None) -> int:
    _configure_utf8_streams()
    parser = build_parser()
    raw_argv = list(argv) if argv is not None else sys.argv[1:]
    machine_hint = bool(raw_argv) and (
        raw_argv[0] == "job" or (raw_argv[0] == "export" and "--json" in raw_argv)
    )
    try:
        args = parser.parse_args(raw_argv)
    except InputError as exc:
        if machine_hint:
            result = _safe_failure(exc)
            _print_json(result)
            return int(result["exitCode"])
        print(f"{exc.code}: {exc}", file=sys.stderr)
        return exc.exit_code
    try:
        if args.command == "doctor":
            result = doctor()
            if args.json:
                print(json.dumps(result, ensure_ascii=False, indent=2))
            else:
                print(f"SlideGuard doctor: {'PASS' if result['ok'] else 'FAIL'}")
                print(f"PowerPoint: {result.get('powerpoint') or 'unavailable'}")
                for name, path in result["executables"].items():
                    print(f"{name}: {path}")
                for error in result["errors"]:
                    print(f"ERROR: {error}", file=sys.stderr)
            return 0 if result["ok"] else 20
        if args.command == "export":
            if args.json:
                return _run_machine(_document_from_export_args(args), base_dir=Path.cwd())
            options = ExportOptions(
                slides=args.slides, output_root=args.out, padding_px=args.padding_px,
                crop_percent=args.crop_percent, expand_percent=args.expand_percent,
                reference_width=args.reference_width, pdf_max_bytes=args.pdf_max_bytes,
                pdf_max_image_dimension=args.pdf_max_image_dimension,
                pdf_jpeg_quality=args.pdf_jpeg_quality, svg_max_bytes=args.svg_max_bytes, dpis=args.dpis,
                svg_widths=args.svg_widths, strict=not args.no_strict,
            )
            output, report = export_job(args.input, options)
            print(f"{report.verdict.value}: {output}")
            return 0 if report.verdict.value != "FAIL" else 50
        if args.command == "job":
            request_path = args.request
            if request_path == "-":
                try:
                    request_text = sys.stdin.read()
                except UnicodeError as exc:
                    result = _safe_failure(InputError("stdin is not valid UTF-8", stage="validation"))
                    _print_json(result)
                    return int(result["exitCode"])
                try:
                    document = load_request(request_text)
                except Exception as exc:
                    result = _safe_failure(exc)
                    _print_json(result)
                    return int(result["exitCode"])
                return _run_machine(document, base_dir=Path.cwd())
            path = Path(request_path).resolve()
            try:
                request_text = path.read_text(encoding="utf-8")
            except (OSError, UnicodeError) as exc:
                result = _safe_failure(InputError(f"Cannot read request file: {path}", stage="validation"))
                _print_json(result)
                return int(result["exitCode"])
            try:
                document = load_request(request_text)
            except Exception as exc:
                result = _safe_failure(exc)
                _print_json(result)
                return int(result["exitCode"])
            return _run_machine(document, base_dir=path.parent)
        if args.command == "gui":
            try:
                from .gui import run_gui
            except ImportError as exc:
                raise EnvironmentError(
                    "The visual interface is not installed; install SlideGuard with the gui extra",
                    stage="environment",
                ) from exc
            return run_gui(args.input)
        if args.command == "verify":
            verdict, findings = verify_package(args.manifest)
            print(json.dumps({"verdict": verdict.value, "findings": [asdict(item) for item in findings]}, ensure_ascii=False, default=str, indent=2))
            return 0 if verdict.value == "PASS" else 50
        if args.command == "fixtures":
            from .fixtures import build_core_fixture
            result = build_core_fixture(args.out)
            print(json.dumps(result, ensure_ascii=False, indent=2))
            return 0
    except SlideGuardError as exc:
        print(f"{exc.code}: {exc}", file=sys.stderr)
        return exc.exit_code
    except Exception as exc:
        print(f"UNEXPECTED_ERROR: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 70
    return 70
