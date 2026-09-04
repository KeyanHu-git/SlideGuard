# SlideGuard

SlideGuard exports a PowerPoint slide to PDF, SVG and PNG without redrawing the slide. It asks the installed Windows version of PowerPoint to render the page, restores original embedded images where a match is safe, crops excess margins, and runs deterministic checks before it publishes anything.

The project grew out of a paper figure that looked correct in PowerPoint but failed in several ways after export: dashed lines became solid, shadows changed, pictures turned soft, a white page appeared behind the SVG, and PDF viewers showed hairline seams at some zoom levels. SlideGuard treats each one as a testable failure rather than a visual judgement call.

## What it protects

- PowerPoint's PDF drawing instruction stream must remain byte-identical after image restoration.
- Dashed lines, thin lines, text, shapes, gradients, shadows and z-order stay under PowerPoint's renderer.
- Original PNG/JPEG assets are restored only when content matching clears a fixed threshold.
- SVG files contain no script or external resource and do not keep PowerPoint's artificial white page rectangle.
- PDF page boxes use one shared crop. Multi-scale renders are checked for residual seams.
- The source `.pptx` is opened read-only and its SHA-256 must be unchanged at the end.
- A size limit is strict. If no candidate fits, the command fails instead of flattening the page or quietly lowering quality.

PowerPoint shapes and text remain vector where PowerPoint emits them as vector. A PNG, JPEG or screenshot inside the slide is still a bitmap. SlideGuard can preserve its original pixels and alpha channel, but it cannot turn those pixels into real vector paths.

## Requirements

- Windows 10 or 11
- Desktop Microsoft PowerPoint
- Python 3.10 or newer
- Poppler commands `pdftocairo`, `pdftoppm` and `pdfinfo` on `PATH`
- Microsoft Edge, Chrome or another Chromium-family browser for PowerPoint-compatible SVG mask rendering. `resvg` and CairoSVG remain fallbacks.

Install the Python package from this checkout:

```powershell
py -m pip install -e ".[qa]"
slideguard doctor
```

## One-click use

Drag a `.pptx` file onto `SlideGuard.cmd`. The launcher exports slide 1 with PDF and compact-SVG limits of less than 2,500,000 bytes. It also keeps the full-size SVG. The output goes into a new `slideguard-output` folder beside the presentation.

The command-line form gives full control:

```powershell
slideguard export "figure.pptx" --slides 1 --pdf-max-bytes 2500000 --svg-max-bytes 2500000
slideguard export "deck.pptx" --slides 1,3-5
slideguard export "deck.pptx" --slides all --out "D:\exports"
```

The v0.2 development branch also has a visual Windows interface:

```powershell
py -m pip install -e ".[qa,gui]"
slideguard gui
```

Open or drag in a PPTX, select the page, drag any of the four edges or four corners, and set each expansion edge independently. The blue line is the manual crop; the green dashed line is the effective output after expansion and fixed reference-pixel padding. Built-in choices cover tight crop, a 2% paper-safe margin plus 16 reference pixels, and the full page. You can save a named custom preset, keep a different crop on each page, or copy the current page's crop to a page range such as `2,4-6`. Every one of these controls writes the same `CropSpec`, which the visual interface passes to the same application service as the JSON entry point. See [docs/gui-crop-presets.md](docs/gui-crop-presets.md) for the stored formats and exact values. Safe mid-export cancellation and the installer remain release blockers and are not represented as finished features.

SlideGuard serializes its own PowerPoint workers. If PowerPoint is already open, the worker does not quit that process: it opens the requested file read-only without a window, closes only that copy, and restores the previous automation-security setting. Mid-call timeout recovery still requires the Office runner gate before beta release.

Automation and AI callers should use the versioned JSON interface. It writes exactly one result document to stdout and resolves paths in a JSON file relative to that file:

```powershell
slideguard job request.json
slideguard export "figure.pptx" --slides 1 --json
slideguard batch examples\batch-request.json
```

Set `behavior.dryRun` to `true` to validate the request, PPTX package and slide selection without opening PowerPoint or publishing files. The shipped request, result, error and progress schemas are documented in [docs/interface-contract-v0.2.md](docs/interface-contract-v0.2.md).

Machine commands keep stdout to one strict JSON document. Output accidentally written by PowerPoint helpers, renderers, warnings, or native libraries is discarded; stderr receives only a small JSON summary with byte counts. Error text is redacted before serialization. `diagnose --out` is the exception: it writes the result to the named file and leaves stdout empty. See [docs/machine-output-contract.md](docs/machine-output-contract.md) for the command matrix and trust boundary.

SlideGuard is offline-only by default. It contains no telemetry, automatic upload or update-check path, and every runtime entry point uses the same policy. A PPTX with any external OOXML relationship is rejected before PowerPoint opens it; exported SVG also rejects external resources. `slideguard doctor --json` records the policy in `networkPolicy`, and CI runs a static dependency/source audit plus socket-denial tests. See [docs/zero-egress-contract.md](docs/zero-egress-contract.md) for the exact boundary and reproducible checks.

The batch entry accepts 1 to 100 independent requests and keeps output in input order. Its default `continue` strategy isolates a bad job and runs the rest. `fail-fast` leaves explicit skipped records. Safe reuse needs both the same source SHA-256 and the same normalized configuration; SlideGuard checks the published manifest, artifact hashes and `checksums.sha256` before returning a cached result. See [examples/batch-request.json](examples/batch-request.json) for a complete request.

To crop like PowerPoint, give the four boundary positions as percentages of the slide. This keeps the area from 5% to 95% horizontally and 3% to 97% vertically:

```powershell
slideguard export "figure.pptx" --crop-percent 5,3,95,97
```

`--expand-percent 2` adds 2% of the selected content width or height outside every edge. Four values set left, top, right and bottom separately. Pixel padding is applied last and can be set to zero for an exact manual boundary:

```powershell
slideguard export "figure.pptx" --crop-percent 5,3,95,97 --expand-percent 1,2,1,2 --padding-px 0
```

Each accepted package contains:

```text
<job-id>/
├── *.pdf
├── svg/*.svg
├── svg-compact/*.svg
├── png/*.png
├── evidence/*
├── manifest.json
├── qa-report.json
├── report.html
├── junit.xml
└── checksums.sha256
```

Open `report.html` for the readable result. CI systems can use `junit.xml`; scripts should use `qa-report.json`. To check that a package has not changed:

```powershell
slideguard verify "D:\exports\<job-id>\manifest.json"
```

## Transparency and PDF seams

SVG has a transparent canvas after export. White rectangles that are real slide objects are retained. A full-bleed design may still look opaque because its artwork covers every pixel, even though the SVG root has no background.

PDF has no transparent page concept in the same sense. Viewers normally composite a PDF page on white. SlideGuard keeps PowerPoint's native page drawing instructions and only replaces safe image streams, which avoids the tile boundaries commonly introduced by browser SVG-to-PDF printing.

## Test fixtures

Generate the local PowerPoint torture deck, then run all pages:

```powershell
slideguard fixtures --out .tmp\fixtures
slideguard export .tmp\fixtures\slideguard-core-torture.pptx --slides all --out .tmp\fixture-output
py -m pytest -q tests
```

The deck covers dash patterns, 0.25 pt lines, shadows, gradients, alpha PNGs, crop, rotation, overlap, dark backgrounds and full-bleed adjacency. Fault-injection tests also create known-bad SVGs so that a validator must prove it can fail before its PASS result is trusted.

## Limits in v0.1

- PowerPoint is the rendering authority, so headless Linux export is not supported.
- Hosted GitHub Actions can run unit and fault-injection tests, but PowerPoint integration tests need a Windows machine with Office.
- The renderer does not claim pixel identity across unrelated PDF/SVG engines. On Windows it prefers Chromium because CairoSVG can misinterpret some PowerPoint/PDF luminosity masks. It checks exact structure where exactness is possible and fixed visual tolerances where raster comparison is the only practical test.
- Password-protected files, linked media and unsupported OLE objects fail or produce an explicit coverage warning.

See [docs/architecture.md](docs/architecture.md), [docs/quality-contract.md](docs/quality-contract.md) and [docs/reproduction-and-pitfalls.md](docs/reproduction-and-pitfalls.md) for the design and the failure record.

## License

SlideGuard's own source is MIT licensed. PowerPoint and Poppler are external programs with their own terms; they are not bundled in the repository.
