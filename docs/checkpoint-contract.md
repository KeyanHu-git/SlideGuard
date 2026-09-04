# Checkpoint contract

`job-state.json` is a private recovery record inside one verified SlideGuard export workspace. It is never written into the source PPTX or the published package.

## Identity and trust boundary

The schema binds a checkpoint to five independent facts:

- the owner marker's full 128-bit workspace nonce;
- the deterministic task ID and normalized request fingerprint;
- the source file name and SHA-256, without an absolute path;
- the SlideGuard version and pipeline revision;
- a `resumeKey` recomputed from the stable fields above, excluding the nonce and timestamps.

The nonce prevents a copied checkpoint from being trusted in another workspace. The stable `resumeKey` lets a later recovery planner compare two attempts with the same input. `writtenAt` is audit metadata only and must never participate in resume identity.

## Atomic persistence

Each update is serialized as strict UTF-8 JSON into a unique temporary file in the workspace, flushed and synchronized, then atomically replaces `job-state.json`. A process interruption can therefore expose the previous complete snapshot, the new complete snapshot, or an ignored temporary file; a partial temporary file is never read as a checkpoint. Write failures abort the export stage and preserve the last accepted snapshot.

On Windows, creation, replacement and cleanup of that temporary file use extended-length paths. This matters when a short SlideGuard work root is reached from a long localized test or OneDrive path; the safety contract must not disappear merely because the nonce-bearing temporary name crosses the legacy path limit.

The writer has exactly one destination name. It cannot overwrite `manifest.json`, the request, the source, or a published package.

## State machine

```text
DISCOVER
  → PREFLIGHT
  → INVENTORY
  → (NATIVE_EXPORT → PATCH → VALIDATE) × selected slide order
  → PACKAGE
  → PUBLISH/pending
  → PUBLISH/complete
```

Every state has a deterministic sequence number derived from the phase and slide cursor. A transition advances exactly one sequence. Per-slide cursors bind the output ordinal to the selected source slide. Only `PUBLISH` may be pending, and the top-level `complete` flag is true only for `PUBLISH/complete`.

Every reusable artifact is recorded by workspace-relative POSIX path, byte size and SHA-256. Absolute paths, parent traversal, Windows alternate-data-stream colons, links, junctions, reparse points, missing files, changed files, duplicate paths, unsorted records and artifacts from a future sequence fail closed.

## Stable failure codes

| Code | Meaning |
|---|---|
| `CHECKPOINT_READ_FAILED` | Missing, oversized, truncated, duplicate-key or non-UTF-8 JSON |
| `CHECKPOINT_WRITE_FAILED` | The durable temporary write or atomic replace failed |
| `CHECKPOINT_VERSION_UNSUPPORTED` | Missing, malformed or unsupported schema major |
| `CHECKPOINT_SCHEMA_INVALID` | The document and bundled JSON Schema disagree |
| `CHECKPOINT_IDENTITY_MISMATCH` | Nonce, task, source, request, tool or resume identity differs |
| `CHECKPOINT_PATH_UNSAFE` | A stored or actual path escapes the workspace trust boundary |
| `CHECKPOINT_ARTIFACT_INVALID` | A recorded artifact is missing, changed or not a regular file |
| `CHECKPOINT_TRANSITION_INVALID` | Phase, cursor, sequence, pending or completion semantics are inconsistent |

Reading and artifact verification are separate from deciding what can be resumed. The deterministic rules and machine fields are defined in [resume-plan-contract.md](resume-plan-contract.md). KEY-176 owns interruption injection across every boundary.
