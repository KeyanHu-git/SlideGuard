"""Framework-free editing state shared by gestures and exact controls."""
from __future__ import annotations

from dataclasses import replace
from pathlib import Path

from ..geometry import NormalizedRect, effective_pixel_box, move_normalized_rect, resize_normalized_rect
from ..gui_state import CropSpec, EditorState, EditHistory


class CropEditor:
    def __init__(self) -> None:
        self.crop = CropSpec("auto", (0., 0., 100., 100.), (0., 0., 0., 0.), 0)
        self.width = 4000
        self.height = 2250
        self.auto_rect = NormalizedRect(0, 0, 1, 1)
        self.ready = False
        self.limit_mb = 2.5
        self.history = EditHistory(self.state)
        self._gesture: EditorState | None = None

    @property
    def state(self) -> EditorState:
        return EditorState(self.crop, self.limit_mb)

    @property
    def base(self) -> NormalizedRect:
        return self.auto_rect if self.crop.mode == "auto" else NormalizedRect.from_percent(self.crop.bounds_percent)

    @property
    def effective(self) -> NormalizedRect:
        return NormalizedRect.from_pixels(*self.pixel_box)

    @property
    def pixel_box(self) -> tuple:
        return effective_pixel_box(self.base, self.width, self.height,
                                   expand_percent=self.crop.expand_percent, padding_px=self.crop.padding_px)

    def install_reference(self, box: tuple) -> None:
        self.auto_rect = NormalizedRect.from_pixels(*box)
        self.width, self.height = box[4:]
        self.ready = True

    def record(self) -> None:
        if self._gesture is None:
            self.history.record(self.state)

    def begin(self) -> None:
        if self._gesture is None:
            self._gesture = self.state

    def end(self, cancel: bool = False) -> None:
        previous, self._gesture = self._gesture, None
        if previous is None:
            return
        if cancel:
            self.crop, self.limit_mb = previous.crop, previous.limit_mb
        else:
            self.record()

    def mode(self, mode: str) -> None:
        if mode not in {"auto", "manual", "full"}:
            raise ValueError("Unknown crop mode")
        bounds = (0., 0., 100., 100.) if mode == "full" else self.base.to_percent()
        self.crop = replace(self.crop, mode="auto" if mode == "auto" else "manual", bounds_percent=bounds)
        self.record()

    def bounds(self, values: tuple) -> None:
        NormalizedRect.from_percent(values)
        self.crop = replace(self.crop, mode="manual", bounds_percent=values)
        self.record()

    def resize(self, handle: str, x: float, y: float) -> None:
        rect = resize_normalized_rect(self.base, handle, x, y,
                                      reference_width=self.width, reference_height=self.height)
        self.bounds(rect.to_percent())

    def move(self, dx: float, dy: float) -> None:
        self.bounds(move_normalized_rect(self.base, dx, dy).to_percent())

    def margin(self, edge: int, value: float) -> None:
        if edge not in {-1, 0, 1, 2, 3}:
            raise ValueError("Unknown margin edge")
        values = list(self.crop.expand_percent)
        if edge == -1:
            values = [value] * 4
        else:
            values[edge] = value
        self.crop = replace(self.crop, expand_percent=tuple(values))
        self.record()

    def budget(self, value: float) -> None:
        candidate = EditorState(self.crop, value)
        self.limit_mb = candidate.limit_mb
        self.record()

    def undo(self, redo: bool = False) -> None:
        state = self.history.redo() if redo else self.history.undo()
        if state is not None:
            self.crop, self.limit_mb = state.crop, state.limit_mb

    def request(self, source: Path, slide: int, output: Path, *, dry_run: bool = False) -> dict:
        if not self.ready:
            raise ValueError("Reference is not ready")
        return {
            "schemaVersion": "1.0", "input": str(source), "slides": str(slide),
            "outputRoot": str(output), "crop": self.crop.to_request_document(),
            "quality": {"pdfMaxBytes": int(self.limit_mb * 1_000_000),
                        "svgMaxBytes": int(self.limit_mb * 1_000_000)},
            "behavior": {"strict": True, "dryRun": dry_run, "progress": "jsonl"},
        }
