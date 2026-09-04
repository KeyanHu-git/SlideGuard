from __future__ import annotations

import warnings
import zipfile
from pathlib import Path

import pytest

from slideguard.errors import InputError
import slideguard.ooxml as ooxml
from slideguard.ooxml import PptxPackage, _canonical_part_name


PRESENTATION = b'''<p:presentation xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships" xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main"><p:sldIdLst><p:sldId id="256" r:id="rId1"/></p:sldIdLst><p:sldSz cx="12192000" cy="6858000"/></p:presentation>'''
RELS = b'''<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="slide" Target="slides/slide1.xml"/></Relationships>'''


def _deck(path: Path, *, presentation: bytes = PRESENTATION, rels: bytes = RELS, extras=()) -> Path:
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("ppt/presentation.xml", presentation)
        archive.writestr("ppt/_rels/presentation.xml.rels", rels)
        archive.writestr("ppt/slides/slide1.xml", "<p:sld xmlns:p='http://schemas.openxmlformats.org/presentationml/2006/main'/>")
        for name, data in extras:
            archive.writestr(name, data)
    return path


def test_rejects_duplicate_package_parts(tmp_path: Path):
    path = _deck(tmp_path / "duplicate.pptx")
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", UserWarning)
        with zipfile.ZipFile(path, "a") as archive:
            archive.writestr("ppt/presentation.xml", PRESENTATION)
    with pytest.raises(InputError, match="duplicate OOXML part"):
        PptxPackage.open(path)


@pytest.mark.parametrize("name", ["../escape.xml", "/absolute.xml", "C:/drive.xml", "ppt/%2e%2e/escape.xml"])
def test_rejects_unsafe_package_part_names(tmp_path: Path, name: str):
    path = _deck(tmp_path / "unsafe.pptx", extras=[(name, "x")])
    with pytest.raises(InputError, match="Unsafe OOXML part name"):
        PptxPackage.open(path)


def test_rejects_backslash_member_names_before_path_normalization():
    with pytest.raises(InputError, match="Unsafe OOXML part name"):
        _canonical_part_name("ppt\\escape.xml")


def test_rejects_high_ratio_zip_bomb_before_xml_parse(tmp_path: Path):
    path = _deck(tmp_path / "bomb.pptx", extras=[("ppt/media/bomb.bin", b"0" * (2 * 1024 * 1024))])
    with pytest.raises(InputError, match="compression-ratio limit"):
        PptxPackage.open(path)


def test_rejects_entry_count_before_reading_parts(tmp_path: Path, monkeypatch):
    path = _deck(tmp_path / "entries.pptx", extras=[("ppt/extra.xml", "x")])
    monkeypatch.setattr(ooxml, "MAX_ARCHIVE_ENTRIES", 3)
    with pytest.raises(InputError, match="more than 3 ZIP entries"):
        PptxPackage.open(path)


def test_rejects_single_part_and_total_expansion_limits(tmp_path: Path, monkeypatch):
    path = _deck(tmp_path / "sizes.pptx", extras=[("ppt/media/data.bin", b"abcdefghij")])
    monkeypatch.setattr(ooxml, "MAX_PART_BYTES", 9)
    with pytest.raises(InputError, match="larger than 9 bytes"):
        PptxPackage.open(path)

    monkeypatch.setattr(ooxml, "MAX_PART_BYTES", 1024 * 1024)
    monkeypatch.setattr(ooxml, "MAX_ARCHIVE_BYTES", len(PRESENTATION) + len(RELS))
    with pytest.raises(InputError, match="expands beyond"):
        PptxPackage.open(path)


def test_rejects_relationship_that_escapes_package(tmp_path: Path):
    rels = RELS.replace(b"slides/slide1.xml", b"../../../outside.xml")
    path = _deck(tmp_path / "rels.pptx", rels=rels)
    with pytest.raises(InputError, match="relationship escapes"):
        PptxPackage.open(path)


def test_rejects_dtd_even_when_entities_are_not_resolved(tmp_path: Path):
    presentation = b'''<!DOCTYPE p:presentation [<!ENTITY x "1">]><p:presentation xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships" xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main"><p:sldIdLst/><p:sldSz cx="12192000" cy="6858000"/></p:presentation>'''
    path = _deck(tmp_path / "dtd.pptx", presentation=presentation)
    with pytest.raises(InputError, match="must not declare DTDs"):
        PptxPackage.open(path)
