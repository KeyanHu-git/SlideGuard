from __future__ import annotations

import json
import math
import subprocess
from importlib.resources import files
from pathlib import Path

from PIL import Image, ImageDraw

from .errors import ExportError
from .powerpoint import _powershell
from .util import sha256_file, write_json


def _alpha_asset(path: Path, size: int = 1024) -> None:
    image = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)
    for radius in range(size // 2, 0, -4):
        alpha = round(255 * (1 - radius / (size / 2)) ** 0.6)
        hue = radius / (size / 2)
        color = (round(40 + 190 * hue), round(190 - 120 * hue), round(235 - 80 * hue), alpha)
        box = (size // 2 - radius, size // 2 - radius, size // 2 + radius, size // 2 + radius)
        draw.ellipse(box, fill=color)
    for index in range(0, size, 32):
        draw.line((index, 0, index, size), fill=(255, 255, 255, 80), width=1)
        draw.line((0, index, size, index), fill=(255, 255, 255, 80), width=1)
    draw.rectangle((0, 0, size - 1, size - 1), outline=(255, 0, 255, 255), width=3)
    image.save(path, format="PNG", compress_level=9)


def build_core_fixture(output_dir: Path) -> dict:
    output_dir = output_dir.resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    asset = output_dir / "alpha-grid.png"
    pptx = output_dir / "slideguard-core-torture.pptx"
    truth = output_dir / "truth.json"
    _alpha_asset(asset)
    worker = Path(str(files("slideguard").joinpath("resources/fixture_builder.ps1")))
    command = [
        _powershell(), "-NoLogo", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass",
        "-File", str(worker), "-OutputPptx", str(pptx), "-AlphaPng", str(asset),
    ]
    process = subprocess.run(command, capture_output=True, text=True, timeout=300, check=False)
    if process.returncode or not pptx.exists():
        raise ExportError(process.stderr.strip() or process.stdout.strip() or "Fixture builder failed")
    data = {
        "schemaVersion": "1.0",
        "fixtureId": "core-torture-v1",
        "seed": 20260904,
        "pptx": pptx.name,
        "pptxSha256": sha256_file(pptx),
        "assetSha256": sha256_file(asset),
        "slides": [
            {"slide": 1, "features": ["solid-lines", "dash-lines", "hairlines", "shadow", "gradient"], "hardAssertions": ["FIDELITY_DASH", "FIDELITY_SHADOW", "FIDELITY_SEAM"]},
            {"slide": 2, "features": ["rgba-image", "crop", "rotation", "transparency", "occlusion"], "hardAssertions": ["FIDELITY_ALPHA_CANVAS", "FIDELITY_ALPHA_RENDER", "STRUCTURE_CONTENT_STREAM"]},
            {"slide": 3, "features": ["full-bleed", "adjacent-fills", "vertical-dash"], "hardAssertions": ["STRUCTURE_PAGE_BOX", "FIDELITY_SEAM", "FIDELITY_DASH"]},
        ],
    }
    write_json(truth, data)
    (output_dir / "fixture.sha256").write_text(
        f"{sha256_file(pptx)}  {pptx.name}\n{sha256_file(asset)}  {asset.name}\n{sha256_file(truth)}  {truth.name}\n",
        encoding="utf-8",
    )
    return data

