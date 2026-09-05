from __future__ import annotations

import importlib

from slideguard import gui_launcher


def test_launcher_explains_missing_optional_gui(monkeypatch, capsys):
    def missing(_name: str):
        raise ModuleNotFoundError("No module named 'PySide6'", name="PySide6")

    monkeypatch.setattr(importlib, "import_module", missing)
    assert gui_launcher.main() == 20
    assert "slideguard[gui]" in capsys.readouterr().err


def test_launcher_does_not_hide_unrelated_import_failure(monkeypatch):
    def missing(_name: str):
        raise ModuleNotFoundError("No module named 'other'", name="other")

    monkeypatch.setattr(importlib, "import_module", missing)
    try:
        gui_launcher.main()
    except ModuleNotFoundError as exc:
        assert exc.name == "other"
    else:
        raise AssertionError("unrelated import failures must remain visible")
