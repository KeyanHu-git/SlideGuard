from pathlib import Path

from slideguard.faults import inject_svg_fault
from slideguard.model import FeatureInventory, Verdict
from slideguard.qa import validate_svg_structure, validate_svg_vector_invariant


BASE = """<svg xmlns="http://www.w3.org/2000/svg" xmlns:xlink="http://www.w3.org/1999/xlink" viewBox="0 0 100 100">
<defs><filter id="shadow"><feGaussianBlur stdDeviation="2"/></filter></defs>
<path d="M 5 5 L 95 95" stroke="#000" stroke-dasharray="4 2" opacity="0.5" filter="url(#shadow)"/>
</svg>"""


def inventory():
    return FeatureInventory(slide=1, slide_part="ppt/slides/slide1.xml", line_count=1, dashed_line_count=1)


def test_good_svg_passes_structure(tmp_path: Path):
    source = tmp_path / "good.svg"
    source.write_text(BASE, encoding="utf-8")
    results = validate_svg_structure(source, inventory())
    assert all(item.status == Verdict.PASS for item in results)


def test_white_canvas_fault_is_detected(tmp_path: Path):
    source = tmp_path / "good.svg"
    bad = tmp_path / "bad.svg"
    source.write_text(BASE, encoding="utf-8")
    inject_svg_fault(source, bad, "white-canvas")
    results = validate_svg_structure(bad, inventory())
    assert any(item.code == "FIDELITY_ALPHA_CANVAS" and item.status == Verdict.FAIL for item in results)


def test_external_resource_fault_is_detected(tmp_path: Path):
    source = tmp_path / "good.svg"
    bad = tmp_path / "bad.svg"
    source.write_text(BASE, encoding="utf-8")
    inject_svg_fault(source, bad, "external-resource")
    results = validate_svg_structure(bad, inventory())
    assert any(item.code == "SEC_SVG_EXTERNAL_RESOURCE" and item.status == Verdict.FAIL for item in results)


def test_image_only_fault_is_detected(tmp_path: Path):
    source = tmp_path / "good.svg"
    bad = tmp_path / "bad.svg"
    source.write_text(BASE, encoding="utf-8")
    inject_svg_fault(source, bad, "image-only")
    results = validate_svg_structure(bad, inventory())
    assert any(item.code == "STRUCTURE_SVG_VECTOR_CONTENT" and item.status == Verdict.FAIL for item in results)


def test_vector_faults_change_exact_fingerprint(tmp_path: Path):
    source = tmp_path / "good.svg"
    source.write_text(BASE, encoding="utf-8")
    for fault in ("remove-dash", "remove-shadow", "force-opacity"):
        bad = tmp_path / f"{fault}.svg"
        inject_svg_fault(source, bad, fault)
        results = validate_svg_vector_invariant(bad, source, inventory())
        assert results[0].status == Verdict.FAIL
