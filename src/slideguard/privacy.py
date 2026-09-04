from __future__ import annotations

import ntpath
import os
import posixpath
import re
from collections.abc import Mapping, Sequence
from pathlib import Path, PurePath
from typing import Any


REDACTED = "<REDACTED>"
ENV_VALUE = "<ENV_VALUE>"
LOCAL_ONLY = "<LOCAL_ONLY>"
CYCLE = "<CYCLE>"

# These fields contain machine-local state and are omitted by redact_for_sharing.
# A relative `path` or `relativePath` is kept because it identifies evidence inside
# the package without disclosing a workstation directory.
LOCAL_ONLY_FIELDS = frozenset(
    {
        "sourcepath",
        "packagepath",
        "outputroot",
        "workdir",
        "workingdirectory",
        "tempdir",
        "temporarydir",
        "userprofile",
        "homedirectory",
        "cwd",
        "commandline",
        "executable",
        "executables",
        "jobjson",
        "resultpath",
        "referencepng",
        "nativepdf",
        "pptxpath",
        "cancelpath",
        "statepath",
        "workerpath",
    }
)

SECRET_FIELDS = frozenset(
    {
        "apikey",
        "accesstoken",
        "refreshtoken",
        "authtoken",
        "authorization",
        "bearertoken",
        "clientsecret",
        "password",
        "passwd",
        "privatekey",
        "secret",
        "sessioncookie",
        "credential",
        "credentials",
    }
)

STABLE_FIELDS = frozenset(
    {
        "code",
        "stage",
        "exitcode",
        "status",
        "schemaversion",
        "toolversion",
        "pipelinerevision",
        "verdict",
        "sha256",
        "configfingerprint",
    }
)
RELATIVE_PATH_FIELDS = frozenset({"relativepath", "manifestpath", "reportpath"})

_KEY_NORMALIZER = re.compile(r"[^a-z0-9]")
_USER_DIR_RE = re.compile(r"(?i)(?<![A-Za-z0-9])[A-Za-z]:\\Users\\[^\\/:*?\"<>|\r\n]+")
_WINDOWS_FILE_RE = re.compile(
    r"(?i)(?<![A-Za-z0-9])(?P<path>[A-Za-z]:\\(?:[^\\\r\n<>:\"|?*]+\\)*[^\\\r\n<>:\"|?*]*?\.[A-Za-z0-9]{1,12})"
)
_UNC_FILE_RE = re.compile(
    r"(?i)(?P<path>\\\\[^\\\s<>:\"|?*]+\\[^\\\r\n<>:\"|?*]+\\(?:[^\\\r\n<>:\"|?*]+\\)*[^\\\r\n<>:\"|?*]*?\.[A-Za-z0-9]{1,12})"
)
_POSIX_FILE_RE = re.compile(
    r"(?P<path>/(?:home|Users|tmp|var|opt|mnt|Volumes)/(?:[^/\s'\"<>|]+/)*[^/\s'\"<>|]+?\.[A-Za-z0-9]{1,12})"
)
_WINDOWS_REMAINDER_RE = re.compile(r"(?i)(?<![A-Za-z0-9])(?:[A-Za-z]:\\|\\\\)[^\r\n'\"<>|]+")
_POSIX_REMAINDER_RE = re.compile(r"(?<![A-Za-z0-9:])/(?:home|Users|tmp|var|opt|mnt|Volumes)/[^\r\n'\"<>|]+")
_FILE_URL_RE = re.compile(r"(?i)file:///(?:[A-Za-z]:/|/)[^\s'\"<>]+")

_ASSIGNED_SECRET_RE = re.compile(
    r"(?i)\b(api[-_ ]?key|access[-_ ]?token|refresh[-_ ]?token|auth[-_ ]?token|"
    r"client[-_ ]?secret|password|passwd|private[-_ ]?key|secret|credential|token|authorization)"
    r'''(\s*[:=]\s*)(?:['"])?([^\s,;}\])'"]+)'''
)
_BEARER_RE = re.compile(r"(?i)\b(Bearer)(\s+)[A-Za-z0-9._~+/=-]+")
_AUTHORIZATION_RE = re.compile(
    r"(?i)\bAuthorization\s*[:=]\s*(?:(?:Bearer|Basic|Token)\s+)?[A-Za-z0-9._~+/=-]+"
)
_COMMON_TOKEN_RE = re.compile(
    r"\b(?:gh[pousr]_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{20,}|"
    r"sk-[A-Za-z0-9_-]{20,}|AKIA[A-Z0-9]{16}|glpat-[A-Za-z0-9_-]{20,}|"
    r"xox[baprs]-[A-Za-z0-9-]{20,}|AIza[A-Za-z0-9_-]{20,})\b"
)
_JWT_RE = re.compile(r"\beyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\b")


def _normalized_key(key: object) -> str:
    return _KEY_NORMALIZER.sub("", str(key).casefold())


def _is_secret_field(key: object) -> bool:
    normalized = _normalized_key(key)
    return normalized in SECRET_FIELDS or normalized.endswith("token") or normalized.endswith("password")


def _is_local_only_field(key: object) -> bool:
    return _normalized_key(key) in LOCAL_ONLY_FIELDS


def _is_absolute_path(value: str) -> bool:
    return bool(
        re.match(r"(?i)^[A-Za-z]:[\\/]", value)
        or value.startswith("\\\\")
        or value.startswith("/")
        or value.casefold().startswith("file:///")
    )


def _path_placeholder(value: str) -> str:
    cleaned = value.rstrip("\\/")
    if value.casefold().startswith("file:///"):
        path_part = value[8:]
        name = posixpath.basename(path_part.rstrip("/"))
        return f"<FILE_URL>/{name}" if name else "<FILE_URL>"
    if value.startswith("\\\\"):
        name = ntpath.basename(cleaned)
        return f"<UNC_PATH>\\{name}" if name else "<UNC_PATH>"
    if re.match(r"(?i)^[A-Za-z]:[\\/]", value):
        name = ntpath.basename(cleaned.replace("/", "\\"))
        root = "<USER_DIR>" if re.search(r"(?i)^[A-Za-z]:\\Users\\", value.replace("/", "\\")) else "<ABS_PATH>"
        return f"{root}\\{name}" if name else root
    name = posixpath.basename(cleaned)
    return f"<ABS_PATH>/{name}" if name else "<ABS_PATH>"


def _redact_path_match(match: re.Match[str]) -> str:
    return _path_placeholder(match.group("path"))


def _environment_values(environ: Mapping[str, str] | None) -> list[str]:
    source = os.environ if environ is None else environ
    values = {str(value) for value in source.values() if isinstance(value, str) and len(value) >= 4}
    return sorted(values, key=len, reverse=True)


def redact_text(text: str, *, environ: Mapping[str, str] | None = None) -> str:
    """Remove workstation paths, credentials and environment values from text."""
    result = str(text)
    result = _AUTHORIZATION_RE.sub(f"Authorization: {REDACTED}", result)
    result = _BEARER_RE.sub(lambda match: f"{match.group(1)}{match.group(2)}{REDACTED}", result)
    result = _ASSIGNED_SECRET_RE.sub(lambda match: f"{match.group(1)}{match.group(2)}{REDACTED}", result)
    result = _COMMON_TOKEN_RE.sub(REDACTED, result)
    result = _JWT_RE.sub(REDACTED, result)
    result = _FILE_URL_RE.sub(lambda match: _path_placeholder(match.group(0)), result)
    result = _UNC_FILE_RE.sub(_redact_path_match, result)
    result = _WINDOWS_FILE_RE.sub(_redact_path_match, result)
    result = _POSIX_FILE_RE.sub(_redact_path_match, result)
    result = _USER_DIR_RE.sub("<USER_DIR>", result)
    result = _WINDOWS_REMAINDER_RE.sub("<ABS_PATH>", result)
    result = _POSIX_REMAINDER_RE.sub("<ABS_PATH>", result)
    for value in _environment_values(environ):
        if value in result:
            result = result.replace(value, ENV_VALUE)
    return result


def _redact_value(
    value: Any,
    *,
    environ: Mapping[str, str] | None,
    drop_local: bool,
    seen: set[int],
) -> Any:
    if value is None or isinstance(value, (bool, int, float)):
        return value
    if isinstance(value, (str, Path, PurePath)):
        text = str(value)
        if _is_absolute_path(text):
            return _path_placeholder(text)
        return redact_text(text, environ=environ)
    if isinstance(value, BaseException):
        identity = id(value)
        if identity in seen:
            return CYCLE
        seen.add(identity)
        result: dict[str, Any] = {
            "exceptionType": type(value).__name__,
            "message": redact_text(str(value), environ=environ),
        }
        linked = value.__cause__
        relation = "cause"
        if linked is None and not value.__suppress_context__:
            linked = value.__context__
            relation = "context"
        if linked is not None:
            result[relation] = _redact_value(
                linked,
                environ=environ,
                drop_local=drop_local,
                seen=seen,
            )
        seen.remove(identity)
        return result
    if isinstance(value, Mapping):
        identity = id(value)
        if identity in seen:
            return CYCLE
        seen.add(identity)
        result = {}
        for key, item in value.items():
            output_key = str(key)
            absolute_path_field = (
                _normalized_key(output_key) == "path"
                and isinstance(item, (str, Path, PurePath))
                and _is_absolute_path(str(item))
            )
            if drop_local and (_is_local_only_field(output_key) or absolute_path_field):
                continue
            if _is_secret_field(output_key):
                result[output_key] = REDACTED
                continue
            normalized_key = _normalized_key(output_key)
            if normalized_key in STABLE_FIELDS:
                result[output_key] = item
                continue
            if (
                normalized_key in RELATIVE_PATH_FIELDS
                and isinstance(item, (str, Path, PurePath))
                and not _is_absolute_path(str(item))
            ):
                result[output_key] = str(item)
                continue
            result[output_key] = _redact_value(
                item,
                environ=environ,
                drop_local=drop_local,
                seen=seen,
            )
        seen.remove(identity)
        return result
    if isinstance(value, Sequence) and not isinstance(value, (bytes, bytearray)):
        identity = id(value)
        if identity in seen:
            return CYCLE
        seen.add(identity)
        result = [
            _redact_value(item, environ=environ, drop_local=drop_local, seen=seen)
            for item in value
        ]
        seen.remove(identity)
        return result
    return redact_text(str(value), environ=environ)


def redact(value: Any, *, environ: Mapping[str, str] | None = None) -> Any:
    """Return a redacted copy without removing fields from the input structure."""
    return _redact_value(value, environ=environ, drop_local=False, seen=set())


def redact_for_sharing(value: Any, *, environ: Mapping[str, str] | None = None) -> Any:
    """Return a redacted copy and omit fields that only make sense on the source machine."""
    return _redact_value(value, environ=environ, drop_local=True, seen=set())


def redact_structure(value: Any, *, environ: Mapping[str, str] | None = None) -> Any:
    """Stable diagnostic-facing name for recursive redaction without field removal."""
    return redact(value, environ=environ)


def _scan_text(text: str, *, environ: Mapping[str, str] | None, found: set[str]) -> None:
    probe = text
    for placeholder in (REDACTED, ENV_VALUE, LOCAL_ONLY, CYCLE, "<USER_DIR>", "<ABS_PATH>", "<UNC_PATH>", "<FILE_URL>"):
        probe = probe.replace(placeholder, "")
    if _is_absolute_path(probe) or any(
        pattern.search(probe)
        for pattern in (
            _WINDOWS_FILE_RE,
            _UNC_FILE_RE,
            _POSIX_FILE_RE,
            _WINDOWS_REMAINDER_RE,
            _POSIX_REMAINDER_RE,
            _FILE_URL_RE,
        )
    ):
        found.add("absolute-path")
    if _USER_DIR_RE.search(probe):
        found.add("user-directory")
    if probe.startswith("\\\\") or _UNC_FILE_RE.search(probe):
        found.add("unc-path")
    if any(
        pattern.search(probe)
        for pattern in (_ASSIGNED_SECRET_RE, _AUTHORIZATION_RE, _BEARER_RE, _COMMON_TOKEN_RE, _JWT_RE)
    ):
        found.add("credential-text")
    if any(value in probe for value in _environment_values(environ)):
        found.add("environment-value")


def scan_secret_categories(value: Any, *, environ: Mapping[str, str] | None = None) -> list[str]:
    """Return sorted category names without returning any matched secret value."""
    found: set[str] = set()
    seen: set[int] = set()

    def visit(item: Any) -> None:
        if item is None or isinstance(item, (bool, int, float)):
            return
        if isinstance(item, (str, Path, PurePath)):
            _scan_text(str(item), environ=environ, found=found)
            return
        if isinstance(item, BaseException):
            identity = id(item)
            if identity in seen:
                return
            seen.add(identity)
            found.add("exception-text")
            _scan_text(str(item), environ=environ, found=found)
            if item.__cause__ is not None:
                visit(item.__cause__)
            elif not item.__suppress_context__ and item.__context__ is not None:
                visit(item.__context__)
            return
        if isinstance(item, Mapping):
            identity = id(item)
            if identity in seen:
                return
            seen.add(identity)
            for key, child in item.items():
                if _is_secret_field(key) and child != REDACTED:
                    found.add("credential-field")
                if _is_local_only_field(key):
                    found.add("local-only-field")
                visit(child)
            return
        if isinstance(item, Sequence) and not isinstance(item, (bytes, bytearray)):
            identity = id(item)
            if identity in seen:
                return
            seen.add(identity)
            for child in item:
                visit(child)
            return
        _scan_text(str(item), environ=environ, found=found)

    visit(value)
    return sorted(found)
