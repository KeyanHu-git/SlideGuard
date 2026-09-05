# Resume plan contract

`resume-plan` reads one owned export workspace and answers a narrow question: which completed steps are still safe to reuse? It does not modify `job-state.json`, delete the old workspace, run PowerPoint or publish a package.

## One task model

Export execution and recovery planning both use `ExportTaskModel`. The model derives the source SHA-256, selected slides, normalized request fingerprint, job ID, output root and final directory with the same code path. The JSON API, command line adapter and desktop worker all call `ResumePlanningService`; none of them interprets a checkpoint on its own.

Run the machine form with an existing `ExportRequest`:

```powershell
slideguard resume-plan request.json --workspace "C:\path\to\owned-workspace" --json
Get-Content request.json -Raw | slideguard resume-plan - --workspace "C:\path\to\owned-workspace" --json
```

Leave off `--json` for a short text view. Both forms use the same plan document and reason codes. A resumable plan exits with code 0. A valid request that points to an unusable checkpoint returns a plan with `status: rejected` and exit code 40.

## Decision order

The planner checks these facts in order:

1. The workspace is one plain direct child of its marker-bound root. The full nonce, task ID and workspace kind must match.
2. `job-state.json` must be strict UTF-8 JSON accepted by the bundled schema and state machine.
3. Source name, source SHA-256, request fingerprint, tool version, pipeline revision, selected slides, task ID and resume key must match the current task model.
4. Every recorded artifact is checked by workspace-relative path, byte count and SHA-256. A filename, directory entry or modification time is never enough.
5. Each reached stage must contain its required artifact records. Package reuse also runs the existing package verifier against `manifest.json` and `checksums.sha256`.
6. Publication stays outside the reusable prefix. The planner always returns `publish-atomically` for a resumable job.

The plan has a `planKey` calculated from the complete plan except that key itself. It contains no wall-clock time, absolute source path or modification time. Running the planner twice against unchanged bytes returns the same document.

## Stage prerequisites

| Stage | Required proof |
|---|---|
| `DISCOVER` | Valid checkpoint identity and sequence |
| `PREFLIGHT` | Valid prior sequence |
| `INVENTORY` | Valid prior sequence and unchanged source identity |
| `NATIVE_EXPORT` | One `native-pdf` and one `reference-png` record for that slide |
| `PATCH` | `pdf`, `raw-svg` and `svg`; also `svg-compact` when the request sets an SVG size limit |
| `VALIDATE` | One accepted `png` plus at least one `evidence` record |
| `PACKAGE` | Recorded manifest, JSON report, HTML report, JUnit report and checksum file; package verification must pass |
| `PUBLISH/pending` | Never reusable; resume starts with a fresh atomic publish attempt |
| `PUBLISH/complete` | Never trusted as a resume source |

The state machine is linear. If an artifact at sequence 4 has a wrong hash, sequences 0 through 3 may be reused. Sequence 4 and every later sequence are marked `recompute`, even when a later file still matches its stored hash. This keeps prerequisite meaning clear and avoids stitching together two inconsistent attempts.

## Hard rejection

The planner rejects the whole workspace when identity, schema or ownership cannot be proved. It also rejects any link, junction or reparse point inside the package tree. A checkpoint that claims `PUBLISH/complete` is not proof of publication.

An existing final directory with the same job ID is another hard stop. The planner does not inspect it to decide that it is "probably the same," and it never overwrites it. The old workspace and formal directory remain untouched for diagnosis.

## Machine fields

`resume-plan.schema.json` defines the result. The important fields are:

- `status`: `resumable` or `rejected`;
- `planKey`: SHA-256 identity of the plan;
- `reusedThroughSequence`: last contiguous reusable sequence;
- `resumeFromSequence`: first sequence that must run again;
- `steps[].action`: `reuse`, `recompute` or `reject`;
- `steps[].reasonCode`: stable reason for that action;
- `steps[].artifacts`: stored and actual byte/hash results;
- `steps[].requirements`: required kind or path and the matched count;
- `workspace.disposition`: always `retained` during planning;
- `publication.action`: `publish-atomically` or `reject`.

No caller should infer more from a file's name, timestamp or presence than the plan states.
