# Reproduction and pitfall record

This file records the failures found while turning the original scripts into SlideGuard. Keep every entry after the bug is fixed: the explanation and its regression test are part of the product.

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

## 14. A valid SVG can contain a very large embedded image attribute

Symptoms: the restored SVG is valid but XML parsing fails with `AttValue length too long` when one data URI contains roughly 10 MiB or more.

Cause: the parser's default attribute-size limit is smaller than legitimate PowerPoint-derived SVG output.

Fix: reject files larger than 256 MiB before parsing, pre-scan for DTD/ENTITY declarations, keep network and entity resolution disabled, and enable large-tree parsing only inside that explicit byte limit. External image URLs and active content remain forbidden.

Proof: a 10.5 MiB single-attribute fixture must pass; DTD, ENTITY and external-resource fixtures must still fail. Linear: KEY-181.

## 15. A PowerPoint worker can finish during the timeout grace period

Symptoms: the worker has already written a successful export and completed cleanup, yet the caller reports a timeout. A fixed result path can also expose a previous run's success.

Fix: every operation gets its own nonce directory. Result and status documents must carry the same nonce. After timeout, accept a late success only during a bounded grace period and only when `cleanupComplete` is true. Stale, partial or mismatched results fail closed.

Linear: KEY-180.

## 16. Machine-readable JSON can be corrupted by library noise

Symptoms: automation receives valid JSON preceded or followed by a warning, debug print or bytes written directly to file descriptor 1 or 2.

Fix: every machine-mode entry point runs behind a process-level output firewall. The command emits exactly one UTF-8 JSON document without BOM. Suppressed output is never replayed; only safe byte counts may be reported. Human help and GUI output remain readable.

Proof: fault injection covers Python text/buffer writes, warnings and native file-descriptor writes for job, batch, doctor, export, verify, fixtures and diagnose. Linear: KEY-160.

## 17. Generic environment-value redaction can corrupt filenames

Symptoms: when an ordinary environment variable has value `json`, `manifest.json` is rewritten into a redaction placeholder and package verification fails.

Fix: short generic environment values are not globally replaced. Values from sensitive variable names remain strictly redacted, and long token-like values remain protected. Structured fields such as code, stage and relative path must keep ordinary extensions intact.

Linear: KEY-184.

## 18. A single export must not cold-start PowerPoint twice

Symptoms: preflight starts PowerPoint once and the export immediately starts it again. The second COM activation may fail while Office is still recovering, even though the environment is healthy.

Fix: export preflight checks non-PowerPoint dependencies only. The actual export worker is the single proof of PowerPoint availability and returns the Office version for the environment report. Standalone `doctor` still performs the full PowerPoint probe.

Proof: a one-page job starts one PowerPoint worker and the real paper slide passes from the packaged executable. Linear: KEY-185.

## 19. Temporary directories are not safe to delete by name alone

Symptoms: crash residue accumulates under the work root, but a directory prefix cannot prove ownership; a junction or copied marker could redirect cleanup into user data.

Fix: cleanup requires an exact work-root fingerprint, full nonce, direct-child location, PID plus process-start token, and an owner marker that is read both before and immediately after deletion authorization. Missing, malformed, copied, symlinked, junction or reparse-point workspaces are retained and reported rather than deleted.

The startup scan classifies confirmed orphans separately from live, draft, failed-evidence and unverifiable directories. Linear: KEY-177 and KEY-178.

## 20. Diagnostic export must not become a data-exfiltration path

Symptoms: raw error objects can contain absolute paths, environment values, tokens, source names or original document content.

Fix: `slideguard diagnose` requires explicit `--consent`, reads only an allowlisted metadata contract, includes reports only by opt-in, applies redaction and secret scanning twice, writes atomically, stays at or below 256 KiB and never uploads. PPTX, PDF, SVG, images, base64 payloads and absolute paths are excluded.

Linear: KEY-168 through KEY-171.

## 21. A checkpoint write can fail only on a deep Windows path

Symptoms: the workspace is created successfully, but the first nonce-qualified checkpoint temporary file raises `FileNotFoundError` under a deep Chinese OneDrive path.

Cause: the workspace path is still below the traditional Win32 limit while the longer atomic-write filename crosses it.

Fix: create, flush, replace and clean the same-directory temporary file through a Win32 extended-length path. The final `job-state.json` remains in the owned workspace, so the atomic-replace boundary does not change. A failed write must leave the previous complete snapshot byte-for-byte intact.

Proof: the real export engine must persist a readable DISCOVER checkpoint before a deliberately injected preflight failure on a long Chinese path. Linear: KEY-195.

## 22. A resumable job must prove identity before reusing work

Symptoms: a directory or filename looks like a previous job, but the source content, crop request, pipeline contract or intermediate artifact has changed.

Fix: checkpoints bind the source SHA-256, normalized request fingerprint, schema/tool/pipeline versions, workspace nonce and owner marker. Every completed artifact records a canonical relative path, byte count and SHA-256. Stage transitions are monotonic and cannot skip prerequisites. Timestamps and attempt-specific nonces do not participate in the cross-run resume identity.

Never resume from filename, modification time, directory existence or a copied completion flag. Unknown major versions, absolute paths, reparse points, hash mismatches and impossible stage cursors fail closed. The deterministic reuse/recompute plan is tracked separately in KEY-174; the interruption matrix is KEY-176.

## 23. “Offline by default” needs both a product contract and release evidence

The runtime contract fixes telemetry, automatic uploads and update checks to false. PPTX external relationships and SVG external resources fail before PowerPoint or a renderer can fetch them. Static source auditing and socket/DNS denial tests cover every CLI, JSON and GUI entry point, including inherited proxy variables.

This proves the application implementation and CI behavior, but it does not substitute for an OS-level audit of a packaged release candidate on a clean Windows account. That release evidence must attribute Office licensing, Windows, antivirus and certificate traffic separately from the SlideGuard process tree. KEY-179 remains open until KEY-196 records that isolated-machine evidence.

## 24. `os.kill(pid, 0)` is not a read-only probe on Windows

Symptoms: a lease or cleanup test vanishes with `Aborted`, and the host UI labels the command “stopped” even though nobody pressed Stop.

Cause: `os.kill(pid, 0)` is a conventional existence probe on POSIX. On Windows, Python routes ordinary signal values through `TerminateProcess`; using zero can terminate the inspected process. A self-probe therefore kills the test runner itself.

Fix: the Windows branch opens a query-only process handle with `PROCESS_QUERY_LIMITED_INFORMATION`, reads `GetExitCodeProcess`, and closes the handle. It never calls `os.kill`. Access denied fails closed as “possibly alive” so it cannot authorize a lease takeover or workspace deletion.

Proof: a Windows-only regression replaces `os.kill` with an assertion failure and checks the current PID through the read-only branch. Linear: KEY-206.

## 25. An exited Windows process can still expose its creation time

Symptoms: a crashed resume writer has released its file lock, but a replacement is rejected as `RESUME_IN_PROGRESS` even after the original process exited.

Cause: an exited Windows process object can remain queryable while another handle is open. `GetProcessTimes` may therefore return the same creation token for a process that is no longer running. Token equality proves PID identity, not liveness.

Fix: test `GetExitCodeProcess == STILL_ACTIVE` first. Only a live PID proceeds to start-token comparison. A real two-process test makes one writer call `os._exit`, verifies that its stale lease remains, and then requires a new writer to take over without deleting or weakening the owner evidence.

## 26. A Chromium launcher can exit before its detached screenshot child

Symptoms: Edge returns exit code zero, but the SVG verification PNG is reported missing. On a later run the PNG may exist, yet cleanup fails because a cache journal inside the temporary browser profile is still locked.

Cause: on Windows, the Edge/Chrome launcher process is not a reliable completion boundary. A detached headless child can still be writing the screenshot or shutting down after the launcher exits.

Fix: keep the HTML source and unique browser profile alive until the PNG both fully decodes and has the exact expected dimensions. Then copy the completed evidence image and retry removal of the exact temporary profile while the child releases its handles. Browser background networking, sync and component updates are disabled; launcher success alone is never treated as render success.

Proof: unit tests cover delayed creation, incomplete/absent output, wrong dimensions and transient profile locks. The real compact SVG must render successfully at 640, 1600 and 3840 pixels. Linear: KEY-213.

## 27. Numeric controls and green test suites do not prove usable cropping

The 2026-09-05 GUI trial found a default 5% manual crop under an automatic-preset label,
invisible automatic output bounds, and settings that could change while an export used
an older snapshot. Do not resolve these defects by relabeling the same panel.

Studio starts in automatic mode and calls the existing crop detector on a 4000px
Office reference. Crop handles and margin sliders edit one CropSpec; display zoom
does not change it. Each gesture is one undo transaction. Source/parameter changes
are locked while workers run. Parameter validation, fidelity QA and package integrity
remain separate operations. Track regression coverage in KEY-240–248.

## 28. Check source bytes before assuming an import failure is an environment issue

Before this redesign, three uncommitted files had become entirely NUL-filled:
engine.py (23,221 bytes), execution.py (2,627 bytes), and test_execution.py (2,235 bytes).
All had modification time 2026-09-05 01:27:18. Python correctly rejected engine.py.
This is not an encoding diagnosis; no original source text remains in those bytes.

Preserve the files and request approval before replacing uncommitted work. Until
recovery is authorized, validate Studio in an isolated Git 4679dad snapshot with only
the new Studio changes overlaid. Report that baseline explicitly. Do not attribute
the corruption to OneDrive, a tool or a person without additional evidence. KEY-249.

## 29. QML syntax and zoomed backgrounds need real component tests

A semicolon after a JavaScript signal-handler block made the QML component fail to
load; the offscreen engine test caught it. Keep QML warnings and failed root-object
creation as test failures. Do not use an ever-growing Canvas bitmap for a checkerboard
at high zoom: tile a fixed 32px texture instead. Cap delivered-PDF image requests at
4096px per edge and disclose that cap; arbitrary high-zoom detail needs a later tiled
renderer, not a claim that stretching the current texture adds detail.

## 30. A longer test root exposed the legacy GUI draft path limit

The exact Studio commit was rerun under `studio-regression-final` instead of
`studio-regression`. Four old draft tests then failed: the draft temporary filename
combines a 64-character source hash and a UUID, crossing Win32 MAX_PATH under the
longer root. The GUI caught OSError and left no saved draft.

GuiDraftStore now uses the existing native_long_path helper for directory creation,
reads, temporary writes, atomic replace and deletion. Public logical paths are not
changed. A Windows test writes, reads and discards a draft beyond 260 characters.
The round-trip assertion compares canonical draft documents: serialization has always
added the active page to pageCrops, so raw dataclass equality was the wrong assertion
for an input with an empty page_crops tuple. Track the fix in KEY-253.
