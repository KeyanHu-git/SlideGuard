from __future__ import annotations

import base64
from pathlib import Path

from lxml import etree


SVG = "http://www.w3.org/2000/svg"
XLINK = "http://www.w3.org/1999/xlink"


def inject_svg_fault(source: Path, output: Path, fault: str) -> None:
    tree = etree.parse(str(source), etree.XMLParser(resolve_entities=False, no_network=True))
    root = tree.getroot()
    if fault == "white-canvas":
        x, y, width, height = root.get("viewBox").split()
        rect = etree.Element(f"{{{SVG}}}rect", x=x, y=y, width=width, height=height, fill="#ffffff")
        root.insert(0, rect)
    elif fault == "external-resource":
        image = etree.SubElement(root, f"{{{SVG}}}image", width="10", height="10")
        image.set(f"{{{XLINK}}}href", "https://example.invalid/tracker.png")
    elif fault == "image-only":
        for child in list(root):
            root.remove(child)
        png = base64.b64encode(
            b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01\x08\x06\x00\x00\x00\x1f\x15\xc4\x89"
            b"\x00\x00\x00\rIDAT\x08\xd7c\xf8\xcf\xc0\xf0\x1f\x00\x05\x00\x01\xff\x89\x99=\x1d\x00\x00\x00\x00IEND\xaeB`\x82"
        ).decode("ascii")
        x, y, width, height = root.get("viewBox").split()
        image = etree.SubElement(root, f"{{{SVG}}}image", x=x, y=y, width=width, height=height)
        image.set(f"{{{XLINK}}}href", f"data:image/png;base64,{png}")
    elif fault == "remove-dash":
        for element in tree.xpath("//*[@stroke-dasharray]"):
            element.attrib.pop("stroke-dasharray", None)
    elif fault == "remove-shadow":
        for element in tree.xpath("//*[local-name()='filter']"):
            parent = element.getparent()
            if parent is not None:
                parent.remove(element)
        for element in tree.xpath("//*[@filter]"):
            element.attrib.pop("filter", None)
    elif fault == "force-opacity":
        for element in tree.xpath("//*[@opacity or @fill-opacity or @stroke-opacity]"):
            for name in ("opacity", "fill-opacity", "stroke-opacity"):
                if name in element.attrib:
                    element.set(name, "1")
    else:
        raise ValueError(f"Unknown SVG fault: {fault}")
    output.parent.mkdir(parents=True, exist_ok=True)
    tree.write(str(output), encoding="utf-8", xml_declaration=True)

