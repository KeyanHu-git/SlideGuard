# SlideGuard v0.2 interface contract

Status: development contract for `v0.2.0-beta.1`. The public JSON schema version is `1.0`.

## One core, three callers

The command line, JSON task interface and future desktop UI must produce the same normalized `ExportRequest` and `ExportOptions`. None of the callers may implement a second crop algorithm or invoke a private export stage directly.

The machine entry point is:

```powershell
slideguard job request.json
Get-Content request.json -Raw | slideguard job -
```

The first form resolves relative input and output paths from the request file's directory. The stdin form resolves them from the current working directory. Stdout contains exactly one compact `ExportResult` JSON document. When `behavior.progress` is `jsonl`, stderr contains only `ProgressEvent` JSON lines.

`slideguard export --json ...` is a convenience adapter for the same contract. The original `slideguard export ...` text output remains available for v0.1 users.

## Ordered batches

Use a `BatchRequest` when one caller needs several independent exports:

```powershell
slideguard batch batch-request.json
Get-Content batch-request.json -Raw | slideguard batch -
```

A batch contains 1 to 100 jobs. Each entry is checked as an `ExportRequest` when its turn starts, so one malformed job does not reject the other jobs. `results[n].itemIndex` is always `n`; the runner never reorders output.

`behavior.strategy` defaults to `continue`. It runs every job and returns `succeeded`, `partial` or `failed` from the child results. `fail-fast` stops after the first non-zero child result and adds an explicit `skipped` entry for every remaining job. The process exit code is zero only when every job succeeds or validates. Otherwise it is the largest child exit code; skipped entries do not invent another error code.

Retries are deliberately narrow. `maxAttempts` is 1 to 3 and `retryDelayMs` is 0 to 10,000 ms. SlideGuard retries only an environment or export failure that carries `details.transient: true`, or a failure whose stage is `powerpoint-timeout` or `external-process-timeout`. Input, size-budget, fidelity, idempotency and internal failures are never retried automatically. Each item records `attempts` and an `attemptLog` entry for every run.

An optional `idempotencyKey` may contain ASCII letters, digits, dots, underscores, colons and hyphens. Without one, SlideGuard derives a SHA-256 key from the source SHA-256, normalized content configuration and pipeline revision. A published result is reused only after SlideGuard checks the key binding, output-root boundary, manifest identity, every returned artifact's byte count and SHA-256, and every line in `checksums.sha256`. A caller-supplied key bound to different content returns `IDEMPOTENCY_CONFLICT` with exit code 31. If an old package is damaged, SlideGuard keeps it for inspection and writes the new run under a separate directory.

## Crop semantics

Crop coordinates are percentages of the complete slide, with the origin at the upper-left. A manual rectangle must satisfy `0 <= left < right <= 100` and `0 <= top < bottom <= 100`.

Expansion is applied after the manual or automatic content rectangle. Left and right expansion percentages use the selected rectangle's width; top and bottom use its height. Expansion is clamped to the slide. `paddingPx` is applied last in reference-render pixels. A UI preview must use this same normalized model and must not treat display pixels as export pixels.

## Configuration fingerprint

`configFingerprint` is SHA-256 over normalized values that can change the produced content or its QA. It includes the effective slide numbers, crop, image-quality budgets, validation scales and pipeline revision. It excludes `taskId`, `outputRoot`, strict publication behavior, dry-run and progress settings. Consequently, moving an otherwise identical job to another output folder does not change its content fingerprint.

The v0.1 internal `jobId` algorithm is not changed by this contract. A future change to that algorithm requires a pipeline revision and a migration note.

## Status and errors

`status` is one of `succeeded`, `validated` or `failed`. `validated` is a dry-run: it validates the schema, the PPTX package and effective slide selection without starting PowerPoint or publishing artifacts.

Errors carry a stable code, exit code, stage and structured details. Unknown error codes remain allowed so that adding a specific error in a compatible release does not invalidate old clients. Internal failures never include a traceback, environment variables or temporary-file contents in the machine result.

The JSON reader is strict: duplicate keys, `NaN`, `Infinity` and other non-standard constants are rejected. A request may select at most 10,000 slides, and a range is checked against the PPTX slide count before it is expanded in memory.

The result schema contains its error definition and can be validated offline as a single file. If a bundled schema or result serializer itself fails, the application service returns a small schema-independent `INTERNAL_ERROR` result instead of dropping machine output.

## Capability boundaries

SlideGuard preserves vector shapes when PowerPoint emits them as vector and restores original raster pixels when a safe match exists. It does not convert PNG, JPEG or screenshots into vector paths. SVG's canvas is transparent after the artificial PowerPoint page rectangle is removed; genuine white artwork remains genuine white artwork.

The authoritative schema files are shipped in `src/slideguard/schemas/`:

- `export-request.schema.json`
- `export-result.schema.json`
- `error.schema.json`
- `progress-event.schema.json`
- `batch-request.schema.json`
- `batch-result.schema.json`

## Compatibility

Clients must send a supported `schemaVersion` and must not send unknown properties. Additive optional result fields are compatible. Removing a field, changing its type or meaning, changing path resolution, or changing crop units requires a new schema major version. A new error code or progress phase is compatible.
