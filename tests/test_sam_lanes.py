"""Brand-name SAM lanes (2026-07-27).

Every fixture here is shaped from a real notice observed in the 2026-07-27
daily extract, including both false positives the guards were built from.
"""

from __future__ import annotations

import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from agents.golden_press import sam_lanes as sl  # noqa: E402

ENTITIES = {
    "product": ["Catalyst", "Webex", "SteelHead", "Riverbed AppResponse"],
    "competitor": ["Palo Alto Networks", "Pure Storage"],
    "reseller": ["Carahsoft"],
}


def _row(**kw):
    base = dict(notice_id="f689cddaf90b4fe18b530963a838925e",
                type="Combined Synopsis/Solicitation", title="Tactical Gear Kit",
                description="", agency="DEPT OF DEFENSE", subtier="DEPT OF THE ARMY",
                office="W6QM", posted="2026-07-20", deadline="2026-08-15",
                url="https://sam.gov/workspace/contract/opp/"
                    "f689cddaf90b4fe18b530963a838925e/view")
    base.update(kw)
    return base


# ---- the guards, each built from a real false positive ---------------------- #
def test_dictionary_word_product_needs_the_vendor_named():
    """"SteelHead" matched "NOTICE OF INTENT TO SOLE-SOURCE TO INNOVASEA", an
    aquaculture buy, because steelhead is a trout. The joined lowercase form
    is what must be tested, not the camel-cased one."""
    words, _ = sl.load_dictionary()
    assert sl.is_distinctive("SteelHead", words) is False
    assert sl.is_distinctive("Aternity", words) is True
    assert sl.is_distinctive("Riverbed AppResponse", words) is True


def test_ambiguous_name_is_dropped_without_the_vendor():
    rows = [_row(title="Sole source to Innovasea",
                 description="Notice of intent to sole-source steelhead "
                             "monitoring equipment for fisheries.")]
    hits, receipt = sl.scan_extract(rows, {"product": ["SteelHead"]},
                                    vendor="Riverbed")
    assert hits == []
    assert receipt["ambiguous_entities"] == ["SteelHead"]


def test_ambiguous_name_counts_once_the_vendor_appears():
    rows = [_row(description="Intent to sole source Riverbed SteelHead "
                             "appliances for WAN optimization.")]
    hits, _ = sl.scan_extract(rows, {"product": ["SteelHead"]}, vendor="Riverbed")
    assert [h["entity"] for h in hits] == ["SteelHead"]


def test_reseller_alone_is_not_a_signal_about_this_client():
    """Carahsoft resells for hundreds of vendors. A Carahsoft justification
    for ServiceNow says nothing about a Riverbed account."""
    rows = [_row(type="Justification", title="ServiceNow BNJ and Sole Source",
                 description="Sole source justification to Carahsoft for "
                             "ServiceNow licences.")]
    hits, _ = sl.scan_extract(rows, {"reseller": ["Carahsoft"]}, vendor="Riverbed")
    assert hits == []


def test_reseller_counts_when_it_carries_the_vendor():
    rows = [_row(type="Justification",
                 description="Sole source to Carahsoft for Riverbed "
                             "AppResponse maintenance renewal.")]
    hits, _ = sl.scan_extract(
        rows, {"reseller": ["Carahsoft"], "product": ["Riverbed AppResponse"]},
        vendor="Riverbed")
    assert {h["entity"] for h in hits} == {"Carahsoft", "Riverbed AppResponse"}


# ---- lanes ----------------------------------------------------------------- #
@pytest.mark.parametrize("notice_type,text,expected", [
    ("Justification", "any text", "posted_ja"),
    ("Justification and Approval (J&A)", "any text", "posted_ja"),
    ("Special Notice", "NOTICE OF INTENT TO SOLE-SOURCE", "sole_source_intent"),
    ("Solicitation", "only one responsible source", "sole_source_intent"),
    ("Presolicitation", "Brand Name or Equal To", "brand_name_or_equal"),
    ("Sources Sought", "market research", "sources_sought"),
])
def test_lane_classification(notice_type, text, expected):
    assert expected in sl.classify_lanes(notice_type, text)


def test_or_equal_alone_is_too_loose_to_be_a_brand_lane():
    """"or equal" appears in ordinary specification language. The lane needs
    the phrase "brand name" or the FAR term of art."""
    assert "brand_name_or_equal" not in sl.classify_lanes(
        "Solicitation", "tolerance of 5mm or equal to the sample provided")


# ---- subject vs incidental ------------------------------------------------- #
def test_product_beside_the_restriction_is_the_subject():
    """Real notice: "Brand Name Requirement for the following specific
    components: Cisco Systems Networking Hardware Catalyst 9300 switches"."""
    rows = [_row(description=(
        "This is a Brand Name Requirement for the following specific "
        "components: Cisco Systems Networking Hardware Catalyst 9300 "
        "switches. No substitutions will be accepted."))]
    hits, _ = sl.scan_extract(rows, {"product": ["Catalyst"]}, vendor="Cisco")
    assert hits and hits[0]["strength"] == "subject"
    assert hits[0]["signal_distance"] < sl.SUBJECT_WINDOW


def test_product_far_from_the_restriction_is_only_mentioned():
    """Real notice: a VA software RFI naming Webex as the training delivery
    platform, thousands of characters from any brand-name language."""
    filler = "requirement text. " * 200
    rows = [_row(type="Sources Sought",
                 description=("Brand name evaluation criteria apply. " + filler
                              + "Provide training delivered via WebEx."))]
    hits, _ = sl.scan_extract(rows, {"product": ["Webex"]}, vendor="Cisco")
    assert hits and all(h["strength"] == "mentioned" for h in hits)


# ---- links ----------------------------------------------------------------- #
def test_notice_url_is_public_never_the_workspace_form():
    """The extract's own link column emits a workspace URL that requires a
    login. The linkage law wants the public form built from the notice id."""
    url = sl.canonical_notice_url(_row())
    assert url == ("https://sam.gov/opp/"
                   "f689cddaf90b4fe18b530963a838925e/view")
    assert "workspace" not in url


def test_unusable_notice_id_falls_back_and_never_fabricates():
    assert sl.canonical_notice_url(
        {"notice_id": "not-a-guid", "url": "https://example.gov/x"}
    ) == "https://example.gov/x"


# ---- aliases --------------------------------------------------------------- #
def test_aliases_cover_the_forms_a_notice_actually_uses():
    forms = sl.aliases("Riverbed AppResponse", vendor="Riverbed")
    assert "AppResponse" in forms          # vendor prefix dropped
    assert "App Response" in forms         # camel split
    assert "Riverbed AppResponse" in forms


def test_matching_is_whole_token():
    rows = [_row(description="Brand name requirement for a catalystic "
                             "converter assembly for Cisco vehicles.")]
    hits, _ = sl.scan_extract(rows, {"product": ["Catalyst"]}, vendor="Cisco")
    assert hits == [], "'catalystic' is not 'Catalyst'"


# ---- receipts: the zeros are the point ------------------------------------- #
def test_receipt_reports_every_searched_entity_including_zeros():
    """"We searched twelve product names across four lanes and found nothing"
    is a real answer. An empty section is not."""
    rows = [_row(type="Sources Sought", description="unrelated requirement")]
    _, receipt = sl.scan_extract(rows, ENTITIES, vendor="Cisco")
    assert receipt["entities_searched"] == 7
    for lane in receipt["lanes"]:
        assert len(lane["entities"]) == 7
        assert all(e["count"] == 0 for e in lane["entities"])
    assert receipt["metered_quota_spent"] == 0
    assert receipt["notices_scanned"] == 1


def test_receipt_names_the_lanes_even_when_the_scan_is_empty():
    _, receipt = sl.scan_extract([], ENTITIES, vendor="Cisco")
    assert [l["lane"] for l in receipt["lanes"]] == [n for n, _ in sl.LANES]


# ---- the durable ledger ---------------------------------------------------- #
def test_ledger_round_trips_and_is_idempotent(tmp_path):
    rows = [_row(description="Brand name requirement: Cisco Catalyst 9300.")]
    hits, _ = sl.scan_extract(rows, {"product": ["Catalyst"]}, vendor="Cisco")
    first = sl.merge_into_ledger("acme", hits, root=tmp_path)
    assert first["added"] == len(hits)
    second = sl.merge_into_ledger("acme", hits, root=tmp_path)
    assert second["added"] == 0, "a same-day rescan must not duplicate"
    assert second["refreshed"] == len(hits)
    assert second["total"] == first["total"]


def test_ledger_keeps_a_notice_sam_has_already_dropped(tmp_path):
    """THE WHOLE POINT. Measured on the two extracts on disk: 5,191 notices
    present on 2026-07-24 were gone by 2026-07-27, 6.4% in three days,
    including 12% of all Justifications. A notice posted and closed between
    two pulls is invisible to any snapshot-only scan."""
    rows = [_row(description="Brand name requirement: Cisco Catalyst 9300.")]
    hits, _ = sl.scan_extract(rows, {"product": ["Catalyst"]}, vendor="Cisco")
    sl.merge_into_ledger("acme", hits, root=tmp_path)
    # the notice is gone from a later extract: scan finds nothing
    later, _ = sl.scan_extract([], {"product": ["Catalyst"]}, vendor="Cisco")
    assert later == []
    merged = sl.merge_into_ledger("acme", later, root=tmp_path)
    assert merged["total"] == len(hits), "the vanished notice is still held"
    assert sl.load_ledger("acme", root=tmp_path)


def test_missing_ledger_is_empty_not_an_error(tmp_path):
    assert sl.load_ledger("never_scanned", root=tmp_path) == {}


def test_torn_ledger_line_loses_one_hit_not_the_file(tmp_path):
    path = sl.ledger_path("acme", tmp_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text('{"notice_id":"a","lane":"posted_ja","entity":"X"}\n'
                    '{"notice_id":"b","lane":"posted_ja"\n'
                    '{"notice_id":"c","lane":"posted_ja","entity":"Z"}\n',
                    encoding="utf-8")
    assert len(sl.load_ledger("acme", tmp_path)) == 2


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
