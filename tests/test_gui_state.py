from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from slideguard.contracts import validate_document
from slideguard.errors import InputError
from slideguard.util import native_long_path
from slideguard.gui_state import (
    BUILT_IN_CROP_PRESETS,
    CropPresetStore,
    CropSpec,
    EditHistory,
    EditorState,
    GuiDraft,
    GuiDraftStore,
    PageCropAssignments,
)


@pytest.mark.skipif(os.name != "nt", reason="Win32 extended path regression")
def test_draft_roundtrip_beyond_windows_max_path(tmp_path):
    root = tmp_path / ("long-parent-" * 10) / ("nested-" * 10)
    store = GuiDraftStore(root)
    digest = "f" * 64
    draft = GuiDraft(source_path="C:/paper/figure.pptx", source_sha256=digest, slide=1,
                     editor=EditorState(BUILT_IN_CROP_PRESETS[0].crop, 2.5))
    path = store.save(draft)
    assert len(str(path)) > 260
    assert Path(native_long_path(path)).is_file()
    restored = store.load(digest)
    assert restored is not None
    assert restored.to_document() == draft.to_document()
    store.discard(digest)
    assert store.load(digest) is None


def crop(left: float = 5.0, expand: float = 0.0, *, mode: str = "manual") -> CropSpec:
    return CropSpec(
        mode=mode,
        bounds_percent=(left, 5.0, 95.0, 95.0),
        expand_percent=(expand, expand, expand, expand),
        padding_px=16,
    )


def state(left: float = 5.0, expand: float = 0.0) -> EditorState:
    return EditorState(crop=crop(left, expand), limit_mb=2.5)


def test_crop_spec_is_the_export_request_boundary():
    manual = crop()
    automatic = crop(mode="auto")

    assert manual.to_request_document() == manual.to_document()
    validate_document(manual.to_document(), "gui-crop-spec.schema.json")
    assert "boundsPercent" not in automatic.to_request_document()
    assert automatic.to_request_document()["expandPercent"] == {
        "left": 0.0,
        "top": 0.0,
        "right": 0.0,
        "bottom": 0.0,
    }
    validate_document(
        {
            "schemaVersion": "1.0",
            "input": "figure.pptx",
            "crop": automatic.to_request_document(),
        },
        "export-request.schema.json",
    )


def test_built_in_presets_cover_tight_paper_safe_and_full_page():
    by_id = {item.preset_id: item for item in BUILT_IN_CROP_PRESETS}

    assert by_id["builtin:tight"].crop == CropSpec(
        "auto", (0.0, 0.0, 100.0, 100.0), (0.0, 0.0, 0.0, 0.0), 0
    )
    assert by_id["builtin:paper-safe"].crop.expand_percent == (2.0, 2.0, 2.0, 2.0)
    assert by_id["builtin:paper-safe"].crop.padding_px == 16
    assert by_id["builtin:full-page"].crop.mode == "manual"


def test_editor_state_rejects_invalid_crop_and_non_finite_values():
    with pytest.raises(InputError):
        CropSpec("manual", (95, 0, 5, 100), (0, 0, 0, 0), 0)
    with pytest.raises(InputError):
        CropSpec("manual", (0, 0, 100, 100), (0, 0, float("nan"), 0), 0)
    with pytest.raises(ValueError):
        EditorState(crop(), float("inf"))


def test_history_discards_redo_branch_and_coalesces_duplicates():
    first = state()
    second = state(6)
    third = state(7)
    replacement = state(8)
    history = EditHistory(first)

    assert not history.record(first)
    assert history.record(second)
    assert history.record(third)
    assert history.undo() == second
    assert history.record(replacement)
    assert not history.can_redo
    assert history.undo() == second
    assert history.undo() == first
    assert history.undo() is None


def test_history_has_a_fixed_memory_bound():
    history = EditHistory(state(), maximum=3)
    for left in (6, 7, 8, 9):
        history.record(state(left))

    assert history.current == state(9)
    assert history.undo() == state(8)
    assert history.undo() == state(7)
    assert history.undo() is None


def test_page_assignments_save_and_copy_one_crop_spec_to_selected_pages():
    assignments = PageCropAssignments()
    original = crop(left=7, expand=2)
    fallback = crop()
    assignments.save(2, original)

    copied = assignments.copy(2, [5, 3, 5])

    assert copied == (5, 3)
    assert assignments.get(3, fallback) is original
    assert assignments.get(5, fallback) is original
    assert assignments.get(9, fallback) is fallback
    assert tuple(slide for slide, _ in assignments.items()) == (2, 3, 5)


def test_page_assignment_copy_rejects_all_targets_before_writing():
    assignments = PageCropAssignments(((2, crop(left=7)),))

    with pytest.raises(ValueError):
        assignments.copy(2, [3, 0, 4])

    assert tuple(slide for slide, _ in assignments.items()) == (2,)


def test_custom_preset_store_is_atomic_and_updates_names_case_insensitively(tmp_path):
    path = tmp_path / "crop-presets.json"
    store = CropPresetStore(path)
    first = store.save("My paper", crop(left=4))
    updated = store.save("my PAPER", crop(left=6, expand=1))

    assert first.preset_id == updated.preset_id
    assert store.load() == (updated,)
    validate_document(json.loads(path.read_text(encoding="utf-8")), "gui-crop-presets.schema.json")
    assert not list(tmp_path.glob("*.tmp"))
    assert store.delete(updated.preset_id)
    assert store.load() == ()
    assert not store.delete(updated.preset_id)

    with pytest.raises(ValueError):
        store.save("自动紧边", crop())


def test_gui_draft_round_trip_keeps_per_page_crop_specs(tmp_path):
    digest = "a" * 64
    store = GuiDraftStore(tmp_path / "gui-drafts")
    draft = GuiDraft(
        source_path=r"C:\paper\figure.pptx",
        source_sha256=digest,
        slide=3,
        editor=state(expand=2),
        page_crops=((1, crop(left=1)), (2, crop(left=2))),
    )

    path = store.save(draft)
    restored = store.load(digest)

    assert path.parent == tmp_path / "gui-drafts"
    assert restored == GuiDraft.from_document(draft.to_document())
    assert restored is not None
    assert dict(restored.page_crops)[3] == draft.editor.crop
    document = json.loads(path.read_text(encoding="utf-8"))
    validate_document(document, "gui-draft.schema.json")
    assert "outputRoot" not in document
    assert "request" not in document
    assert not list(path.parent.glob("*.tmp"))

    store.discard(digest)
    assert store.load(digest) is None


def test_v1_gui_draft_migrates_to_current_page_assignment():
    document = {
        "draftSchemaVersion": 1,
        "sourcePath": r"C:\paper\figure.pptx",
        "sourceSha256": "c" * 64,
        "slide": 4,
        "editor": {
            "mode": "manual",
            "boundsPercent": [4, 5, 95, 96],
            "expandPercent": [1, 2, 3, 4],
            "paddingPx": 12,
            "limitMb": 2.5,
        },
    }

    migrated = GuiDraft.from_document(document)

    assert migrated.slide == 4
    assert dict(migrated.page_crops)[4] == migrated.editor.crop
    assert migrated.to_document()["draftSchemaVersion"] == 2


@pytest.mark.parametrize(
    "document",
    [
        "not json",
        json.dumps({"draftSchemaVersion": 99}),
        json.dumps(
            {
                "draftSchemaVersion": 2,
                "sourcePath": "x.pptx",
                "sourceSha256": "b" * 64,
                "activeSlide": 1,
                "editor": {
                    "crop": {
                        "mode": "manual",
                        "boundsPercent": {"left": 0, "top": 0, "right": 100, "bottom": 100},
                        "expandPercent": {"left": 0, "top": 0, "right": 0, "bottom": 0},
                        "paddingPx": 0,
                    },
                    "limitMb": "NaN",
                },
                "pageCrops": {},
            }
        ),
    ],
)
def test_gui_draft_store_ignores_corrupt_or_incompatible_data(tmp_path, document):
    digest = "b" * 64
    store = GuiDraftStore(tmp_path)
    path = store.path_for(digest)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(document, encoding="utf-8")

    assert store.load(digest) is None
    assert not path.exists()
