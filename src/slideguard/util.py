from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
import unicodedata
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable

from .errors import EnvironmentError, InputError


WINDOWS_RESERVED = {
    "CON", "PRN", "AUX", "NUL",
    *(f"COM{i}" for i in range(1, 10)),
    *(f"LPT{i}" for i in range(1, 10)),
}
MAX_SELECTED_SLIDES = 10_000


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with open(native_long_path(path), "rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def stable_json(data: object) -> str:
    return json.dumps(data, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def write_json(path: Path, data: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def safe_slug(value: str, fallback: str = "slide") -> str:
    value = unicodedata.normalize("NFC", value)
    value = re.sub(r"[<>:\"/\\|?*\x00-\x1f]", "-", value)
    value = re.sub(r"\s+", "-", value).strip(" .-")
    if not value:
        value = fallback
    if value.upper() in WINDOWS_RESERVED:
        value = f"_{value}"
    return value[:96]


def parse_slides(spec: str, maximum: int | None = None) -> list[int]:
    if not spec or spec.lower() == "all":
        if maximum is None:
            raise InputError("'all' requires a known slide count")
        return list(range(1, maximum + 1))
    result: list[int] = []
    for token in spec.split(","):
        token = token.strip()
        if not token:
            continue
        if "-" in token:
            parts = token.split("-", 1)
            if not all(part.isdigit() for part in parts):
                raise InputError(f"Invalid slide range: {token}")
            start, end = map(int, parts)
            if start > end:
                raise InputError(f"Descending slide range is not allowed: {token}")
            if maximum is not None and end > maximum:
                raise InputError(f"Slide {end} is outside 1..{maximum}")
            if end - start + 1 > MAX_SELECTED_SLIDES or len(result) + end - start + 1 > MAX_SELECTED_SLIDES:
                raise InputError(f"A request may select at most {MAX_SELECTED_SLIDES} slides")
            result.extend(range(start, end + 1))
        elif token.isdigit():
            if maximum is not None and int(token) > maximum:
                raise InputError(f"Slide {int(token)} is outside 1..{maximum}")
            if len(result) >= MAX_SELECTED_SLIDES:
                raise InputError(f"A request may select at most {MAX_SELECTED_SLIDES} slides")
            result.append(int(token))
        else:
            raise InputError(f"Invalid slide token: {token}")
    unique = list(dict.fromkeys(result))
    if not unique or min(unique) < 1:
        raise InputError("Slide numbers start at 1")
    if maximum is not None and max(unique) > maximum:
        raise InputError(f"Slide {max(unique)} is outside 1..{maximum}")
    return unique


def require_executable(name: str) -> str:
    path = shutil.which(name)
    if not path:
        raise EnvironmentError(f"Required executable not found: {name}")
    return str(Path(path).resolve())


def run_checked(command: list[str], *, timeout: int = 300, cwd: Path | None = None) -> subprocess.CompletedProcess[str]:
    process = subprocess.run(
        command,
        cwd=str(cwd) if cwd else None,
        text=True,
        encoding="utf-8",
        errors="replace",
        capture_output=True,
        timeout=timeout,
        check=False,
    )
    if process.returncode:
        joined = " ".join(command[:3])
        raise RuntimeError(f"Command failed ({process.returncode}): {joined}\n{process.stderr or process.stdout}")
    return process


def ensure_within(child: Path, parent: Path) -> None:
    child_abs = child.resolve()
    parent_abs = parent.resolve()
    if child_abs != parent_abs and parent_abs not in child_abs.parents:
        raise InputError(f"Path escapes the expected root: {child_abs}")


def checksum_lines(paths: Iterable[Path], base: Path) -> str:
    rows = []
    for path in sorted(paths, key=lambda item: item.as_posix().lower()):
        rows.append(f"{sha256_file(path)}  {path.relative_to(base).as_posix()}")
    return "\n".join(rows) + "\n"


def default_work_root() -> Path:
    base = os.environ.get("LOCALAPPDATA")
    if not base:
        raise EnvironmentError("LOCALAPPDATA is unavailable")
    # PowerPoint's Slide.Export still fails on paths that are valid for modern
    # Windows. Keep every COM-facing scratch path deliberately short and ASCII.
    return Path(base) / "SlideGuard" / "w"


def native_long_path(path: Path) -> str:
    """Return an absolute Win32 extended path so publication is not limited to MAX_PATH."""
    value = str(path.resolve())
    if os.name != "nt" or value.startswith("\\\\?\\"):
        return value
    if value.startswith("\\\\"):
        return "\\\\?\\UNC\\" + value[2:]
    return "\\\\?\\" + value
