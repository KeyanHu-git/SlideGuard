# Zero-egress contract

Status: required release contract for `v0.2.0-beta.1`.

## Default behavior

Every shipped runtime entry point is offline-only: GUI, `doctor`, `diagnose`, `export`, `verify`, `fixtures`, `job` and `batch`. SlideGuard does not collect telemetry, upload diagnostics, check for updates, resolve remote schemas or call an HTTP API. `diagnose` only writes a local JSON document after explicit consent.

The policy is compiled into the application and does not read a user configuration switch. A missing configuration, an inherited `HTTP_PROXY`/`HTTPS_PROXY`/`ALL_PROXY`, or a telemetry-shaped environment variable cannot enable network behavior.

The policy is represented by one machine-readable structure:

```json
{"version":"1.0","mode":"offline-only","telemetryEnabled":false,"automaticUploadsEnabled":false,"updateChecksEnabled":false}
```

`doctor --json` includes it as `networkPolicy`. Export QA reports inherit the same doctor record. A diagnostic bundle includes the policy under `safety.networkPolicy`.

## Fail-closed content boundary

SlideGuard rejects every OOXML relationship whose `TargetMode` is `External` before it starts the PowerPoint worker. This includes linked images, media, OLE targets and hyperlinks. Users must embed required content in the PPTX before export. This conservative rule prevents PowerPoint from being handed a document-authored remote target.

SVG parsing disables entity and network resolution. Script, event-handler and non-embedded resource references fail validation. GUI package opening is restricted to `QUrl.fromLocalFile`; the source audit rejects any other `openUrl` call.

## Reproducible proof

Run the deterministic source and dependency audit from the repository root:

```powershell
python scripts\audit_zero_egress.py
```

Success is one JSON object with `"verdict":"PASS"` and an empty `findings` array. The audit fails on runtime network imports, known network or telemetry dependencies, network-capable PowerShell commands, and non-local GUI URL opening.

Then run the controlled runtime suite:

```powershell
python -m pytest -q tests\test_zero_egress.py tests\test_ooxml_security.py
```

The tests replace DNS lookup and socket connection/send operations with a deny-and-record trap, then exercise all entry points. Any attempted Python network operation fails the test. CI runs both the static audit and the complete test suite on Python 3.10 and 3.12.

## Evidence boundary

These checks prove that SlideGuard's shipped Python and PowerShell runtime does not initiate network access and that document-authored external targets are rejected before PowerPoint opens the file. They do not claim that Windows, Microsoft Office licensing, antivirus, certificate services or other independently installed processes are silent. A clean-machine release qualification may additionally capture OS-level network events and attribute them by process, but such observations are environmental evidence rather than application behavior.

Future online functionality requires a separate opt-in executable path, a versioned policy change, threat review, tests that prove the offline default remains unchanged, and release notes. No such online path exists in this release.
