"""L1 notice lane read from the durable store. Offline, no network, no quota."""

from __future__ import annotations

import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from agents.golden_press.l1_store import run_l1_from_store  # noqa: E402
from tools import notice_store as ns  # noqa: E402

CAPS = ["network monitoring", "container orchestration"]


def _insert(conn, nid, *, ntype="Sources Sought", naics="541519", psc="DA01",
            title="Network monitoring services", desc="network monitoring",
            agency="DEPT OF X", deadline="2026-09-01"):
    with conn:
        conn.execute(
            "INSERT INTO notices (notice_id, title, notice_type, agency, "
            "subtier, office, posted, deadline, naics, psc, set_aside, url, "
            "description_prefix, description_sha256, description_len, "
            "first_seen, last_seen) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (nid, title, ntype, agency, "SUB", "OFFICE-1", "2026-07-01",
             deadline, naics, psc, "SDVOSB", f"https://sam.gov/opp/{nid}/view",
             desc, "", len(desc), "2026-07-27", "2026-07-28"))


@pytest.fixture()
def store(tmp_path):
    return ns.connect(tmp_path / "notices.db")


def test_a_matching_notice_comes_back_fully_populated(store):
    _insert(store, "a1")
    recs, queries, receipt = run_l1_from_store(CAPS, conn=store)
    assert len(recs) == 1
    r = recs[0]
    assert r.lane == "L1_notice"
    assert r.notice_type == "Sources Sought"
    assert r.notice_leverage_rank == 1
    assert r.response_deadline == "2026-09-01"
    assert r.set_aside == "SDVOSB"
    assert r.office == "OFFICE-1"
    assert r.url == "https://sam.gov/opp/a1/view"   # canonical, not workspace
    assert r.relevance_method == "notice_store_capability_search"
    assert receipt["metered_quota_spent"] == 0


def test_award_notices_never_enter_the_solicitation_lane(store):
    _insert(store, "sought", ntype="Sources Sought")
    _insert(store, "award", ntype="Award Notice")
    recs, _, receipt = run_l1_from_store(CAPS, conn=store)
    assert [r.record_id for r in recs] == ["sought"]
    assert receipt["rank_4_excluded"] == 1


def test_award_notices_are_available_to_the_evidence_question(store):
    """Rank 4 is not discarded, it is a different question."""
    _insert(store, "award", ntype="Award Notice")
    recs, _, _ = run_l1_from_store(CAPS, conn=store, include_awards=True)
    assert [r.record_id for r in recs] == ["award"]
    assert recs[0].notice_leverage_rank == 4


def test_out_of_boundary_notices_are_not_offered(store):
    _insert(store, "inside", naics="541519", psc="DA01")
    _insert(store, "outside", naics="236220", psc="Y1JZ")   # construction
    recs, _, _ = run_l1_from_store(CAPS, conn=store)
    assert [r.record_id for r in recs] == ["inside"]


def test_client_boundary_also_qualifies_the_term_tier(store):
    """A language-services code must not be admitted and then rejected by
    the global technology-code gate."""
    _insert(store, "dlite", ntype="Solicitation", naics="541930", psc="R608",
            title="DLITE III Draft Solicitation",
            desc="translation interpretation and language training services")
    recs, queries, receipt = run_l1_from_store(
        ["language training"], conn=store, naics_boundary=["541930"])
    assert [record.record_id for record in recs] == ["dlite"]
    assert receipt["qualifying_code_basis"] == "client"
    assert queries[0].body["naics_prefixes"] == ["541930"]
    assert queries[0].body["qualifying_code_basis"] == "client"


def test_client_boundary_does_not_admit_wrong_domain_phrase_collision(store):
    _insert(store, "real", naics="611630", psc="U009",
            title="Language Instructor Services", desc="language training")
    _insert(store, "building", naics="236220", psc="Z2JZ",
            title="Building Maintenance at Japanese Language Training Center",
            desc="language training center facility maintenance")
    recs, _, _ = run_l1_from_store(
        ["language training"], conn=store,
        naics_boundary=["541930", "611630"])
    assert [record.record_id for record in recs] == ["real"]


def test_client_exclusions_and_clock_apply_before_l1_selection(store):
    _insert(store, "current", naics="541930", psc="R608",
            title="Translation and Interpretation Services",
            desc="spoken translation and interpretation", deadline="2026-09-01")
    _insert(store, "asl", naics="541930", psc="R608",
            title="American Sign Language Interpretation Services",
            desc="sign language interpretation", deadline="2026-09-01")
    _insert(store, "closed", naics="541930", psc="R608",
            title="Translation and Interpretation Services",
            desc="spoken translation and interpretation", deadline="2026-08-19")
    recs, _, receipt = run_l1_from_store(
        ["translation and interpretation"], conn=store,
        naics_boundary=["541930"], excluded_terms=["sign language"],
        as_of="2026-08-20")
    assert [record.record_id for record in recs] == ["current"]
    assert receipt["client_exclusions"] == 1
    assert receipt["closed_historic"] == 1


def test_a_notice_with_no_capability_term_is_dropped(store):
    _insert(store, "a1", title="Unrelated buy", desc="unrelated requirement")
    recs, _, receipt = run_l1_from_store(CAPS, conn=store)
    assert recs == []
    assert receipt["in_corridor"] == 1     # it was offered, and it failed


def test_entity_terms_widen_the_match(store):
    _insert(store, "a1", title="Acme Widget buy",
            desc="network monitoring for the Acme Widget estate")
    recs, _, _ = run_l1_from_store(CAPS, entity_terms=["Acme Widget"], conn=store)
    assert [r.record_id for r in recs] == ["a1"]
    assert "Acme Widget" in (recs[0].entity_hits or [])


def test_dedupe_collapses_the_same_notice_posted_repeatedly(store):
    for i in range(4):
        _insert(store, f"dup{i}", title="Broad Agency Announcement",
                desc="network monitoring", agency="DEPT OF X")
    recs, _, receipt = run_l1_from_store(CAPS, conn=store)
    assert len(recs) == 1
    assert receipt["deduped_away"] == 3


def test_the_cap_is_reported_never_silent(store):
    for i in range(20):
        _insert(store, f"n{i}", title=f"Network monitoring {i}",
                desc="network monitoring")
    recs, _, receipt = run_l1_from_store(CAPS, conn=store, cap=5)
    assert len(recs) == 5
    assert receipt["kept"] == 5


def test_an_empty_store_returns_nothing_and_never_raises(tmp_path, monkeypatch):
    """NO BLOCKING. An absent store initialises empty rather than failing, the
    lane returns nothing, and the caller can fall back to the sweep artifact.
    Zero records with a visible zero-row receipt is a stated finding; a crash
    would take the press with it."""
    monkeypatch.setenv("LILA_NOTICE_STORE_DIR", str(tmp_path / "gone"))
    recs, queries, receipt = run_l1_from_store(CAPS)
    assert recs == []
    assert receipt["store_rows"] == 0
    assert receipt["in_corridor"] == 0
    assert receipt["store_last_ingest"] is None   # visibly never ingested


def test_the_receipt_shows_the_whole_funnel(store):
    _insert(store, "a1")
    _, _, receipt = run_l1_from_store(CAPS, conn=store)
    for key in ("store_rows", "in_corridor", "screened", "term_hits", "kept",
                "deduped_away", "rank_4_excluded", "by_rank",
                "store_last_ingest", "metered_quota_spent"):
        assert key in receipt, f"receipt must disclose {key}"


def test_the_lane_query_names_the_store_and_zero_quota(store):
    _insert(store, "a1")
    _, queries, _ = run_l1_from_store(CAPS, conn=store)
    q = queries[0]
    assert q.method == "notice_store"
    assert "notices.db" in q.endpoint
    assert q.body["rank_4_excluded"] is True


# ---- the shared entity guards ---------------------------------------------- #
ENTITIES = {"product": ["SteelHead", "Riverbed AppResponse"],
            "competitor": ["SolarWinds"], "reseller": ["Carahsoft"]}


def test_the_guard_is_the_same_function_both_lanes_call():
    """Shared, not copied. If these ever become two implementations they will
    drift, and a guard right in one lane and stale in the other is invisible
    from either side."""
    from agents.golden_press import sam_lanes
    from agents.golden_press import l1_store
    assert l1_store.reject_entity_hit is sam_lanes.reject_entity_hit


def test_ambiguous_name_without_the_vendor_is_rejected(store):
    """"SteelHead" matched a Commerce sole-source to Innovasea because
    steelhead is a trout."""
    _insert(store, "fish", title="NOTICE OF INTENT TO SOLE-SOURCE TO INNOVASEA",
            desc="network monitoring of steelhead migration for fisheries")
    recs, _, receipt = run_l1_from_store(
        ["network monitoring"], entities=ENTITIES, vendor="Riverbed", conn=store)
    # The record may survive on its CAPABILITY hit. What must never happen is
    # "SteelHead" being credited as an entity match on an aquaculture buy.
    assert receipt["guard_rejections"]["ambiguous_name_without_vendor"] == 1
    assert all("SteelHead" not in (r.entity_hits or []) for r in recs)


def test_ambiguous_name_survives_when_the_vendor_is_named(store):
    _insert(store, "real", title="Riverbed SteelHead appliance refresh",
            desc="Riverbed SteelHead WAN optimization appliances")
    recs, _, _ = run_l1_from_store(
        ["network monitoring"], entities=ENTITIES, vendor="Riverbed", conn=store)
    assert [r.record_id for r in recs] == ["real"]
    assert "SteelHead" in (recs[0].entity_hits or [])


def test_reseller_alone_is_rejected(store):
    """Carahsoft resells for hundreds of vendors."""
    _insert(store, "sn", title="ServiceNow Order",
            desc="network monitoring order placed through Carahsoft")
    recs, _, receipt = run_l1_from_store(
        ["network monitoring"], entities=ENTITIES, vendor="Riverbed", conn=store)
    assert receipt["guard_rejections"]["reseller_without_vendor_or_product"] == 1
    assert all("Carahsoft" not in (r.entity_hits or []) for r in recs)


def test_reseller_survives_when_it_carries_the_vendor(store):
    _insert(store, "ok", title="Carahsoft Riverbed AppResponse renewal",
            desc="Carahsoft quote for Riverbed AppResponse maintenance")
    recs, _, _ = run_l1_from_store(
        ["network monitoring"], entities=ENTITIES, vendor="Riverbed", conn=store)
    assert [r.record_id for r in recs] == ["ok"]
    assert "Carahsoft" in (recs[0].entity_hits or [])


def test_a_brand_name_in_the_capability_list_is_stripped(store):
    """THE RIVERBED SURVIVOR. Intake emitted "SteelHead" as a CAPABILITY term
    as well as an entity. Capability terms are exempt from the entity guard by
    design, so the brand name walked straight past it and matched the trout."""
    _insert(store, "fish", title="SOLE-SOURCE TO INNOVASEA",
            desc="steelhead monitoring equipment")
    recs, _, _ = run_l1_from_store(
        ["network monitoring", "SteelHead"],      # brand sitting in capability
        entities=ENTITIES, vendor="Riverbed", conn=store)
    assert recs == [], "a brand name in the capability list must not bypass the guard"


def test_capability_terms_are_never_sent_through_the_entity_guard(store):
    """Procurement language has no vendor to anchor against. Guarding it would
    silently kill legitimate capability finds like the VA IATDR pair."""
    _insert(store, "iatdr", title="Infrastructure Automation Technical Debt Reduction",
            desc="infrastructure automation services")
    recs, _, _ = run_l1_from_store(
        ["infrastructure automation"], entities=ENTITIES, vendor="Red Hat", conn=store)
    assert [r.record_id for r in recs] == ["iatdr"]


def test_the_receipt_says_whether_guards_ran_at_all(store):
    _insert(store, "a1")
    _, _, unguarded = run_l1_from_store(["network monitoring"], conn=store)
    _, _, guarded = run_l1_from_store(["network monitoring"],
                                      entities=ENTITIES, vendor="Riverbed", conn=store)
    assert unguarded["guards_applied"] is False
    assert guarded["guards_applied"] is True


# ---- contacts -------------------------------------------------------------- #
@pytest.mark.parametrize("name,email,expected", [
    ("Jessica B. Rooks", "jessica.b.rooks.civ@army.mil", "named_individual"),
    ("Andrea Caltabilota", "Andrea.Caltabilota@va.gov", "named_individual"),
    # A REAL PERSON addressed by surname. An earlier heuristic scored this
    # shared purely because it is not first.last, while the notice named him.
    ("Jonathan Dittmer", "Dittmer@wapa.gov", "surname_only"),
    ("Jane Doe", "contracting@agency.gov", "shared_mailbox"),
    ("Jane Doe", "", "shared_mailbox"),
    ("", "someone@agency.gov", "shared_mailbox"),
])
def test_contact_quality_resolves_against_the_named_person(name, email, expected):
    from agents.golden_press.notice_join import contact_quality
    assert contact_quality(name, email) == expected


def test_contacts_land_on_the_record_from_the_store(store):
    with store:
        store.execute(
            "UPDATE notices SET poc_name='Jane A Smith', poc_email='jane.a.smith@va.gov',"
            " poc_phone='555-0100', poc_secondary_email='backup@va.gov' "
            "WHERE notice_id='c1'") if False else None
    _insert(store, "c1")
    with store:
        store.execute("UPDATE notices SET poc_name=?, poc_email=?, poc_phone=?,"
                      " poc_secondary_email=? WHERE notice_id='c1'",
                      ("Jane A Smith", "jane.a.smith@va.gov", "555-0100",
                       "backup@va.gov"))
    recs, _, _ = run_l1_from_store(CAPS, conn=store)
    r = recs[0]
    assert r.contact_name == "Jane A Smith"
    assert r.contact_email == "jane.a.smith@va.gov"
    assert r.contact_phone == "555-0100"
    assert r.contact_secondary_email == "backup@va.gov"
    assert r.contact_quality == "named_individual"


def test_rank_1_and_2_may_be_contacted_directly(store):
    _insert(store, "ss", ntype="Sources Sought")
    _insert(store, "sn", ntype="Special Notice")
    recs, _, _ = run_l1_from_store(CAPS, conn=store)
    assert {r.contact_use for r in recs} == {"direct"}


def test_rank_3_carries_the_contact_but_not_for_cold_calling(store):
    """Once a solicitation is open, vendor communication belongs in the
    official Q&A process. The contact is ownership information, not a lead."""
    _insert(store, "sol", ntype="Solicitation")
    with store:
        store.execute("UPDATE notices SET poc_email='co@agency.gov', "
                      "poc_name='Chris Owner' WHERE notice_id='sol'")
    recs, _, _ = run_l1_from_store(CAPS, conn=store)
    assert recs[0].notice_leverage_rank == 3
    assert recs[0].contact_use == "formal_channel_only"
    assert recs[0].contact_email == "co@agency.gov", "still carried in the data"


def test_the_press_prefers_the_store_and_falls_back_loudly(tmp_path, monkeypatch):
    """The L1 lane reported DEGRADED on every real pack because it read a
    cached sweep artifact. The store is now first; the artifact is a
    fallback and says so, so nobody reads a weeks-old cache as live."""
    from agents.golden_press import retrieval as R

    class _S:
        client_name = "Acme Networks"
        research_entities = []
        inferred_naics = []
        target_agencies = []
    monkeypatch.setenv("LILA_NOTICE_STORE_DIR", str(tmp_path / "empty"))
    pack = R.build_evidence_pack(
        _S(), sweep=None, post=lambda *a, **k: {"results": []},
        get=lambda *a, **k: {"results": []})
    l1 = {l.lane: l for l in pack.lanes}["L1_notice"]
    assert l1.status == "degraded"
    assert "no store rows" in l1.detail


def test_witnessed_negative_context_rejects_a_distinctive_collision():
    # Operator ruling 2026-08-04: "Sentra" is dictionary-distinctive, so the
    # ambiguous-name rule admits it, and the DLA gasket-probe award seated as
    # rival evidence. Corpus-witnessed phrases now reject the hit wherever
    # they co-occur with the match.
    from agents.golden_press.sam_lanes import reject_entity_hit

    matcher = {"distinctive": True, "kind": "competitor"}
    why = reject_entity_hit(
        matcher, vendor_present=False, product_hits=set(),
        text="8504681597!PROBE,GASKON SENTRA",
        negative_phrases=("probe,gaskon", "sedan"))
    assert why == "witnessed_negative_context:probe,gaskon"

    clean = reject_entity_hit(
        matcher, vendor_present=False, product_hits=set(),
        text="SENTRA DATA SECURITY POSTURE MANAGEMENT LICENSES",
        negative_phrases=("probe,gaskon", "sedan"))
    assert clean is None


def test_live_corpus_phrases_reach_the_shared_guard():
    from agents.golden_press.sam_lanes import _corpus_negatives

    phrases = _corpus_negatives()
    assert "probe,gaskon" in phrases and "sedan" in phrases
