"""Research source display must preserve evidence without inventing freshness."""
import base64
from copy import deepcopy
from datetime import date
from html.parser import HTMLParser
import json

import pytest

from agents.assess.research_subjects import make_subject
from agents.leadgen.press import run_press
from agents.leadgen.press_html import render_html
from agents.leadgen.research_html import render_source_fields, source_link
from agents.reports.links import build_usaspending_award_link, parse_client_anchors
from agents.reports.lint import (
    lint_contact_rendering, lint_count_tokens, lint_counts, lint_emdash,
    lint_federal_link_construction, lint_sam_workspace_links,
)
from tests.test_research_subjects import AWARDS, build


class SourceFields(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.downloads = []
        self.contacts = []
        self.text = []

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == "a" and attrs.get("data-source-json"):
            self.downloads.append((attrs["data-source-json"], base64.b64decode(
                attrs["href"].split(",", 1)[1]).decode("utf-8")))
        if attrs.get("data-contact"):
            self.contacts.append(attrs)

    def handle_data(self, data):
        self.text.append(data)


def award(**extra):
    raw = deepcopy(AWARDS[0]["data"])
    raw.update(url=AWARDS[0]["sourceURL"], **extra)
    return make_subject("award", raw)


def test_native_research_html_meets_source_contact_and_count_contracts(monkeypatch):
    run, _, _ = build(monkeypatch)
    receipt = run_press(assess=run, client_name="NETSCOUT")
    before = receipt.model_dump_json()
    page = render_html(receipt)
    for check in (lint_federal_link_construction, lint_contact_rendering,
                  lint_count_tokens, lint_counts, lint_emdash, lint_sam_workspace_links):
        result = check(page)
        assert result.ok, (check.__name__, result.violations)
    assert receipt.model_dump_json() == before
    parsed = SourceFields()
    parsed.feed(page)
    originals = [s.source_payload_json for s in run.research.items]
    assert [raw for kind, raw in parsed.downloads if kind == "source"] == originals
    assert all(not p.lead_ids for p in receipt.parents if p.research_subject)
    assert parsed.contacts and {c["data-grade"] for c in parsed.contacts} == {"C"}
    assert all(c["data-source"].startswith("research:v1:") for c in parsed.contacts)
    assert "source observation date unknown" in page
    assert "current contact role remains unconfirmed" in page


def test_original_nested_source_and_context_json_survive_download_and_display():
    extra = {"path/key~": ["München & <original>", None, False, {"email": "history@agency.gov"}],
             "empty": {}, "unicode": "汉字"}
    raw = deepcopy(AWARDS[0]["data"])
    raw.update(url=AWARDS[0]["sourceURL"], source_details=extra)
    context = {"record": raw, "gap": "No current owner confirmed"}
    subject = make_subject("award", raw, discovery_context=context)
    before = subject.model_dump_json()
    page = render_source_fields(subject, as_of=date(2026, 9, 14))
    parsed = SourceFields()
    parsed.feed(page)
    assert dict(parsed.downloads) == {
        "source": subject.source_payload_json,
        "discovery-context": subject.discovery_context_json,
    }
    text = "".join(parsed.text)
    for value in ("München & <original>", "history@agency.gov", "汉字", "null", "false", "{}",
                  "/source_details/path~1key~0/0", "/record/source_details/unicode"):
        assert value in text
    assert "<original>" not in page
    assert subject.model_dump_json() == before
    assert lint_contact_rendering(page).ok and lint_federal_link_construction(page).ok


@pytest.mark.parametrize("observed", [None, "2000-01-01T00:00:00Z", "2099-01-01T00:00:00Z"])
def test_raw_contact_grade_never_borrows_source_retrieval_or_future_clock(observed):
    subject = award(retrieved_at=observed, contact_email="named@agency.gov", contact_phone="+1-202-555-0100")
    page = render_source_fields(subject, as_of=date(2026, 9, 14))
    parsed = SourceFields()
    parsed.feed(page)
    assert len(parsed.contacts) == 2
    assert all(c["data-grade"] == "C" for c in parsed.contacts)
    assert all(e.retrieved_at is None for e in subject.evidence)
    assert all(e.source_acquisition.basis == "none" for e in subject.evidence)
    assert subject.communication_permission == "none"


def test_source_link_uses_structured_award_identity_without_promoting_source_clock():
    subject = award()
    page = source_link(subject)
    anchor, = parse_client_anchors(page)
    assert anchor.href == build_usaspending_award_link(subject.source_record_id).url
    assert anchor.attrs["data-link-record"] == subject.source_record_id
    assert lint_federal_link_construction(page).ok
    assert subject.evidence[0].retrieved_at is None


@pytest.mark.parametrize("change", [
    {"source_record_id": "CONT_AWD_foreign_123"},
    {"source_url": "https://www.usaspending.gov/award/CONT_AWD_foreign_123"},
    {"source_url": "javascript:alert(1)"},
])
def test_copied_unbound_subject_cannot_receive_builder_proof(change):
    subject = award().model_copy(update=change)
    with pytest.raises(ValueError):
        source_link(subject)
    with pytest.raises(ValueError):
        render_source_fields(subject, as_of=date(2026, 9, 14))


def test_foreign_federal_link_stays_explicit_with_exact_original_download():
    subject = award(related_url="https://www.usaspending.gov/award/CONT_AWD_other_123")
    page = render_source_fields(subject, as_of=date(2026, 9, 14))
    parsed = SourceFields()
    parsed.feed(page)
    assert "Federal URL needs source reconciliation; see original JSON" in page
    assert "/related_url" in page
    assert dict(parsed.downloads)["source"] == subject.source_payload_json
    assert all("CONT_AWD_other_123" not in anchor.href for anchor in parse_client_anchors(page))
    assert lint_federal_link_construction(page).ok
    assert json.loads(subject.source_payload_json)["related_url"].endswith("CONT_AWD_other_123")


def test_native_parent_with_related_notice_context_survives_report_and_lint(monkeypatch):
    from agents.assess.ledger import build_assess_run
    from tests.test_research_subjects import research_sweep, NOW, BINDING
    sweep, profile = research_sweep(monkeypatch)
    record = sweep["results"]["incumbent_buyer_map"]["research_award_details"]["records"][0]
    other_id = "a" * 32
    related = f"https://sam.gov/opp/{other_id}/view"
    record["related_notice_url"] = related
    run, _, _ = build_assess_run("NETSCOUT", sweep, profile, BINDING, as_of=NOW)
    receipt = run_press(assess=run, client_name="NETSCOUT")
    subject = next(s for s in run.research.items
                   if json.loads(s.source_payload_json).get("related_notice_url") == related)
    parent = next(p for p in receipt.parents if p.subject_id == subject.subject_id)
    page = render_html(receipt)
    parsed = SourceFields()
    parsed.feed(page)
    assert parent.title in "".join(parsed.text) and not parent.lead_ids
    assert ("source", subject.source_payload_json) in parsed.downloads
    assert "/related_notice_url" in page
    assert "Federal URL needs source reconciliation; see original JSON" in page
    assert all(other_id not in a.href for a in parse_client_anchors(page))
    assert lint_federal_link_construction(page).ok
    assert lint_contact_rendering(page).ok
    assert "or permission to contact" not in page
