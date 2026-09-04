from __future__ import annotations

import os
import sys
from pathlib import Path


def add_private_tool_paths(package_root: Path) -> list[Path]:
    """Put optional user-supplied tools ahead of the inherited PATH."""
    candidates = (
        package_root / "external" / "poppler" / "Library" / "bin",
        package_root / "external" / "poppler" / "bin",
        package_root / "external" / "chromium",
    )
    existing = [path for path in candidates if path.is_dir()]
    if existing:
        inherited = os.environ.get("PATH", "")
        prefix = os.pathsep.join(str(path) for path in existing)
        os.environ["PATH"] = prefix + (os.pathsep + inherited if inherited else "")
    return existing


def main() -> int:
    package_root = Path(sys.executable).resolve().parent if getattr(sys, "frozen", False) else Path(__file__).resolve().parents[1]
    add_private_tool_paths(package_root)
    from slideguard.cli import main as cli_main

    return int(cli_main())


if __name__ == "__main__":
    raise SystemExit(main())
