from __future__ import annotations

import hashlib
import zipfile
from pathlib import Path

from slideguard import __version__


ROOT = Path(__file__).resolve().parents[1]
DIST = ROOT / "dist"
INCLUDE = [
    ".github",
    "docs/architecture.md",
    "docs/quality-contract.md",
    "docs/reproduction-and-pitfalls.md",
    "src",
    "tests",
    "fixtures/manifests/core-matrix.json",
    "CHANGELOG.md",
    "CONTRIBUTING.md",
    "LICENSE",
    "README.md",
    "SECURITY.md",
    "SlideGuard.cmd",
    "pyproject.toml",
]


def main() -> None:
    DIST.mkdir(exist_ok=True)
    target = DIST / f"SlideGuard-{__version__}-windows-source.zip"
    with zipfile.ZipFile(target, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
        for name in INCLUDE:
            path = ROOT / name
            paths = [path] if path.is_file() else [item for item in path.rglob("*") if item.is_file()]
            for item in paths:
                if "__pycache__" in item.parts or item.suffix in {".pyc", ".xlsx"}:
                    continue
                archive.write(item, Path(f"SlideGuard-{__version__}") / item.relative_to(ROOT))
    digest = hashlib.sha256(target.read_bytes()).hexdigest()
    (DIST / f"{target.name}.sha256").write_text(f"{digest}  {target.name}\n", encoding="ascii")
    print(target)
    print(digest)


if __name__ == "__main__":
    main()

