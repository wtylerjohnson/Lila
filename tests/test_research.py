"""Company-research tests (Step 1b): parallel site + web workers, soft failures.

Offline — fake engine + monkeypatched scraper. No keys, no network.
"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import agents.company_research as cr  # noqa: E402
from agents.company_research import WebsiteGuess, research_company  # noqa: E402
from tools.scrape.site import ScrapeBundle, ScrapedPage  # noqa: E402


class FakeEngine:
    """Stands in for DecisionEngine: canned web research + site guess."""

    def __init__(self, guess_url=None, findings="Acme does federal IT.", citations=None):
        self.guess_url = guess_url
        self.findings = findings
        self.citations = citations or ["https://example.com/news"]
        self.web_calls = 0

    def web_research(self, *, system_prompt, query, max_uses=5):
        self.web_calls += 1
        return self.findings, self.citations

    def structure(self, *, instructions, findings, schema):
        return WebsiteGuess(url=self.guess_url, confidence=0.9 if self.guess_url else 0.0)


def _fake_scrape(url, max_pages=5):
    return ScrapeBundle(
        root_url=url,
        pages=[ScrapedPage(
            url=url,
            text="We build systems for federal agencies and commercial networks.",
        )],
        sources=[url])


def test_website_from_form_is_scraped_not_searched(monkeypatch):
    monkeypatch.setattr(cr, "scrape_site", _fake_scrape)
    engine = FakeEngine(guess_url="https://should-not-be-used.com")
    r = research_company("Acme", website="https://acme.com", engine=engine)
    assert r.website == "https://acme.com"
    assert r.website_source == "form"
    assert r.scrape and r.scrape.pages
    assert r.web_findings  # web worker ran in parallel
    assert engine.web_calls == 1  # only the company search — no find-website call


def test_missing_website_is_discovered_then_scraped(monkeypatch):
    monkeypatch.setattr(cr, "scrape_site", _fake_scrape)
    engine = FakeEngine(guess_url="https://acme.io")
    r = research_company("Acme", website=None, engine=engine)
    assert r.website == "https://acme.io"
    assert r.website_source == "web_search"
    assert r.scrape and r.scrape.root_url == "https://acme.io"


def test_unfindable_website_does_not_block_web_research(monkeypatch):
    monkeypatch.setattr(cr, "scrape_site", _fake_scrape)
    engine = FakeEngine(guess_url=None)
    r = research_company("Ghost LLC", website=None, engine=engine)
    assert r.website is None
    assert r.website_source == "not_found"
    assert r.scrape is None
    assert r.web_findings  # the other worker still delivered


def test_worker_exception_is_soft(monkeypatch):
    def boom(url, max_pages=5):
        raise RuntimeError("site exploded")

    monkeypatch.setattr(cr, "scrape_site", boom)
    engine = FakeEngine()
    r = research_company("Acme", website="https://acme.com", engine=engine)
    assert r.scrape is None
    assert any("site exploded" in e for e in r.errors)
    assert r.web_findings  # intake proceeds on what survived


def test_flags_can_disable_workers():
    r = research_company("Acme", website="https://acme.com", do_scrape=False, do_web=False)
    assert r.scrape is None and r.web_findings == ""


def test_low_confidence_website_guess_is_not_scraped(monkeypatch):
    """Website guess confidence is a hard gate, not a hint (Step 1)."""
    scraped = []

    def track(url, max_pages=5):
        scraped.append(url)
        return _fake_scrape(url, max_pages=max_pages)

    monkeypatch.setattr(cr, "scrape_site", track)

    class WeakEngine(FakeEngine):
        def structure(self, *, instructions, findings, schema):
            return WebsiteGuess(
                url="https://wrong-company.directory",
                confidence=0.2,
                rationale="directory hit",
            )

    r = research_company("Ghost LLC", website=None, engine=WeakEngine())
    assert r.website is None
    assert r.website_source == "not_found"
    assert scraped == []
    assert any("bind floor" in e for e in r.errors)
    assert r.web_findings  # web worker still runs on the legacy helper
