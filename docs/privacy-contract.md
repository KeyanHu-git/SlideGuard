# SlideGuard privacy contract

Status: development contract for `v0.2.0-beta.1`.

SlideGuard keeps full local paths in its normal machine result because the desktop UI and local automation need to open the source and published package. That result is local data. Do not attach it unchanged to a GitHub issue, Linear issue, email or public bug report.

Use `redact_for_sharing` before creating a diagnostic attachment. It returns a new structure and does not change the local object.

## What a shared record keeps

A shared record keeps stable error codes, stages, exit codes, schema and tool versions, checksums, verdicts and relative evidence paths such as `svg/figure.svg`. It also keeps the final filename when an absolute path appears inside exception text. For example, `C:\Users\Alice\Lab\figure.pptx` becomes `<USER_DIR>\figure.pptx`.

## What it removes or masks

The share function removes machine-only fields whose names identify a source path, package path, output root, work directory, temporary directory, command line or PowerPoint worker path. Absolute drive paths, UNC paths, file URLs and common Unix home or temporary paths are masked when they appear elsewhere.

Credential-shaped dictionary fields are always replaced with `<REDACTED>`. The same rule covers bearer strings, API-key assignments, common GitHub/OpenAI/AWS token prefixes and JWTs inside exception messages. Environment variable values of four or more characters become `<ENV_VALUE>`.

The raw values below must stay local:

- `source.path` and legacy `source_path`
- `output.packagePath`, `outputRoot` and `config.output_root`
- work, temporary, cancellation, state and PowerPoint job/result paths
- executable paths, current working directory and command line
- unfiltered environment dictionaries and raw exception or traceback text
- request fields that contain credentials, access tokens, cookies or private keys

`manifestPath`, `reportPath`, artifact `relativePath`, error `code` and error `stage` are safe to keep after recursive redaction. A diagnostic builder must still run the secret-injection test before publication; a field name alone is not proof that its value is safe.
