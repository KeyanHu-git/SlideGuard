# SlideGuard machine output contract

Status: development contract for `v0.2.0-beta.1`.

Machine commands that return their result on stdout reserve it for one strict JSON document. This applies to `job`, `batch`, `export --json`, `doctor --json`, `verify`, `fixtures`, and `diagnose` without `--out`. `diagnose --out` writes the document to the requested file and leaves stdout empty. A future command that writes JSON must use the same output guard before release.

The guard starts before SlideGuard calls the export, PowerPoint, renderer, verifier or fixture code. Python `print`, warnings, writes through `sys.stdout.buffer`, and direct file-descriptor writes are discarded from the machine channel. The final serializer rejects `NaN` and `Infinity`; if serialization fails, it writes the command's small fallback error document.

Suppressed output is not replayed. If any library wrote to stdout or stderr, SlideGuard writes one safe JSON summary to stderr with byte counts only. It never copies the captured text, path or credential into that summary. Progress events use a separate safe stderr writer and remain JSONL.

Error messages and detail fields pass through the recursive privacy filter before serialization. Stable error codes and stages remain unchanged. Declared local result fields such as `source.path` and `output.packagePath` keep their contract meaning; callers must still treat the normal machine result as local data and run the sharing filter before attaching it to an issue.

Human help and normal text commands are outside this guard. `slideguard --help` stays readable, and `slideguard export` without `--json` keeps its one-line human result.
