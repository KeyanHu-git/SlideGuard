from __future__ import annotations

import importlib
import sys
from pathlib import Path


def main() -> int:
    """Start the optional GUI without importing PySide at package load time."""
    try:
        gui = importlib.import_module("slideguard.gui")
    except ModuleNotFoundError as exc:
        if exc.name == "PySide6" or (exc.name or "").startswith("PySide6."):
            print(
                "SlideGuard 的可视界面尚未安装。请运行：pip install 'slideguard[gui]'",
                file=sys.stderr,
            )
            return 20
        raise
    initial = Path(sys.argv[1]).resolve() if len(sys.argv) > 1 else None
    return int(gui.run_gui(initial))
