from __future__ import annotations

import copy
import io
import json
import os
import sys
import tempfile
import warnings
from dataclasses import dataclass
from typing import Any, TextIO

from .privacy import REDACTED, redact_structure


_UNTRUSTED_FIELDS = frozenset(
    {
        "error",
        "errors",
        "message",
        "messages",
        "warning",
        "warnings",
        "reason",
        "detail",
        "details",
        "exception",
        "exceptions",
        "stderr",
        "stdout",
    }
)
_SECRET_FIELDS = frozenset(
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


class _BinaryNoiseSink:
    def __init__(self, owner: "_TextNoiseSink") -> None:
        self.owner = owner

    def write(self, data: bytes | bytearray) -> int:
        size = len(data)
        self.owner.byte_count += size
        return size

    def flush(self) -> None:
        return None


class _TextNoiseSink(io.TextIOBase):
    """A non-readable text stream that counts and discards untrusted output."""

    encoding = "utf-8"
    errors = "replace"

    def __init__(self, fd: int) -> None:
        super().__init__()
        self.fd = fd
        self.byte_count = 0
        self.buffer = _BinaryNoiseSink(self)

    def writable(self) -> bool:
        return True

    def write(self, text: str) -> int:
        value = str(text)
        self.byte_count += len(value.encode("utf-8", errors="replace"))
        return len(value)

    def flush(self) -> None:
        return None

    def isatty(self) -> bool:
        return False

    def fileno(self) -> int:
        return self.fd


@dataclass(slots=True)
class _FdCapture:
    fd: int
    saved_fd: int
    temporary: Any
    safe_stream: TextIO
    owns_safe_stream: bool


def _normalized_key(key: object) -> str:
    return "".join(character for character in str(key).casefold() if character.isalnum())


def _secret_field(key: object) -> bool:
    normalized = _normalized_key(key)
    return normalized in _SECRET_FIELDS or normalized.endswith("token") or normalized.endswith("password")


def sanitize_machine_document(document: Any) -> Any:
    """Redact untrusted diagnostic text without changing declared output paths."""

    def visit(value: Any) -> Any:
        if isinstance(value, dict):
            result = {}
            for key, item in value.items():
                normalized = _normalized_key(key)
                if _secret_field(key):
                    result[key] = REDACTED
                elif normalized in _UNTRUSTED_FIELDS:
                    result[key] = redact_structure(item)
                else:
                    result[key] = visit(item)
            return result
        if isinstance(value, list):
            return [visit(item) for item in value]
        if isinstance(value, tuple):
            return [visit(item) for item in value]
        return value

    return visit(copy.deepcopy(document))


class MachineOutputFirewall:
    """Keep library output away from a machine command's JSON stdout channel."""

    def __init__(self) -> None:
        self._original_stdout: TextIO | None = None
        self._original_stderr: TextIO | None = None
        self._stdout_sink = _TextNoiseSink(1)
        self._stderr_sink = _TextNoiseSink(2)
        self._fd_captures: dict[int, _FdCapture] = {}
        self.safe_stderr: TextIO | None = None
        self.stdout_bytes = 0
        self.stderr_bytes = 0

    @staticmethod
    def _stream_fd(stream: TextIO, expected: int) -> int | None:
        try:
            value = stream.fileno()
        except (AttributeError, io.UnsupportedOperation, OSError, ValueError):
            return None
        return value if value == expected else None

    def _capture_fd(self, fd: int, stream: TextIO) -> _FdCapture | None:
        try:
            stream.flush()
            saved_fd = os.dup(fd)
            temporary = tempfile.TemporaryFile(mode="w+b")
            os.dup2(temporary.fileno(), fd)
            if self._stream_fd(stream, fd) is not None:
                safe_binary = os.fdopen(os.dup(saved_fd), "wb", buffering=0)
                safe_stream = io.TextIOWrapper(safe_binary, encoding="utf-8", errors="strict", write_through=True)
                owns_safe_stream = True
            else:
                safe_stream = stream
                owns_safe_stream = False
            return _FdCapture(fd, saved_fd, temporary, safe_stream, owns_safe_stream)
        except (OSError, io.UnsupportedOperation):
            try:
                os.dup2(saved_fd, fd)
                os.close(saved_fd)
            except (NameError, OSError):
                pass
            try:
                temporary.close()
            except (NameError, OSError):
                pass
            return None

    def __enter__(self) -> "MachineOutputFirewall":
        self._original_stdout = sys.stdout
        self._original_stderr = sys.stderr
        self._warning_context = warnings.catch_warnings(record=True)
        self._warning_records = self._warning_context.__enter__()
        warnings.simplefilter("always")
        for fd, stream in ((1, self._original_stdout), (2, self._original_stderr)):
            captured = self._capture_fd(fd, stream)
            if captured is not None:
                self._fd_captures[fd] = captured
        self.safe_stderr = (
            self._fd_captures[2].safe_stream
            if 2 in self._fd_captures
            else self._original_stderr
        )
        sys.stdout = self._stdout_sink
        sys.stderr = self._stderr_sink
        return self

    @staticmethod
    def _temporary_bytes(capture: _FdCapture | None) -> int:
        if capture is None:
            return 0
        try:
            capture.temporary.flush()
            return int(capture.temporary.seek(0, os.SEEK_END))
        except OSError:
            return 0

    def __exit__(self, exc_type: Any, exc: Any, traceback: Any) -> None:
        self._warning_context.__exit__(exc_type, exc, traceback)
        sys.stdout = self._original_stdout  # type: ignore[assignment]
        sys.stderr = self._original_stderr  # type: ignore[assignment]
        self.stdout_bytes = self._stdout_sink.byte_count + self._temporary_bytes(self._fd_captures.get(1))
        self.stderr_bytes = self._stderr_sink.byte_count + self._temporary_bytes(self._fd_captures.get(2))
        self.stderr_bytes += sum(
            len(str(record.message).encode("utf-8", errors="replace"))
            for record in self._warning_records
        )
        for fd in (2, 1):
            capture = self._fd_captures.get(fd)
            if capture is None:
                continue
            try:
                if capture.owns_safe_stream:
                    capture.safe_stream.close()
            finally:
                try:
                    os.dup2(capture.saved_fd, fd)
                finally:
                    os.close(capture.saved_fd)
                    capture.temporary.close()
        self.safe_stderr = None

    def noise_event(self) -> dict[str, Any] | None:
        if self.stdout_bytes == 0 and self.stderr_bytes == 0:
            return None
        return {
            "schemaVersion": "1.0",
            "event": "output-firewall",
            "code": "OUTPUT_NOISE_SUPPRESSED",
            "suppressed": {
                "stdoutBytes": self.stdout_bytes,
                "stderrBytes": self.stderr_bytes,
            },
        }


def emit_noise_summary(firewall: MachineOutputFirewall, *, stream: TextIO | None = None) -> None:
    event = firewall.noise_event()
    if event is None:
        return
    destination = stream or sys.stderr
    payload = json.dumps(event, ensure_ascii=True, allow_nan=False, separators=(",", ":"))
    print(payload, file=destination, flush=True)
