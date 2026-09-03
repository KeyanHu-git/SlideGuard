from __future__ import annotations

import io
import posixpath
import zipfile
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

from lxml import etree
from PIL import Image

from .errors import InputError
from .model import FeatureInventory
from .util import sha256_bytes


NS = {
    "a": "http://schemas.openxmlformats.org/drawingml/2006/main",
    "m": "http://schemas.openxmlformats.org/officeDocument/2006/math",
    "p": "http://schemas.openxmlformats.org/presentationml/2006/main",
    "pr": "http://schemas.openxmlformats.org/package/2006/relationships",
    "r": "http://schemas.openxmlformats.org/officeDocument/2006/relationships",
}
REL = "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}id"
EMBED = "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}embed"


def _parse(data: bytes) -> etree._Element:
    return etree.fromstring(data, parser=etree.XMLParser(resolve_entities=False, no_network=True, huge_tree=True))


def _rels_part(part: str) -> str:
    path = PurePosixPath(part)
    return str(path.parent / "_rels" / f"{path.name}.rels")


def _resolve_target(source_part: str, target: str) -> str:
    if target.startswith("/"):
        return target.lstrip("/")
    return posixpath.normpath(posixpath.join(posixpath.dirname(source_part), target))


@dataclass(slots=True)
class PptxPackage:
    path: Path
    page_width_emu: int
    page_height_emu: int
    slide_parts: list[str]

    @property
    def slide_count(self) -> int:
        return len(self.slide_parts)

    @property
    def page_width_pt(self) -> float:
        return self.page_width_emu / 12700.0

    @property
    def page_height_pt(self) -> float:
        return self.page_height_emu / 12700.0

    @classmethod
    def open(cls, path: Path) -> "PptxPackage":
        path = path.resolve()
        if not path.is_file() or path.suffix.lower() != ".pptx":
            raise InputError(f"Not a PPTX file: {path}")
        try:
            with zipfile.ZipFile(path) as archive:
                presentation = _parse(archive.read("ppt/presentation.xml"))
                rels = _parse(archive.read("ppt/_rels/presentation.xml.rels"))
                relmap = {rel.get("Id"): rel.get("Target") for rel in rels.xpath("//pr:Relationship", namespaces=NS)}
                slide_parts = []
                for node in presentation.xpath("//p:sldIdLst/p:sldId", namespaces=NS):
                    rid = node.get(REL)
                    target = relmap.get(rid or "")
                    if not target:
                        raise InputError(f"Missing relationship for slide id {rid}")
                    slide_parts.append(_resolve_target("ppt/presentation.xml", target))
                size = presentation.find("p:sldSz", namespaces=NS)
                if size is None:
                    raise InputError("PPTX does not declare slide dimensions")
                return cls(path, int(size.get("cx")), int(size.get("cy")), slide_parts)
        except (zipfile.BadZipFile, KeyError, etree.XMLSyntaxError) as exc:
            raise InputError(f"Invalid or incomplete PPTX: {path}") from exc

    def inventory(self, slide_number: int) -> FeatureInventory:
        if not 1 <= slide_number <= self.slide_count:
            raise InputError(f"Slide {slide_number} is outside 1..{self.slide_count}")
        part = self.slide_parts[slide_number - 1]
        with zipfile.ZipFile(self.path) as archive:
            slide = _parse(archive.read(part))
            relmap: dict[str, tuple[str, str | None]] = {}
            rels_name = _rels_part(part)
            if rels_name in archive.namelist():
                rels = _parse(archive.read(rels_name))
                for rel in rels.xpath("//pr:Relationship", namespaces=NS):
                    relmap[rel.get("Id")] = (rel.get("Target", ""), rel.get("TargetMode"))

            fonts = set()
            for attr in slide.xpath("//@typeface"):
                if attr and not attr.startswith("+"):
                    fonts.add(str(attr))

            media = []
            alpha_media_count = 0
            seen_media: set[tuple[str, tuple[int, int, int, int]]] = set()
            for blip in slide.xpath("//a:blip[@r:embed]", namespaces=NS):
                rid = blip.get(EMBED)
                relation = relmap.get(rid or "")
                if not relation or relation[1] == "External":
                    continue
                target = _resolve_target(part, relation[0])
                parent = blip.getparent()
                src_rect = parent.find("a:srcRect", namespaces=NS) if parent is not None else None
                crop = tuple(int(src_rect.get(key, "0")) if src_rect is not None else 0 for key in ("l", "t", "r", "b"))
                key = (target, crop)
                if key in seen_media or target not in archive.namelist():
                    continue
                seen_media.add(key)
                raw = archive.read(target)
                record = {"part": target, "sha256": sha256_bytes(raw), "bytes": len(raw), "crop": list(crop)}
                try:
                    with Image.open(io.BytesIO(raw)) as image:
                        alpha = image.getchannel("A") if "A" in image.getbands() else None
                        has_alpha = bool(alpha is not None and alpha.getextrema() != (255, 255))
                        alpha_media_count += int(has_alpha)
                        record.update({"width": image.width, "height": image.height, "format": image.format, "mode": image.mode, "hasAlpha": has_alpha})
                except Exception:
                    record.update({"width": None, "height": None, "format": "unsupported", "mode": None})
                media.append(record)

            external = []
            for rid, (target, target_mode) in relmap.items():
                if target_mode == "External":
                    external.append(f"{rid}:{target}")

            dash_nodes = slide.xpath("//a:ln/a:prstDash", namespaces=NS)
            dashed = [node for node in dash_nodes if node.get("val", "solid") != "solid"]
            return FeatureInventory(
                slide=slide_number,
                slide_part=part,
                shape_count=len(slide.xpath("//p:sp|//p:pic|//p:cxnSp|//p:graphicFrame", namespaces=NS)),
                image_count=len(slide.xpath("//p:pic", namespaces=NS)),
                line_count=len(slide.xpath("//p:cxnSp|//p:sp[p:spPr/a:ln]", namespaces=NS)),
                dashed_line_count=len(dashed),
                shadow_count=len(slide.xpath("//a:outerShdw|//a:innerShdw", namespaces=NS)),
                transparency_count=len(slide.xpath("//a:alpha|//a:alphaMod|//a:alphaModFix|//a:alphaOff", namespaces=NS)) + alpha_media_count,
                crop_count=len(slide.xpath("//a:srcRect", namespaces=NS)),
                group_count=len(slide.xpath("//p:grpSp", namespaces=NS)),
                gradient_count=len(slide.xpath("//a:gradFill", namespaces=NS)),
                text_run_count=len(slide.xpath("//a:r|//a:fld", namespaces=NS)),
                formula_count=len(slide.xpath("//m:oMath|//m:oMathPara", namespaces=NS)),
                fonts=sorted(fonts, key=str.casefold),
                external_relationships=external,
                media=media,
            )

    def media_candidates(self, slide_number: int) -> list[tuple[str, tuple[int, int, int, int], bytes]]:
        part = self.slide_parts[slide_number - 1]
        result = []
        with zipfile.ZipFile(self.path) as archive:
            slide = _parse(archive.read(part))
            rels = _parse(archive.read(_rels_part(part)))
            relmap = {
                rel.get("Id"): (rel.get("Target", ""), rel.get("TargetMode"))
                for rel in rels.xpath("//pr:Relationship", namespaces=NS)
            }
            seen = set()
            for blip in slide.xpath("//a:blip[@r:embed]", namespaces=NS):
                rid = blip.get(EMBED)
                relation = relmap.get(rid or "")
                if not relation or relation[1] == "External":
                    continue
                target = _resolve_target(part, relation[0])
                parent = blip.getparent()
                src_rect = parent.find("a:srcRect", namespaces=NS) if parent is not None else None
                crop = tuple(int(src_rect.get(key, "0")) if src_rect is not None else 0 for key in ("l", "t", "r", "b"))
                key = (target, crop)
                if key in seen or target not in archive.namelist():
                    continue
                seen.add(key)
                raw = archive.read(target)
                try:
                    with Image.open(io.BytesIO(raw)) as image:
                        image.verify()
                except Exception:
                    continue
                result.append((PurePosixPath(target).name, crop, raw))
        return result
