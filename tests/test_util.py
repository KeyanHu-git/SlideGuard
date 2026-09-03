from slideguard.util import parse_slides, safe_slug


def test_parse_slides_keeps_order_and_deduplicates():
    assert parse_slides("3,1-2,3", 4) == [3, 1, 2]


def test_safe_slug_handles_windows_names_and_unicode():
    assert safe_slug("CON") == "_CON"
    assert safe_slug("图 1: final?.pptx") == "图-1--final-.pptx"

