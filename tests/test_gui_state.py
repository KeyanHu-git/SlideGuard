from __future__ import annotations

import json

import pytest

from slideguard.errors import InputError
from slideguard.gui_state import EditHistory, EditorState, GuiDraft, GuiDraftStore


def state(left: float = 5.0, expand: float = 0.0) -> EditorState:
    return EditorState(
        mode="manual",
        bounds_percent=(left, 5.0, 95.0, 95.0),
        expand_percent=(expand, expand, expand, expand),
        padding_px=16,
        limit_mb=2.5,
    )


def test_editor_state_rejects_invalid_crop_and_non_finite_values():
    with pytest.raises(InputError):
        EditorState("manual", (95, 0, 5, 100), (0, 0, 0, 0), 0, 2.5)
    with pytest.raises(InputError):
        EditorState("manual", (0, 0, 100, 100), (0, 0, float("nan"), 0), 0, 2.5)
    with pytest.raises(ValueError):
        EditorState("manual", (0, 0, 100, 100), (0, 0, 0, 0), 0, float("inf"))


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


def test_gui_draft_round_trip_stays_in_its_own_store(tmp_path):
    digest = "a" * 64
    store = GuiDraftStore(tmp_path / "gui-drafts")
    draft = GuiDraft(
        source_path=r"C:\paper\figure.pptx",
        source_sha256=digest,
        slide=3,
        editor=state(expand=2),
    )

    path = store.save(draft)

    assert path.parent == tmp_path / "gui-drafts"
    assert store.load(digest) == draft
    document = json.loads(path.read_text(encoding="utf-8"))
    assert "outputRoot" not in document
    assert "request" not in document
    assert not list(path.parent.glob("*.tmp"))

    store.discard(digest)
    assert store.load(digest) is None


@pytest.mark.parametrize(
    "document",
    [
        "not json",
        json.dumps({"draftSchemaVersion": 99}),
        json.dumps(
            {
                "draftSchemaVersion": 1,
                "sourcePath": "x.pptx",
                "sourceSha256": "b" * 64,
                "slide": 1,
                "editor": {
                    "mode": "manual",
                    "boundsPercent": [0, 0, 100, 100],
                    "expandPercent": [0, 0, 0, 0],
                    "paddingPx": 0,
                    "limitMb": "NaN",
                },
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
