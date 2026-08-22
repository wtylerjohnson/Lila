"""aria-hidden subtrees are presentation, not copy (operator-approved 2026-07-30).

The scrolling ticker carries a duplicate mirror set so the CSS keyframe, which
ends at translateX(-50%), loops seamlessly. Counting that presentational copy
as prose produced 12 phantom repeated_prose violations against a report whose
visible copy never repeated.
"""

from __future__ import annotations

from agents.golden_press.validate import _strip_aria_hidden, _visible_text


def test_hidden_subtree_is_dropped_and_visible_copy_survives():
    html = ('<div class="set">REAL COPY</div>'
            '<div class="set" aria-hidden="true">MIRROR COPY</div>')
    text = _visible_text(html)
    assert "REAL COPY" in text
    assert "MIRROR COPY" not in text


def test_nested_same_tag_closes_correctly():
    html = ('<div aria-hidden="true"><div>inner</div>outer</div>'
            "<div>KEPT</div>")
    text = _visible_text(html)
    assert "inner" not in text and "outer" not in text
    assert "KEPT" in text


def test_void_element_drops_only_itself():
    html = '<img aria-hidden="true" src="x.png"><span>KEPT</span>'
    assert "KEPT" in _visible_text(html)


def test_self_closed_element_drops_only_itself():
    html = '<span aria-hidden="true"/><span>KEPT</span>'
    assert "KEPT" in _visible_text(html)


def test_aria_hidden_false_is_still_read():
    html = '<div aria-hidden="false">READ ME</div>'
    assert "READ ME" in _visible_text(html)


def test_unbalanced_markup_never_raises():
    assert isinstance(_strip_aria_hidden('<div aria-hidden="true">oops'), str)


def test_a_real_ticker_mirror_does_not_read_as_repeated_prose():
    from agents.golden_press.validate import check_repeated_prose

    line = ("Internal Revenue Service this is a definitive contract to acquire "
            "Riverbed network monitoring hardware and software maintenance")
    html = (f'<div class="sb-news-set">{line}</div>'
            f'<div class="sb-news-set" aria-hidden="true">{line}</div>')
    assert [v for v in check_repeated_prose(html) if v.rule == "repeated_prose"] == []
