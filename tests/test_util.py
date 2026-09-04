import pytest

from slideguard.errors import InputError
from slideguard.util import parse_slides, safe_slug


def test_parse_slides_keeps_order_and_deduplicates():
    assert parse_slides("3,1-2,3", 4) == [3, 1, 2]


def test_safe_slug_handles_windows_names_and_unicode():
    assert safe_slug("CON") == "_CON"
    assert safe_slug("图 1: final?.pptx") == "图-1--final-.pptx"


def test_parse_slides_rejects_huge_range_before_expanding_it():
    with pytest.raises(InputError, match="outside"):
        parse_slides("1-999999999", 12)

    with pytest.raises(InputError, match="at most"):
        parse_slides("1-999999999")
