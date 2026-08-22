"""A document nobody can look at is not a deliverable.

The failure this guards against shipped on 2026-08-07 with a fully green
suite: 24 of 31 emitted classes had no CSS rule, so the client artifact
rendered as unstyled HTML. Every structural test passed, because every
structural test asked whether the document was CORRECT and none asked
whether it was VIEWABLE.
"""

from __future__ import annotations

import pytest

from agents.golden_press.style_contract import (
    BEHAVIOURAL, audit, defined_classes, emitted_classes, require_styled,
    unstyled,
)

STYLED = """
<style>.card{border:1px solid}.card-title{font-weight:700}</style>
<div class="card"><h3 class="card-title">x</h3></div>
"""

UNSTYLED_DOC = """
<style>.sb-band{padding:2rem}</style>
<section class="mm"><div class="n">01</div><p class="lede">x</p></section>
"""


def test_emitted_classes_reads_every_token():
    assert emitted_classes('<a class="one two"></a><b class="three"></b>') == {
        "one", "two", "three"}


def test_defined_classes_reads_the_stylesheet():
    assert defined_classes(".a{}.b-c{}") == {"a", "b-c"}


def test_a_fully_styled_document_has_no_gap():
    assert unstyled(STYLED) == set()
    assert audit(STYLED)["styled_fraction"] == 1.0


def test_the_shipped_failure_is_caught():
    """The exact shape of the artifact the operator called awful."""
    gap = unstyled(UNSTYLED_DOC)
    assert gap == {"mm", "n", "lede"}
    assert audit(UNSTYLED_DOC)["styled_fraction"] < 0.5


def test_require_styled_names_the_classes_and_the_remedy():
    with pytest.raises(AssertionError) as excinfo:
        require_styled(UNSTYLED_DOC, label="market map")
    message = str(excinfo.value)
    assert "market map" in message
    assert "lede" in message and "mm" in message
    assert "must ship the rule that styles it" in message


def test_class_names_inside_scripts_do_not_count_as_used():
    """A class mentioned only in JS must not satisfy the contract."""
    doc = ('<style>.real{}</style><div class="real"></div>'
           '<script>var x = "fake-class";</script>')
    assert unstyled(doc) == set()
    doc_bad = ('<style>.real{}</style><div class="real ghost"></div>'
               '<script>document.querySelector(".ghost")</script>')
    assert unstyled(doc_bad) == {"ghost"}, (
        "a class used in markup is unstyled even if the runtime names it")


def test_behavioural_classes_are_permitted_with_a_stated_reason():
    doc = '<style>.a{}</style><div class="a work-trigger"></div>'
    assert unstyled(doc) == set()
    for name, reason in BEHAVIOURAL.items():
        assert reason, f"{name} is permitted with no reason given"


def test_the_allow_list_is_explicit_not_a_wildcard():
    doc = '<style>.a{}</style><div class="a custom"></div>'
    assert unstyled(doc) == {"custom"}
    assert unstyled(doc, allow=("custom",)) == set()
