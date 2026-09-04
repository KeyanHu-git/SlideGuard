# Reproduction and pitfall record

This file records the failures found while turning the original scripts into SlideGuard v0.1. Keep the entry when a bug is fixed. The regression test is part of the fix.

## 1. Browser SVG-to-PDF printing creates zoom-dependent seams

Symptoms: thin gray or white boundaries appear and disappear as the PDF zoom changes. They are often tile or transparency-group boundaries made by the browser renderer.

Fix: never print the final SVG through Chrome or Edge. Keep PowerPoint's native PDF page operators, replace only matched image streams, and change page boxes without rewriting the content stream.

Proof: pre/post content-stream SHA-256 must match. Multi-DPI residual-seam checks cover 72 through 600 DPI.

## 2. `ppPrintCurrent` silently ignores the requested slide range

Symptoms: page 2 and page 3 export as page 1. The command succeeds, so a single-page smoke test misses the bug.

Cause: `PpPrintRangeType` value 3 means `ppPrintCurrent`; value 4 means `ppPrintSlideRange`.

Fix: the PowerPoint worker passes 4 and the test suite rejects the old argument. The three-page fixture also requires distinct output hashes.

Linear: KEY-50.

## 3. PowerPoint COM rejects a valid long output path

Symptoms: `Slide.Export` or `ExportAsFixedFormat` fails under a deep OneDrive folder with spaces and Chinese characters.

Fix: PowerPoint writes to `%TEMP%\sg-<id>\r.png` and `n.pdf`. SlideGuard checks both files, then copies them to its job directory.

Linear: KEY-51.

## 4. A helper renderer decodes a UTF-8 path as GBK

Symptoms: path text becomes mojibake and the renderer reports that the PPTX does not exist.

Fix: SlideGuard does not use that helper for PowerPoint export. Its broker passes a UTF-8 JSON file to PowerShell and reads the result as UTF-8 with BOM tolerance.

## 5. Replacing original images lowers SSIM against the downsampled PDF

Symptoms: slide structure is identical, but a page with restored images scores lower than a page without images.

Cause: the comparison treats the intended sharpness increase as an error.

Fix: keep the exact operator hash as the hard geometry gate. Image-bearing pages use a documented SSIM floor of 0.85, keep MAE ≤ 0.08, and still run the same seam test.

## 6. Transparent PNGs carry arbitrary hidden RGB values

Symptoms: a visibly correct PNG match has correlation around 0.7 instead of 0.9.

Cause: PowerPoint separates RGB and alpha. Pixels hidden by alpha may contain any RGB value, so an RGB-only match penalizes invisible data.

Fix: compare premultiplied signatures and use 0.65 for PNG candidates. The accepted SVG must retain a mask for every alpha-bearing source asset.

## 7. A transparent canvas can render as fully opaque

Symptoms: an alpha check reports zero transparent pixels on a slide with a transparent root canvas.

Cause: full-bleed artwork covers the canvas.

Fix: inspect SVG structure and masks. The raster alpha count is evidence, not a failure by itself.

## 8. Pytest scans unrelated desktop research code

Symptoms: running tests from the desktop imports another project's `test_*.py` and can crash inside an unrelated dependency.

Fix: `pyproject.toml` limits discovery to `tests`, the documented command names that directory, and the temporary test root stays under `.tmp/pytest`.

## 9. Failed runs leave useful evidence and also consume disk space

Successful jobs remove their scratch directory. Failed strict jobs keep one folder under `%LOCALAPPDATA%\SlideGuard\w` so the report can be inspected. Delete only confirmed old failed-job folders after copying needed evidence; never clean the entire user temp or OneDrive tree as part of export.

## 10. CairoSVG shows black or white rectangles around PowerPoint effects

Symptoms: the SVG is correct in Edge, but automated PNG renders contain large rectangular blocks around shadows, grouped pictures or transparency effects.

Cause: CairoSVG and Chromium do not interpret every luminosity-mask/filter combination emitted by `pdftocairo` identically.

Fix: on Windows, render SVG evidence with installed Edge or Chrome in headless mode, on an explicitly transparent canvas. Keep `resvg` and CairoSVG only as fallbacks. This browser use is raster verification only; SVG-to-PDF printing remains forbidden because it creates the seam problem in pitfall 1.

Proof: composite the transparent browser render on white and compare it with PowerPoint's own white reference at 640, 1600 and 3840 px.

## 11. A timed-out COM call can outlive the Python request

Cause: PowerPoint export is a blocking COM call. Stopping the Python or PowerShell caller does not prove that the PowerPoint process belongs to SlideGuard, and ending every `POWERPNT.EXE` process can destroy a user's unsaved work.

Fix: every worker writes an early nonce-bound status file. A PowerPoint PID is treated as SlideGuard-owned only when it was absent before COM activation, started inside that activation window, uses Office's `/AUTOMATION -Embedding` command line, and is the only matching new process. On timeout, SlideGuard writes a cancellation token and waits briefly. If the worker used an existing or unproven session, it remains alive long enough to close SlideGuard's hidden read-only presentation after the blocking call returns. SlideGuard does not stop that PowerPoint process.

Forced cleanup is limited to a PID from a valid current-worker handshake. A second script checks the PID, start time, process name and recorded identity method immediately before calling `Stop-Process -Id`. Name-based process termination is forbidden. The timeout error records whether cleanup completed and whether a worker may still be finishing in the background.

Proof: run the ownership fault tests in `tests/test_powerpoint.py`, then run a real `probe` once with PowerPoint closed and once while a user session is open. The first run must leave no PowerPoint process. The second must preserve the original PID.

## 12. Publishing succeeds locally but fails in a deep OneDrive folder

Symptoms: QA passes, but copying a few long-named evidence files reports `WinError 3` or appears missing during package verification.

Cause: the final path exceeds the legacy Win32 `MAX_PATH` boundary even though PowerPoint export already used a short scratch path.

Fix: use a short atomic staging name and Win32 extended-length paths for publication, existence checks and checksum reads. Do not shorten or silently omit evidence filenames.

## 13. Tests pass locally but fail on a clean GitHub runner

Symptoms: tests using pytest's `tmp_path` fail before setup because `.tmp/pytest` cannot be created.

Cause: `--basetemp=.tmp/pytest` assumes its parent directory already exists. A developer checkout may contain ignored `.tmp`, while a clean CI checkout does not.

Fix: use a single repository-root temporary directory such as `--basetemp=.pytest-tmp` and ignore it. CI must test a clean checkout, not only a working directory with residual folders.
