"""The Market Map must be viewable, not merely correct.

Every other test in this suite asks whether the document is RIGHT. These ask
whether a client can look at it: that the classes it emits are actually
styled by the stylesheet that ships with it, that no client's data rides into
another client's file, and that a database key is never presented as an
identifier.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from agents.golden_press.market_map_render import (
    SECTIONS, _resolving_source_url, _seal, coverage_items,
    display_identifier, render_sections, ticker_items, work_details,
)
from agents.golden_press.market_map_skeleton import (
    EMPTY_SLOT, EditIds, build_document, load_css, load_runtime, logo_slot,
)
from agents.golden_press.market_map_projection import build_market_map
from agents.golden_press.market_map_projection import _events
from agents.golden_press.records import EvidencePack
from agents.golden_press.style_contract import (
    standalone_classes, unstyled_in_context,
)

REPO = Path(__file__).resolve().parents[1]
SLUG = "apexanalytix"


def _pack_path(slug: str) -> Path:
    return (REPO / "data" / "state" / "candidate_review_v1" / slug
            / f"{slug}.golden_report.evidence_pack.json")


@pytest.fixture(scope="module")
def document():
    path = _pack_path(SLUG)
    if not path.exists():
        pytest.skip(f"no evidence pack for {SLUG}")
    pack = EvidencePack.model_validate(json.loads(path.read_text()))
    profile = json.loads(
        (REPO / "clients" / SLUG / "profile.json").read_text())
    doc = build_market_map(pack, profile=profile, slug=SLUG,
                           as_of="2026-08-07")
    ids = EditIds()
    html = build_document(
        client_name=SLUG, slug=SLUG, stamp="2026-08-07",
        title="Federal Market Map", standfirst="What we know.",
        edition=f"{SLUG} · All Federal", edition_note="Research opened.",
        coverage=coverage_items(doc), sections=SECTIONS,
        content=render_sections(doc, ids), ticker_items=ticker_items(doc),
        work_details=work_details(doc), ids=ids)
    return doc, html


def test_every_emitted_class_is_actually_styled(document):
    """The check that would have caught the unstyled artifact instantly.

    Context aware on purpose: `.money` is styled only as `.market-proof
    .money`, so emitting it in a table cell styles nothing. A substring
    match on the stylesheet calls that fine and it is not.
    """
    _doc, html = document
    problems = unstyled_in_context(html)
    assert not problems, (
        "these classes reach the page with no rule that can match them: "
        + "; ".join(f"{name} ({why})" for name, why in problems))


def test_the_stylesheet_actually_ships_in_the_document(document):
    _doc, html = document
    assert "<style>" in html
    assert len(load_css()) > 20_000
    assert len(standalone_classes(load_css())) > 60


def test_no_raw_database_key_is_shown_as_an_identifier(document):
    """A 32-character hex string is our key, not a federal identifier."""
    _doc, html = document
    body = re.sub(r"<script.*?</script>", "", html, flags=re.S | re.I)
    text = re.sub(r"<[^>]+>", " ", body)
    assert not re.findall(r"\b[0-9a-f]{32}\b", text)


def test_display_identifier_returns_empty_rather_than_a_placeholder():
    """A placeholder reads as data. "Open the record" once rendered in a
    ticker as though it were the solicitation number."""

    class _Opp:
        identifier = "2b004cf8a914455eb70bda4d0acd3f1d"
        evidence = ()

    assert display_identifier(_Opp()) == ""


def test_display_identifier_prefers_the_solicitation_number():
    class _Ref:
        source_id = "HHM402-2X-Audit"
        url = ""

    class _Opp:
        identifier = "2b004cf8a914455eb70bda4d0acd3f1d"
        evidence = (_Ref(),)

    assert display_identifier(_Opp()) == "HHM402-2X-Audit"


def test_nonresolving_usaspending_page_uses_official_exact_id_api():
    broken = (
        "https://www.usaspending.gov/award/"
        "CONT_AWD_W912PL25CA018_9700_-NONE-_-NONE-")
    assert _resolving_source_url(broken) == (
        "https://api.usaspending.gov/api/v2/awards/"
        "CONT_AWD_W912PL25CA018_9700_-NONE-_-NONE-/")
    normal = (
        "https://www.usaspending.gov/award/"
        "CONT_AWD_47QTCA20D0001_4732_GS00Q17GWD2000_0")
    assert _resolving_source_url(normal) == normal


def test_event_reposts_collapse_to_latest_notice():
    """One event family renders once even when SAM republishes its notice."""
    pack = type("Pack", (), {"events": [
        {
            "event_id": "older",
            "name": "Enterprise Network Industry Day",
            "url": "https://sam.gov/opp/older/view",
            "posted_date": "2026-07-27",
            "registration_deadline": "2026-07-29",
        },
        {
            "event_id": "newer",
            "name": "  Enterprise   Network Industry Day ",
            "url": "https://sam.gov/opp/newer/view",
            "posted_date": "2026-07-29",
            "registration_deadline": "2026-07-30",
        },
    ]})()

    rows = _events(pack)

    assert len(rows) == 1
    assert rows[0].url == "https://sam.gov/opp/newer/view"


def test_logo_slots_ship_resolved_or_with_a_visible_fallback(document):
    """No identity surface may render as an invisible one-pixel image."""
    _doc, html = document
    slots = re.findall(
        r'<div class="logo-slot[^"]*"[^>]*>.*?</div>', html, flags=re.S)
    assert slots, "the document should carry fillable logo slots"
    for slot in slots:
        if "has-image" in slot:
            assert 'src="data:image/' in slot and EMPTY_SLOT not in slot
            continue
        fallback = re.search(
            r'<span class="logo-fallback"[^>]*>([^<]+)</span>', slot)
        assert "is-unmatched" in slot
        assert "<img" not in slot
        assert fallback and fallback.group(1).strip(), (
            "an unresolved mark has no visible identity fallback")


def test_template_and_curated_authority_marks_render_real_images(document):
    """The fixed GTM mark and a catalogued federal seal are not blank."""
    _doc, html = document
    gtm = re.search(
        r'<div class="[^"]*has-image[^"]*" '
        r'data-logo-slot-id="gtm-header-logo".*?</div>', html, flags=re.S)
    assert gtm and EMPTY_SLOT not in gtm.group(0)

    dod = _seal("Department of Defense", "test-dod")
    assert "has-image" in dod
    assert EMPTY_SLOT not in dod


def test_unknown_mark_is_a_monogram_not_a_broken_image():
    slot = logo_slot("unknown", "Example Systems", required=False)
    assert "is-unmatched" in slot
    assert ">ES</span>" in slot
    assert "<img" not in slot


def test_no_other_clients_data_rides_along(document):
    """The source artifact carried 775KB of one client's saved edits."""
    _doc, html = document
    for name in ("riverbed", "Riverbed", "RIVERBED"):
        assert name not in html
    assert "embeddedState" not in html or '"fields"' not in html


def test_the_runtime_is_generic_and_has_an_injection_point():
    runtime = load_runtime()
    assert "__MARKET_MAP_DATA__" in runtime
    assert "riverbed" not in runtime.lower()
    assert "usaspending.gov/award/CONT_AWD" not in runtime, (
        "the runtime carries a hard-coded award ledger belonging to one "
        "client")


def test_every_work_trigger_has_a_drawer_entry(document):
    """A trigger with no entry opens an empty drawer, which reads as broken."""
    doc, html = document
    keys = set(re.findall(r'data-work="([^"]+)"', html))
    details = set(work_details(doc))
    missing = keys - details
    assert not missing, f"work triggers with no drawer entry: {sorted(missing)}"
    assert "report" in details, (
        "the runtime falls back to workDetails.report for an unknown key")


def test_every_drawer_entry_has_the_four_fields_the_runtime_reads(document):
    doc, _html = document
    for key, entry in work_details(doc).items():
        missing = {"title", "summary", "formula", "sources"} - set(entry)
        assert not missing, f"{key} is missing {sorted(missing)}"


def test_editable_field_ids_are_unique(document):
    _doc, html = document
    ids = re.findall(r'data-edit-id="([^"]+)"', html)
    assert ids
    assert len(ids) == len(set(ids))


def test_figures_are_not_editable(document):
    """A hand-edited figure would contradict the record it links to."""
    _doc, html = document
    for button in re.findall(r"<button[^>]*work-trigger[^>]*>", html):
        assert "data-edit-id" not in button
        assert "contenteditable" not in button


def test_the_document_is_self_contained(document):
    """It is emailed. No CDN, no external stylesheet, no remote image."""
    _doc, html = document
    assert "<link" not in html
    assert "<script src" not in html
    assert not re.findall(r'src="https?://', html)
    assert "@import" not in html


def test_plain_head_emits_exactly_three_grid_children(document):
    """`.plain-head` is a three-column grid; a fourth child breaks it."""
    _doc, html = document
    for head in re.findall(r'<div class="plain-head">(.*?)</div>\s*<',
                           html, flags=re.S):
        depth, children = 0, 0
        for tag in re.findall(r"<(/?)(\w+)", head):
            if tag[0]:
                depth -= 1
            else:
                if depth == 0:
                    children += 1
                depth += 1
        assert children <= 3, f"plain-head has {children} grid children"
