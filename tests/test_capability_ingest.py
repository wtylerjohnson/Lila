"""Reading the client's own product pages, instead of guessing what they sell.

THE ROOT CAUSE. `capability_terms.core` is hand-authored. For apexanalytix
someone typed eight marketing phrases; all eight matched ZERO of 330,641
federal notices, while the company's own site named fifty-plus real
capabilities that were never in the frame and therefore never searched.
Every widening, mining and tiering built before this was treating a symptom.
"""

from __future__ import annotations

import os
import sqlite3
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tools import capability_ingest as ci  # noqa: E402

_PAGE = """
<html><body>
  <h1>One Power Platform</h1>
  <h2>Unclaimed Property Asset Recovery</h2>
  <h2>Sales and Use Tax Recovery Audit</h2>
  <h3>Supplier Diversity</h3>
  <a href="/solutions/audit-recovery/">Accounts Payable Recovery Audit</a>
  <a href="/solutions/supplier-management/">Bank Account Validation</a>
  <a href="/solutions/">Learn More</a>
  <a href="/careers/">Careers</a>
  <h2>Ready to roar?</h2>
  <h2>Fill out our contact form and we will be in touch shortly.</h2>
</body></html>
"""


def test_it_reads_real_capability_names_from_the_page():
    found = ci.extract_capabilities(_PAGE)
    assert "Unclaimed Property Asset Recovery" in found
    assert "Sales and Use Tax Recovery Audit" in found
    assert "Supplier Diversity" in found
    assert "Bank Account Validation" in found


def test_navigation_furniture_is_not_a_capability():
    """NEGATIVE. "Learn More", "Careers" and marketing slogans are how a
    naive scrape fills a frame with nothing."""
    found = ci.extract_capabilities(_PAGE)
    for junk in ("Learn More", "Careers", "Ready to roar?",
                 "One Power Platform"):
        assert junk not in found


def test_a_sentence_is_not_a_capability_name():
    assert not ci._looks_like_capability(
        "Fill out our contact form and we will be in touch shortly.")
    assert ci._looks_like_capability("Supplier Risk Management")


def test_a_phrase_with_no_capability_marker_is_refused():
    """A capability name carries domain language. Without a marker any two
    words on the page would enter the frame."""
    assert not ci._looks_like_capability("Our Story")
    assert not ci._looks_like_capability("Press Room")
    assert ci._looks_like_capability("Insurance Coverage Monitoring")


def test_no_model_call_is_ever_made():
    """STRUCTURAL. A model reading a marketing site reproduces marketing
    language, which is exactly the input that failed. Extraction is
    structural, from headings and product-link text."""
    import ast
    from pathlib import Path
    tree = ast.parse(Path(ci.__file__).read_text(encoding="utf-8"))
    imported = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            imported.add(node.module or "")
        elif isinstance(node, ast.Import):
            imported.update(a.name for a in node.names)
    for forbidden in ("maxplan_cli", "anthropic", "openai"):
        assert not any(forbidden in name for name in imported)


def test_the_crawl_never_touches_the_network_in_a_test():
    """`fetcher` is injectable, which is what makes this suite hermetic."""
    pages = {"https://x.test": _PAGE}
    out = ci.ingest("https://x.test", fetcher=lambda url: pages.get(url))
    assert out["receipt"]["pages_fetched"] == 1
    assert out["receipt"]["model_calls"] == 0
    assert out["receipt"]["credits_spent"] == 0
    assert any(c["name"] == "Supplier Diversity"
               for c in out["capabilities"])


def test_an_unreachable_site_is_a_stated_absence_not_a_crash():
    out = ci.ingest("https://x.test", fetcher=lambda url: None)
    assert out["capabilities"] == []
    assert out["receipt"]["pages_failed"] >= 1


def test_a_missing_url_says_so():
    out = ci.ingest("", fetcher=lambda url: None)
    assert out["capabilities"] == []
    assert "no site url" in out["receipt"]["why"]


# ===== measurement: proposes with real yield, never applies ============== #
@pytest.fixture()
def store():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute("CREATE TABLE notices (title TEXT, description_prefix TEXT)")
    for _ in range(12):
        conn.execute("INSERT INTO notices VALUES (?,?)",
                     ("Sales and Use Tax Recovery Audit Services", ""))
    conn.execute("INSERT INTO notices VALUES (?,?)", ("Grounds mowing", ""))
    conn.commit()
    yield conn
    conn.close()


def test_a_capability_with_federal_demand_is_proposed(store):
    out = ci.measure([{"name": "Sales and Use Tax Recovery Audit"}], store)
    assert out["proposed"][0]["federal_notices"] == 12


def test_a_capability_already_in_the_frame_is_not_re_proposed(store):
    out = ci.measure([{"name": "Sales and Use Tax Recovery Audit"}], store,
                     existing=["sales and use tax recovery audit"])
    assert out["proposed"] == []
    assert out["rows"][0]["in_frame"] is True


def test_a_capability_with_no_federal_demand_is_recorded_not_dropped(store):
    """The company sells it and the government is not currently buying it.
    That is a finding, not an omission, and it belongs in the receipt."""
    out = ci.measure([{"name": "Escheatment Avoidance Services"}], store)
    assert out["proposed"] == []
    assert out["receipt"]["no_current_federal_demand"] == 1
    assert out["rows"][0]["capability"] == "Escheatment Avoidance Services"
