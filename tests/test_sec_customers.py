"""Bound-CIK SEC customer roster: promote roster sentences, drop underwriters."""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from agents.intake.extract import (
    customer_excerpt_is_sec_paraphrase,
    customer_url_is_chrome,
    is_customer_category_phrase,
    is_customer_chrome,
    is_customer_name,
    is_customer_roster_excerpt,
)
from agents.intake.sec_customers import (
    extract_roster_customers,
    fetch_bound_sec_customers,
    filing_document_url,
    filing_has_named_roster,
    is_bound_sec_filing_url,
    list_customer_filings,
    resolve_bound_cik,
)


SEC_URL = (
    "https://www.sec.gov/Archives/edgar/data/1596532/"
    "000119312514227698/d639957d424b4.htm"
)
ROSTER = (
    "Our customers include large cloud providers such as Microsoft "
    "and Netflix and financial services organizations such as "
    "Barclays, Citigroup, and Morgan Stanley."
)
UNDERWRITER = (
    "Morgan Stanley, Citigroup, BofA Merrill Lynch, Barclays and "
    "Credit Suisse are acting as joint book-running managers for "
    "the offering."
)
TEN_K_CATEGORY = (
    "Our Customers Our customers include large cloud customers or Cloud "
    "and AI Titans, other internet and service providers, including "
    "specialty and AI Neoclouds, and a wide breadth of enterprise "
    "customers, including financial services organizations and "
    "government agencies."
)
BROWSE_ATOM = (
    '<?xml version="1.0"?><feed><entry><link href="'
    "https://www.sec.gov/Archives/edgar/data/1596532/"
    '000119312514227698/0001193125-14-227698-index.htm"/></entry></feed>'
)


def test_roster_sentence_promotes_banks_not_underwriters():
    filing = f"<html><p>{ROSTER}</p><table>{UNDERWRITER}</table></html>"
    names = {n.casefold(): ex for n, ex in extract_roster_customers(filing)}
    assert {"barclays", "citigroup", "morgan stanley", "microsoft"} <= set(names)
    for excerpt in names.values():
        assert is_customer_roster_excerpt(excerpt)
        assert "book-running" not in excerpt.casefold()
    only_uw = extract_roster_customers(UNDERWRITER)
    assert only_uw == []


def test_wrong_cik_and_non_edgar_urls_rejected():
    assert is_bound_sec_filing_url(SEC_URL, "0001596532")
    assert not is_bound_sec_filing_url(SEC_URL, "0000123456")
    assert not is_bound_sec_filing_url(
        "https://www.arista.com/en/company/customers", "0001596532")
    assert filing_document_url(
        "0001596532", "0001193125-14-227698", "d639957d424b4.htm"
    ) == SEC_URL


def test_resolve_cik_matches_bound_issuer_only():
    tickers = {
        "0": {"cik_str": 1596532, "ticker": "ANET",
              "title": "Arista Networks, Inc."},
        "1": {"cik_str": 21344, "ticker": "KO",
              "title": "Coca-Cola Co"},
    }
    assert resolve_bound_cik("Arista Networks", tickers=tickers) == "0001596532"
    assert resolve_bound_cik("Acme Net", tickers=tickers) is None


def test_fetch_prefers_424b4_roster_over_nameless_10k():
    tickers = {
        "0": {"cik_str": 1596532, "title": "Arista Networks, Inc."},
    }
    submissions = {
        "filings": {
            "recent": {
                "form": ["10-K", "424B4"],
                "accessionNumber": [
                    "0001628280-24-000000",
                    "0001193125-14-227698",
                ],
                "primaryDocument": ["anet-20231231.htm", "d639957d424b4.htm"],
            }
        }
    }
    ten_k = "Item 1. Business. We sell to cloud titans and enterprises."

    def fetch_json(url, **_kw):
        if "company_tickers" in url:
            return tickers
        if "submissions" in url:
            return submissions
        raise AssertionError(url)

    def fetch_text(url, **_kw):
        if "d639957d424b4" in url:
            return ROSTER
        if "anet-20231231" in url:
            return ten_k
        raise AssertionError(url)

    hits = fetch_bound_sec_customers(
        "Arista Networks",
        fetch_json=fetch_json,
        fetch_text=fetch_text,
        tickers=tickers,
    )
    names = {h["name"].casefold() for h in hits}
    assert {"barclays", "citigroup", "morgan stanley"} <= names
    assert all("424b4" in h["url"].casefold() for h in hits)
    assert all(h["cik"] == "0001596532" for h in hits)


def test_list_filings_orders_ipo_roster_ahead_of_10k():
    rows = list_customer_filings({
        "filings": {
            "recent": {
                "form": ["10-K", "S-1", "424B4"],
                "accessionNumber": ["a", "b", "c"],
                "primaryDocument": ["k.htm", "s.htm", "b4.htm"],
            }
        }
    })
    assert [r[0] for r in rows][:2] == ["424B4", "S-1"]


def test_category_only_10k_is_not_a_named_roster():
    assert extract_roster_customers(TEN_K_CATEGORY) == []
    assert filing_has_named_roster(TEN_K_CATEGORY) is False
    for junk in (
        "AI Neoclouds", "Our Customers Our", "Cloud and AI Titans",
        "financial services organizations", "government agencies",
        "enterprise customers",
    ):
        assert is_customer_category_phrase(junk)
        assert not is_customer_name(junk)


def test_fetch_walks_past_category_10k_to_browse_424b4():
    """Recent FY2025 10-K has no proper nouns; browse still finds the IPO."""
    tickers = {
        "0": {"cik_str": 1596532, "title": "Arista Networks, Inc."},
    }
    submissions = {
        "filings": {
            "recent": {
                "form": ["10-K"],
                "accessionNumber": ["0001628280-25-000000"],
                "primaryDocument": ["anet-20251231.htm"],
            },
            "files": [],
        }
    }

    def fetch_json(url, **_kw):
        if "company_tickers" in url:
            return tickers
        if "submissions" in url:
            return submissions
        if "search-index" in url:
            return {"hits": {"hits": []}}
        raise AssertionError(url)

    def fetch_text(url, **_kw):
        if "browse-edgar" in url and "424B4" in url:
            return BROWSE_ATOM
        if "browse-edgar" in url:
            return "<feed></feed>"
        if "anet-20251231" in url:
            return TEN_K_CATEGORY
        if "000119312514227698" in url or "d639957d424b4" in url:
            return "<FILENAME>d639957d424b4.htm\n" + ROSTER
        raise AssertionError(url)

    hits = fetch_bound_sec_customers(
        "Arista Networks",
        fetch_json=fetch_json,
        fetch_text=fetch_text,
        tickers=tickers,
    )
    names = {h["name"].casefold() for h in hits}
    assert {"barclays", "citigroup", "morgan stanley"} <= names
    assert "ai neoclouds" not in names
    assert "our customers our" not in names
    assert all("d639957d424b4.htm" in h["url"] for h in hits)
    assert all(h["form"] == "424B4" for h in hits)


def test_support_chrome_and_sec_paraphrase_are_rejected():
    assert customer_url_is_chrome("https://www.arista.com/en/support/acare")
    assert customer_url_is_chrome("https://www.arista.com/en/company")
    assert not customer_url_is_chrome(
        "https://www.arista.com/en/company/customers")
    assert not customer_url_is_chrome(
        "https://www.arista.com/assets/data/pdf/CaseStudies/Activ.pdf")
    assert not customer_url_is_chrome(SEC_URL)
    assert is_customer_chrome("Quick Facts")
    assert is_customer_chrome("Forrester Wave")
    assert is_customer_chrome("Support Engineering Finance")
    assert is_customer_chrome("Data Center Network Solutions")
    assert not is_customer_name("A-Care")
    assert customer_excerpt_is_sec_paraphrase(
        "Named in Arista's 2014 S-1/10-K: Barclays")
    assert not customer_excerpt_is_sec_paraphrase(ROSTER)
