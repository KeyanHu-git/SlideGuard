from __future__ import annotations

import json
import math
import os
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .geometry import NormalizedRect, validate_expansion_percent


DRAFT_SCHEMA_VERSION = 1


@dataclass(frozen=True, slots=True)
class EditorState:
    mode: str
    bounds_percent: tuple[float, float, float, float]
    expand_percent: tuple[float, float, float, float]
    padding_px: int
    limit_mb: float

    def __post_init__(self) -> None:
        if self.mode not in {"manual", "auto"}:
            raise ValueError(f"Unknown crop mode: {self.mode}")
        NormalizedRect.from_percent(self.bounds_percent)
        validate_expansion_percent(self.expand_percent)
        if isinstance(self.padding_px, bool) or not isinstance(self.padding_px, int) or self.padding_px < 0:
            raise ValueError("padding_px must be a non-negative integer")
        if not math.isfinite(self.limit_mb) or self.limit_mb <= 0:
            raise ValueError("limit_mb must be a positive finite number")

    def to_document(self) -> dict[str, Any]:
        return {
            "mode": self.mode,
            "boundsPercent": list(self.bounds_percent),
            "expandPercent": list(self.expand_percent),
            "paddingPx": self.padding_px,
            "limitMb": self.limit_mb,
        }

    @classmethod
    def from_document(cls, value: object) -> "EditorState":
        if not isinstance(value, dict):
            raise ValueError("editor must be an object")
        expected = {"mode", "boundsPercent", "expandPercent", "paddingPx", "limitMb"}
        if set(value) != expected:
            raise ValueError("editor fields do not match the draft schema")
        bounds = value["boundsPercent"]
        expand = value["expandPercent"]
        if not isinstance(bounds, list) or len(bounds) != 4:
            raise ValueError("boundsPercent must contain four numbers")
        if not isinstance(expand, list) or len(expand) != 4:
            raise ValueError("expandPercent must contain four numbers")
        return cls(
            mode=str(value["mode"]),
            bounds_percent=tuple(float(item) for item in bounds),
            expand_percent=tuple(float(item) for item in expand),
            padding_px=value["paddingPx"],
            limit_mb=float(value["limitMb"]),
        )


class EditHistory:
    """Small bounded history for crop settings; mouse drags are recorded by the caller."""

    def __init__(self, initial: EditorState, *, maximum: int = 100) -> None:
        if maximum < 2:
            raise ValueError("maximum history size must be at least two")
        self._maximum = maximum
        self._items = [initial]
        self._index = 0

    @property
    def can_undo(self) -> bool:
        return self._index > 0

    @property
    def can_redo(self) -> bool:
        return self._index + 1 < len(self._items)

    @property
    def current(self) -> EditorState:
        return self._items[self._index]

    def reset(self, state: EditorState) -> None:
        self._items = [state]
        self._index = 0

    def record(self, state: EditorState) -> bool:
        if state == self.current:
            return False
        del self._items[self._index + 1 :]
        self._items.append(state)
        if len(self._items) > self._maximum:
            del self._items[0]
        else:
            self._index += 1
        self._index = len(self._items) - 1
        return True

    def undo(self) -> EditorState | None:
        if not self.can_undo:
            return None
        self._index -= 1
        return self.current

    def redo(self) -> EditorState | None:
        if not self.can_redo:
            return None
        self._index += 1
        return self.current


@dataclass(frozen=True, slots=True)
class GuiDraft:
    source_path: str
    source_sha256: str
    slide: int
    editor: EditorState

    def __post_init__(self) -> None:
        if len(self.source_sha256) != 64 or any(char not in "0123456789abcdef" for char in self.source_sha256):
            raise ValueError("source_sha256 must be a lowercase SHA-256 digest")
        if isinstance(self.slide, bool) or not isinstance(self.slide, int) or self.slide < 1:
            raise ValueError("slide must be a positive integer")

    def to_document(self) -> dict[str, Any]:
        return {
            "draftSchemaVersion": DRAFT_SCHEMA_VERSION,
            "sourcePath": self.source_path,
            "sourceSha256": self.source_sha256,
            "slide": self.slide,
            "editor": self.editor.to_document(),
        }

    @classmethod
    def from_document(cls, value: object) -> "GuiDraft":
        if not isinstance(value, dict):
            raise ValueError("draft must be an object")
        expected = {"draftSchemaVersion", "sourcePath", "sourceSha256", "slide", "editor"}
        if set(value) != expected or value.get("draftSchemaVersion") != DRAFT_SCHEMA_VERSION:
            raise ValueError("unsupported GUI draft schema")
        return cls(
            source_path=str(value["sourcePath"]),
            source_sha256=str(value["sourceSha256"]),
            slide=value["slide"],
            editor=EditorState.from_document(value["editor"]),
        )


class GuiDraftStore:
    """Crash-only GUI drafts kept apart from export requests and output packages."""

    def __init__(self, root: Path) -> None:
        self.root = root

    def path_for(self, source_sha256: str) -> Path:
        if len(source_sha256) != 64 or any(char not in "0123456789abcdef" for char in source_sha256):
            raise ValueError("source_sha256 must be a lowercase SHA-256 digest")
        return self.root / f"{source_sha256}.json"

    def load(self, source_sha256: str) -> GuiDraft | None:
        path = self.path_for(source_sha256)
        if not path.is_file():
            return None
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
            draft = GuiDraft.from_document(value)
        except (OSError, UnicodeError, json.JSONDecodeError, TypeError, ValueError):
            try:
                path.unlink(missing_ok=True)
            except OSError:
                pass
            return None
        return draft if draft.source_sha256 == source_sha256 else None

    def save(self, draft: GuiDraft) -> Path:
        self.root.mkdir(parents=True, exist_ok=True)
        path = self.path_for(draft.source_sha256)
        temporary = self.root / f".{draft.source_sha256}.{uuid.uuid4().hex}.tmp"
        data = json.dumps(draft.to_document(), ensure_ascii=False, indent=2, allow_nan=False)
        try:
            temporary.write_text(data, encoding="utf-8")
            os.replace(temporary, path)
        finally:
            temporary.unlink(missing_ok=True)
        return path

    def discard(self, source_sha256: str) -> None:
        self.path_for(source_sha256).unlink(missing_ok=True)
