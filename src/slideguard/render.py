from __future__ import annotations

import html
import re
import shutil
import tempfile
import time
from pathlib import Path

from PIL import Image

from .errors import EnvironmentError
from .util import require_executable, run_checked


_SVG_NUMBER = re.compile(r"^\s*([0-9]+(?:\.[0-9]+)?)")


def _find_chromium() -> str | None:
    """Return a Chromium-family browser suitable for faithful SVG rendering."""
    for name in ("msedge", "chrome", "chromium", "chromium-browser"):
        executable = shutil.which(name)
        if executable:
            return executable
    for candidate in (
        Path("C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe"),
        Path("C:/Program Files/Microsoft/Edge/Application/msedge.exe"),
        Path("C:/Program Files/Google/Chrome/Application/chrome.exe"),
        Path("C:/Program Files (x86)/Google/Chrome/Application/chrome.exe"),
    ):
        if candidate.exists():
            return str(candidate)
    return None


def svg_renderer_info() -> dict[str, str]:
    chromium = _find_chromium()
    if chromium:
        return {"backend": "chromium", "path": chromium}
    resvg = shutil.which("resvg")
    if resvg:
        return {"backend": "resvg", "path": resvg}
    try:
        import cairosvg
    except ImportError as exc:
        raise EnvironmentError("SVG rendering requires Chromium, resvg or CairoSVG") from exc
    return {"backend": "cairosvg", "version": getattr(cairosvg, "__version__", "present")}


def _svg_pixel_size(svg: Path, requested_width: int | None) -> tuple[int, int]:
    prefix = svg.read_text(encoding="utf-8", errors="replace")[:4096]
    root_match = re.search(r"<svg\b([^>]*)>", prefix, flags=re.IGNORECASE | re.DOTALL)
    if not root_match:
        raise RuntimeError(f"Cannot read SVG root dimensions: {svg}")
    attributes = root_match.group(1)

    def attribute(name: str) -> str | None:
        match = re.search(rf"\b{name}\s*=\s*(['\"])(.*?)\1", attributes, flags=re.IGNORECASE | re.DOTALL)
        return match.group(2) if match else None

    width_value = attribute("width")
    height_value = attribute("height")
    view_box = attribute("viewBox")
    source_width = source_height = None
    if width_value and height_value:
        width_match = _SVG_NUMBER.match(width_value)
        height_match = _SVG_NUMBER.match(height_value)
        if width_match and height_match:
            source_width = float(width_match.group(1))
            source_height = float(height_match.group(1))
    if (not source_width or not source_height) and view_box:
        numbers = [float(value) for value in re.findall(r"[-+]?(?:\d*\.\d+|\d+)", view_box)]
        if len(numbers) == 4:
            source_width, source_height = numbers[2], numbers[3]
    if not source_width or not source_height:
        raise RuntimeError(f"SVG needs numeric width/height or viewBox: {svg}")
    output_width = int(requested_width or round(source_width))
    output_height = max(1, int(round(output_width * source_height / source_width)))
    return output_width, output_height


def _render_svg_chromium(browser: str, svg: Path, output_png: Path, width: int | None) -> None:
    output_width, output_height = _svg_pixel_size(svg, width)
    work = Path(tempfile.mkdtemp(prefix="slideguard-svg-"))
    try:
        page = work / "render.html"
        screenshot = work / "render.png"
        profile = work / "profile"
        profile.mkdir()
        source = html.escape(svg.resolve().as_uri(), quote=True)
        page.write_text(
            "<!doctype html><meta charset=\"utf-8\">"
            "<style>html,body{margin:0;width:100%;height:100%;overflow:hidden;background:transparent}"
            "img{display:block;width:100%;height:100%;object-fit:fill}</style>"
            f"<img src=\"{source}\">",
            encoding="utf-8",
        )
        run_checked(
            [
                browser,
                "--headless",
                "--disable-gpu",
                "--disable-background-mode",
                "--disable-background-networking",
                "--disable-component-update",
                "--disable-sync",
                "--hide-scrollbars",
                "--no-first-run",
                "--no-default-browser-check",
                "--disable-extensions",
                f"--user-data-dir={profile}",
                f"--window-size={output_width},{output_height}",
                "--force-device-scale-factor=1",
                "--default-background-color=00000000",
                f"--screenshot={screenshot}",
                page.as_uri(),
            ],
            timeout=300,
        )
        _wait_for_chromium_screenshot(screenshot, (output_width, output_height))
        shutil.copy2(screenshot, output_png)
    finally:
        _remove_chromium_workdir(work)


def _wait_for_chromium_screenshot(
    screenshot: Path,
    expected_size: tuple[int, int],
    *,
    timeout: float = 30.0,
    poll_interval: float = 0.05,
) -> None:
    """Wait for Edge/Chrome's detached headless child to finish the PNG.

    On Windows the browser launcher can return successfully before its child
    has created the screenshot.  A valid, fully decoded PNG is the completion
    signal; launcher exit alone is not.
    """
    deadline = time.monotonic() + timeout
    while True:
        if screenshot.is_file():
            try:
                with Image.open(screenshot) as rendered:
                    rendered.load()
                    rendered_size = rendered.size
                if rendered_size != expected_size:
                    raise RuntimeError(
                        f"Chromium SVG render size mismatch: {rendered_size} != {expected_size}"
                    )
                return
            except OSError:
                # The browser may have created the path but not completed the
                # PNG chunks yet. Keep the profile/page alive and poll again.
                pass
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise RuntimeError("Chromium did not create a complete SVG screenshot")
        time.sleep(min(poll_interval, remaining))


def _remove_chromium_workdir(
    work: Path,
    *,
    timeout: float = 15.0,
    poll_interval: float = 0.05,
) -> None:
    """Remove a browser profile after detached Chromium children release it."""
    deadline = time.monotonic() + timeout
    last_error: OSError | None = None
    while work.exists():
        try:
            shutil.rmtree(work)
            return
        except OSError as exc:
            last_error = exc
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise RuntimeError(f"Chromium did not release its temporary profile: {work}") from last_error
        time.sleep(min(poll_interval, remaining))


def render_pdf(pdf: Path, output_png: Path, dpi: int) -> Path:
    renderer = require_executable("pdftoppm")
    output_png.parent.mkdir(parents=True, exist_ok=True)
    prefix = output_png.with_suffix("")
    run_checked([renderer, "-f", "1", "-singlefile", "-r", str(dpi), "-png", str(pdf), str(prefix)], timeout=300)
    if not output_png.exists():
        raise RuntimeError(f"PDF renderer did not create {output_png}")
    return output_png


def render_svg(svg: Path, output_png: Path, *, width: int | None = None) -> Path:
    output_png.parent.mkdir(parents=True, exist_ok=True)
    renderer = svg_renderer_info()
    chromium = renderer.get("path") if renderer["backend"] == "chromium" else None
    resvg = shutil.which("resvg")
    if chromium:
        _render_svg_chromium(chromium, svg, output_png, width)
    elif resvg:
        command = [resvg, str(svg), str(output_png)]
        if width:
            command[1:1] = ["--width", str(width)]
        run_checked(command, timeout=300)
    else:
        try:
            import cairosvg
        except ImportError as exc:
            raise EnvironmentError("SVG rendering requires resvg or CairoSVG") from exc
        cairosvg.svg2png(url=str(svg), write_to=str(output_png), output_width=width)
    if not output_png.exists():
        raise RuntimeError(f"SVG renderer did not create {output_png}")
    return output_png


def composite_checkerboard(rgba_path: Path, output_path: Path, cell: int = 24) -> Path:
    image = Image.open(rgba_path).convert("RGBA")
    background = Image.new("RGBA", image.size, (242, 242, 242, 255))
    pixels = background.load()
    for y in range(image.height):
        for x in range(image.width):
            if ((x // cell) + (y // cell)) % 2:
                pixels[x, y] = (212, 212, 212, 255)
    background.alpha_composite(image)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    background.convert("RGB").save(output_path, format="PNG")
    return output_path
