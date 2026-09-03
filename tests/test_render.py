from slideguard.render import _svg_pixel_size


def test_svg_pixel_size_preserves_fractional_aspect_ratio(tmp_path):
    svg = tmp_path / "sample.svg"
    svg.write_text(
        '<svg xmlns="http://www.w3.org/2000/svg" width="852.24" height="412.08" '
        'viewBox="0.24 2.88 852.24 412.08"/>',
        encoding="utf-8",
    )
    assert _svg_pixel_size(svg, 1600) == (1600, 774)
