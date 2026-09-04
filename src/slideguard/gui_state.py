from __future__ import annotations

import json
import math
import os
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

from .geometry import NormalizedRect, validate_expansion_percent


DRAFT_SCHEMA_VERSION = 2
PRESET_SCHEMA_VERSION = 1
_CROP_FIELDS = {"mode", "boundsPercent", "expandPercent", "paddingPx"}
_EDGE_NAMES = ("left", "top", "right", "bottom")


@dataclass(frozen=True, slots=True)
class CropSpec:
    """The only crop value written by GUI controls, presets and page assignments."""

    mode: str
    bounds_percent: tuple[float, float, float, float]
    expand_percent: tuple[float, float, float, float]
    padding_px: int

    def __post_init__(self) -> None:
        if self.mode not in {"manual", "auto"}:
            raise ValueError(f"Unknown crop mode: {self.mode}")
        NormalizedRect.from_percent(self.bounds_percent)
        validate_expansion_percent(self.expand_percent)
        if isinstance(self.padding_px, bool) or not isinstance(self.padding_px, int) or self.padding_px < 0:
            raise ValueError("padding_px must be a non-negative integer")

    def to_document(self) -> dict[str, Any]:
        return {
            "mode": self.mode,
            "boundsPercent": dict(zip(_EDGE_NAMES, self.bounds_percent)),
            "expandPercent": dict(zip(_EDGE_NAMES, self.expand_percent)),
            "paddingPx": self.padding_px,
        }

    def to_request_document(self) -> dict[str, Any]:
        document = self.to_document()
        if self.mode == "auto":
            document.pop("boundsPercent")
        return document

    @classmethod
    def from_document(cls, value: object) -> "CropSpec":
        if not isinstance(value, dict) or set(value) != _CROP_FIELDS:
            raise ValueError("crop fields do not match the GUI crop schema")
        bounds = _edge_tuple(value["boundsPercent"], "boundsPercent")
        expand = _edge_tuple(value["expandPercent"], "expandPercent")
        return cls(
            mode=str(value["mode"]),
            bounds_percent=bounds,
            expand_percent=expand,
            padding_px=value["paddingPx"],
        )


def _edge_tuple(value: object, field: str) -> tuple[float, float, float, float]:
    if not isinstance(value, dict) or set(value) != set(_EDGE_NAMES):
        raise ValueError(f"{field} must contain left, top, right and bottom")
    return tuple(float(value[name]) for name in _EDGE_NAMES)


@dataclass(frozen=True, slots=True)
class CropPreset:
    preset_id: str
    name: str
    crop: CropSpec
    built_in: bool = False

    def __post_init__(self) -> None:
        if not self.preset_id or len(self.preset_id) > 100:
            raise ValueError("preset_id must contain 1 to 100 characters")
        if not self.name.strip() or len(self.name.strip()) > 80:
            raise ValueError("preset name must contain 1 to 80 characters")

    def to_document(self) -> dict[str, Any]:
        return {"id": self.preset_id, "name": self.name.strip(), "crop": self.crop.to_document()}

    @classmethod
    def from_document(cls, value: object) -> "CropPreset":
        if not isinstance(value, dict) or set(value) != {"id", "name", "crop"}:
            raise ValueError("preset fields do not match the GUI preset schema")
        return cls(
            preset_id=str(value["id"]),
            name=str(value["name"]),
            crop=CropSpec.from_document(value["crop"]),
        )


BUILT_IN_CROP_PRESETS = (
    CropPreset(
        "builtin:tight",
        "自动紧边",
        CropSpec("auto", (0.0, 0.0, 100.0, 100.0), (0.0, 0.0, 0.0, 0.0), 0),
        built_in=True,
    ),
    CropPreset(
        "builtin:paper-safe",
        "论文安全边距",
        CropSpec("auto", (0.0, 0.0, 100.0, 100.0), (2.0, 2.0, 2.0, 2.0), 16),
        built_in=True,
    ),
    CropPreset(
        "builtin:full-page",
        "整页",
        CropSpec("manual", (0.0, 0.0, 100.0, 100.0), (0.0, 0.0, 0.0, 0.0), 0),
        built_in=True,
    ),
)


class CropPresetStore:
    """Atomic local storage for named custom CropSpec values."""

    def __init__(self, path: Path) -> None:
        self.path = path

    def load(self) -> tuple[CropPreset, ...]:
        if not self.path.is_file():
            return ()
        try:
            value = json.loads(self.path.read_text(encoding="utf-8"))
            if not isinstance(value, dict) or set(value) != {"presetSchemaVersion", "presets"}:
                raise ValueError("preset document fields do not match the schema")
            if value["presetSchemaVersion"] != PRESET_SCHEMA_VERSION or not isinstance(value["presets"], list):
                raise ValueError("unsupported preset schema")
            presets = tuple(CropPreset.from_document(item) for item in value["presets"])
            if len(presets) > 200:
                raise ValueError("at most 200 custom presets are allowed")
            if any(not item.preset_id.startswith("custom:") for item in presets):
                raise ValueError("stored preset ids must use the custom namespace")
            built_in_names = {item.name.casefold() for item in BUILT_IN_CROP_PRESETS}
            if any(item.name.casefold() in built_in_names for item in presets):
                raise ValueError("custom preset names must not shadow built-in presets")
            if len({item.preset_id for item in presets}) != len(presets):
                raise ValueError("preset ids must be unique")
            if len({item.name.casefold() for item in presets}) != len(presets):
                raise ValueError("preset names must be unique")
            return presets
        except (OSError, UnicodeError, json.JSONDecodeError, TypeError, ValueError):
            return ()

    def save(self, name: str, crop: CropSpec) -> CropPreset:
        clean_name = name.strip()
        if not clean_name or len(clean_name) > 80:
            raise ValueError("preset name must contain 1 to 80 characters")
        if clean_name.casefold() in {item.name.casefold() for item in BUILT_IN_CROP_PRESETS}:
            raise ValueError("built-in preset names are reserved")
        presets = list(self.load())
        existing = next((item for item in presets if item.name.casefold() == clean_name.casefold()), None)
        preset = CropPreset(existing.preset_id if existing else f"custom:{uuid.uuid4().hex}", clean_name, crop)
        if existing:
            presets[presets.index(existing)] = preset
        else:
            if len(presets) >= 200:
                raise ValueError("at most 200 custom presets are allowed")
            presets.append(preset)
        self._write(presets)
        return preset

    def delete(self, preset_id: str) -> bool:
        presets = list(self.load())
        kept = [item for item in presets if item.preset_id != preset_id]
        if len(kept) == len(presets):
            return False
        self._write(kept)
        return True

    def _write(self, presets: Iterable[CropPreset]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.parent / f".{self.path.name}.{uuid.uuid4().hex}.tmp"
        document = {
            "presetSchemaVersion": PRESET_SCHEMA_VERSION,
            "presets": [item.to_document() for item in presets],
        }
        data = json.dumps(document, ensure_ascii=False, indent=2, allow_nan=False)
        try:
            temporary.write_text(data, encoding="utf-8")
            os.replace(temporary, self.path)
        finally:
            temporary.unlink(missing_ok=True)


class PageCropAssignments:
    """CropSpec values keyed by slide number, independent of any page-list widget."""

    def __init__(self, items: Iterable[tuple[int, CropSpec]] = ()) -> None:
        self._items: dict[int, CropSpec] = {}
        for slide, crop in items:
            self.save(slide, crop)

    def save(self, slide: int, crop: CropSpec) -> None:
        if isinstance(slide, bool) or not isinstance(slide, int) or slide < 1:
            raise ValueError("slide must be a positive integer")
        if not isinstance(crop, CropSpec):
            raise TypeError("crop must be a CropSpec")
        self._items[slide] = crop

    def get(self, slide: int, fallback: CropSpec) -> CropSpec:
        return self._items.get(slide, fallback)

    def copy(self, source_slide: int, target_slides: Iterable[int]) -> tuple[int, ...]:
        if source_slide not in self._items:
            raise ValueError("source slide has no saved CropSpec")
        copied: list[int] = []
        for slide in target_slides:
            if isinstance(slide, bool) or not isinstance(slide, int) or slide < 1:
                raise ValueError("slide must be a positive integer")
            if slide not in copied:
                copied.append(slide)
        for slide in copied:
            self._items[slide] = self._items[source_slide]
        return tuple(copied)

    def items(self) -> tuple[tuple[int, CropSpec], ...]:
        return tuple(sorted(self._items.items()))


@dataclass(frozen=True, slots=True)
class EditorState:
    crop: CropSpec
    limit_mb: float

    def __post_init__(self) -> None:
        if not math.isfinite(self.limit_mb) or self.limit_mb <= 0:
            raise ValueError("limit_mb must be a positive finite number")

    @property
    def mode(self) -> str:
        return self.crop.mode

    @property
    def bounds_percent(self) -> tuple[float, float, float, float]:
        return self.crop.bounds_percent

    @property
    def expand_percent(self) -> tuple[float, float, float, float]:
        return self.crop.expand_percent

    @property
    def padding_px(self) -> int:
        return self.crop.padding_px

    def to_document(self) -> dict[str, Any]:
        return {"crop": self.crop.to_document(), "limitMb": self.limit_mb}

    @classmethod
    def from_document(cls, value: object) -> "EditorState":
        if not isinstance(value, dict) or set(value) != {"crop", "limitMb"}:
            raise ValueError("editor fields do not match the draft schema")
        return cls(crop=CropSpec.from_document(value["crop"]), limit_mb=float(value["limitMb"]))

    @classmethod
    def from_v1_document(cls, value: object) -> "EditorState":
        if not isinstance(value, dict):
            raise ValueError("editor must be an object")
        expected = {"mode", "boundsPercent", "expandPercent", "paddingPx", "limitMb"}
        if set(value) != expected:
            raise ValueError("editor fields do not match the v1 draft schema")
        bounds = value["boundsPercent"]
        expand = value["expandPercent"]
        if not isinstance(bounds, list) or len(bounds) != 4:
            raise ValueError("boundsPercent must contain four numbers")
        if not isinstance(expand, list) or len(expand) != 4:
            raise ValueError("expandPercent must contain four numbers")
        return cls(
            crop=CropSpec(
                mode=str(value["mode"]),
                bounds_percent=tuple(float(item) for item in bounds),
                expand_percent=tuple(float(item) for item in expand),
                padding_px=value["paddingPx"],
            ),
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
    page_crops: tuple[tuple[int, CropSpec], ...] = ()

    def __post_init__(self) -> None:
        if len(self.source_sha256) != 64 or any(char not in "0123456789abcdef" for char in self.source_sha256):
            raise ValueError("source_sha256 must be a lowercase SHA-256 digest")
        if isinstance(self.slide, bool) or not isinstance(self.slide, int) or self.slide < 1:
            raise ValueError("slide must be a positive integer")
        PageCropAssignments(self.page_crops)

    def to_document(self) -> dict[str, Any]:
        assignments = PageCropAssignments(self.page_crops)
        assignments.save(self.slide, self.editor.crop)
        return {
            "draftSchemaVersion": DRAFT_SCHEMA_VERSION,
            "sourcePath": self.source_path,
            "sourceSha256": self.source_sha256,
            "activeSlide": self.slide,
            "editor": self.editor.to_document(),
            "pageCrops": {str(slide): crop.to_document() for slide, crop in assignments.items()},
        }

    def assignments(self) -> PageCropAssignments:
        assignments = PageCropAssignments(self.page_crops)
        assignments.save(self.slide, self.editor.crop)
        return assignments

    @classmethod
    def from_document(cls, value: object) -> "GuiDraft":
        if not isinstance(value, dict):
            raise ValueError("draft must be an object")
        version = value.get("draftSchemaVersion")
        if version == 1:
            expected = {"draftSchemaVersion", "sourcePath", "sourceSha256", "slide", "editor"}
            if set(value) != expected:
                raise ValueError("v1 GUI draft fields do not match the schema")
            editor = EditorState.from_v1_document(value["editor"])
            return cls(
                source_path=str(value["sourcePath"]),
                source_sha256=str(value["sourceSha256"]),
                slide=value["slide"],
                editor=editor,
                page_crops=((value["slide"], editor.crop),),
            )
        expected = {
            "draftSchemaVersion",
            "sourcePath",
            "sourceSha256",
            "activeSlide",
            "editor",
            "pageCrops",
        }
        if set(value) != expected or version != DRAFT_SCHEMA_VERSION:
            raise ValueError("unsupported GUI draft schema")
        page_crops = value["pageCrops"]
        if not isinstance(page_crops, dict):
            raise ValueError("pageCrops must be an object")
        items: list[tuple[int, CropSpec]] = []
        for slide_text, crop_document in page_crops.items():
            if not isinstance(slide_text, str) or not slide_text.isascii() or not slide_text.isdigit():
                raise ValueError("pageCrops keys must be decimal slide numbers")
            items.append((int(slide_text), CropSpec.from_document(crop_document)))
        return cls(
            source_path=str(value["sourcePath"]),
            source_sha256=str(value["sourceSha256"]),
            slide=value["activeSlide"],
            editor=EditorState.from_document(value["editor"]),
            page_crops=tuple(sorted(items)),
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
