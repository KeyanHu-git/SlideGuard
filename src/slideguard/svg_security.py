from __future__ import annotations

import re
from pathlib import Path

from lxml import etree

from .errors import InputError


MAX_SVG_BYTES = 256 * 1024 * 1024
FORBIDDEN_ELEMENTS = {
    "script", "foreignobject", "iframe", "object", "embed", "audio", "video", "canvas",
}
URL_FUNCTION = re.compile(r"url\(\s*(['\"]?)(.*?)\1\s*\)", flags=re.IGNORECASE)
SAFE_DATA_IMAGE = re.compile(r"^data:image/(?:png|jpeg);base64,[A-Za-z0-9+/=\s]+$", flags=re.IGNORECASE)


def parse_svg(path: Path) -> etree._ElementTree:
    if not path.is_file() or path.stat().st_size > MAX_SVG_BYTES:
        raise InputError("SVG is missing or exceeds the size limit")
    data = path.read_bytes()
    if re.search(br"<!\s*(?:DOCTYPE|ENTITY)\b", data, flags=re.IGNORECASE):
        raise InputError("SVG must not declare DTDs or entities")
    try:
        root = etree.fromstring(
            data,
            parser=etree.XMLParser(resolve_entities=False, no_network=True, huge_tree=False),
        )
    except etree.XMLSyntaxError as exc:
        raise InputError("SVG is not valid safe XML") from exc
    return etree.ElementTree(root)


def _safe_reference(value: str, *, allow_data_image: bool) -> bool:
    stripped = value.strip()
    if stripped.startswith("#") and len(stripped) > 1:
        return True
    if allow_data_image and SAFE_DATA_IMAGE.fullmatch(stripped):
        return True
    return False


def security_violations(tree: etree._ElementTree) -> list[str]:
    violations: list[str] = []
    for element in tree.iter():
        local_element = etree.QName(element).localname.casefold()
        if local_element in FORBIDDEN_ELEMENTS:
            violations.append(f"element:{local_element}")
        if local_element == "style" and element.text:
            for match in URL_FUNCTION.finditer(element.text):
                if not _safe_reference(match.group(2), allow_data_image=False):
                    violations.append("style:external-url")
            if "javascript:" in element.text.casefold():
                violations.append("style:javascript")
        for name, raw_value in element.attrib.items():
            local_name = etree.QName(name).localname.casefold()
            value = str(raw_value)
            lowered = value.casefold()
            if local_name.startswith("on"):
                violations.append(f"event:{local_name}")
            if local_name == "base":
                violations.append("attribute:base")
            if "javascript:" in lowered or "file:" in lowered:
                violations.append(f"attribute:{local_name}:active-url")
            if local_name in {"href", "src", "poster"}:
                allow_data = local_element == "image" and local_name == "href"
                if not _safe_reference(value, allow_data_image=allow_data):
                    violations.append(f"attribute:{local_name}:external")
            if local_name == "style" or "url(" in lowered:
                for match in URL_FUNCTION.finditer(value):
                    if not _safe_reference(match.group(2), allow_data_image=False):
                        violations.append(f"attribute:{local_name}:external-url")
    return list(dict.fromkeys(violations))
