"""Contact graph (phase one) — recorded-fixture tests.

Covers the spec's required scenarios: multi-POC extraction, the append-only
invariant, profile derivation + grade decay over mocked time, conservative merge
and the ambiguous-merge review path, backfill idempotency, and query ranking.
Everything runs against tmp_path — no test touches the real on-disk store.
"""

import json
from datetime import date, datetime

from tools.contact_graph import (
    UNATTRIBUTED,
    ContactGraphStore,
    ContactObservation,
    derive_profiles,
    format_profile,
    grade_for,
    observations_from_notice,
    query,
)
from tools.contact_graph.backfill import backfill
from tools.contact_graph.harvest import harvest_from_results

# A recorded SAM notice with THREE POCs: a primary (email + phone), a secondary
# contract specialist (email only), and an all-null stub that must be dropped.
NOTICE = {
    "noticeId": "NCT-001",
    "title": "Threat Intelligence Platform",
    "type": "Solicitation",
    "fullParentPathName": (
        "COMMERCE, DEPARTMENT OF.NATIONAL OCEANIC AND ATMOSPHERIC ADMINISTRATION.NOAA ACQUISITION"
    ),
    "postedDate": "2026-06-15",
    "naicsCode": "541512",
    "uiLink": "https://sam.gov/opp/NCT-001/view",
    "pointOfContact": [
        {"fax": None, "type": "primary", "email": "jane.doe@noaa.gov",
         "phone": "301-555-0001", "title": "Contracting Officer", "fullName": "Jane Doe"},
        {"fax": None, "type": "secondary", "email": "sam.vale@noaa.gov",
         "phone": None, "title": "Contract Specialist", "fullName": "Sam Vale"},
        {"fax": None, "type": "secondary", "email": None,
         "phone": None, "title": None, "fullName": None},
    ],
}

H = datetime(2026, 7, 5, 12, 0, 0)


def _obs(**kw):
    kw.setdefault("harvested_at", H)
    return ContactObservation(**kw)


# --- 1. multi-POC extraction from one notice ---------------------------------
def test_multi_poc_extraction_from_one_notice():
    obs = observations_from_notice(NOTICE, harvested_at=H)

    # every POC captured, not just the first; null stub dropped
    people = {o.person_name for o in obs}
    assert people == {"Jane Doe", "Sam Vale"}

    # Jane has two atomic observations (one per channel); Sam has one (email only)
    jane = [o for o in obs if o.person_name == "Jane Doe"]
    sam = [o for o in obs if o.person_name == "Sam Vale"]
    assert {o.channel_kind for o in jane} == {"email", "phone"}
    assert {(o.channel_kind, o.channel_value) for o in sam} == {("email", "sam.vale@noaa.gov")}

    # notice context is captured, incl. role_type and the CO/CS title
    j_email = next(o for o in jane if o.channel_kind == "email")
    assert j_email.role_type == "primary"
    assert j_email.title == "Contracting Officer"
    assert j_email.agency == "COMMERCE, DEPARTMENT OF"  # top of the dotted path
    assert j_email.naics == "541512"
    assert j_email.notice_type == "Solicitation"
    assert j_email.source_url == "https://sam.gov/opp/NCT-001/view"
    assert j_email.observed_at == date(2026, 6, 15)  # posted date, not harvest date

    s = next(o for o in sam)
    assert s.role_type == "secondary" and s.title == "Contract Specialist"


# --- 2. append-only invariant ------------------------------------------------
def test_observation_append_only_invariant(tmp_path):
    store = ContactGraphStore(root=tmp_path / "cg")
    obs = observations_from_notice(NOTICE, harvested_at=H)

    r1 = store.append(obs)
    assert r1.written == 3 and r1.skipped == 0
    first_bytes = store.observations_path.read_bytes()

    # re-appending the same observations mutates nothing and deletes nothing
    r2 = store.append(obs)
    assert r2.written == 0 and r2.skipped == 3
    assert store.observations_path.read_bytes() == first_bytes

    # a new observation is appended; prior bytes are preserved exactly (no rewrite)
    extra = _obs(notice_id="NCT-002", person_name="Jane Doe", channel_kind="email",
                 channel_value="jane.doe@noaa.gov", agency="COMMERCE, DEPARTMENT OF",
                 observed_at=date(2026, 6, 20))
    store.append([extra])
    after = store.observations_path.read_bytes()
    assert after.startswith(first_bytes)  # original lines untouched
    assert len(store.read_observations()) == 4


# --- 3. profile derivation + grade decay over mocked time --------------------
def test_grade_decay_over_mocked_time():
    observed = date(2026, 1, 1)
    assert grade_for(observed, now=date(2026, 1, 31)) == "A"   # 30 days
    assert grade_for(observed, now=date(2026, 6, 1)) == "B"    # ~151 days
    assert grade_for(observed, now=date(2027, 6, 1)) == "C"    # >1 year
    assert grade_for(None, now=date(2026, 1, 1)) == "C"        # no date -> C

    # the derived profile's channel grade reflects `now`, nothing is stored as truth
    o = _obs(notice_id="N", person_name="Jane Doe", channel_kind="email",
             channel_value="jane@noaa.gov", agency="COMMERCE, DEPARTMENT OF",
             observed_at=observed)
    fresh, _ = derive_profiles([o], now=date(2026, 2, 1))
    later, _ = derive_profiles([o], now=date(2027, 6, 1))
    assert fresh[0].channels[0].grade == "A"
    assert later[0].channels[0].grade == "C"
    # same underlying observation, grade decayed purely by the calendar
    assert fresh[0].channels[0].last_observed == later[0].channels[0].last_observed == observed


def test_profile_derivation_aggregates():
    obs = observations_from_notice(NOTICE, harvested_at=H)
    obs += observations_from_notice({**NOTICE, "noticeId": "NCT-009"}, harvested_at=H)
    profiles, _ = derive_profiles(obs, now=date(2026, 7, 5))
    jane = next(p for p in profiles if p.person_name == "Jane Doe")
    assert jane.sighting_count == 2                      # two distinct notices
    assert "541512" in jane.naics
    assert jane.first_observed == jane.last_observed == date(2026, 6, 15)
    assert {c.kind for c in jane.channels} == {"email", "phone"}


# --- 4. conservative merge + cross-agency guard + ambiguous review -----------
def test_conservative_merge_and_cross_agency_guard():
    obs = [
        _obs(notice_id="a", person_name="Jane Doe", channel_kind="email",
             channel_value="jane@va.gov", agency="VETERANS AFFAIRS, DEPARTMENT OF",
             observed_at=date(2026, 6, 1)),
        _obs(notice_id="b", person_name="Jane Doe", channel_kind="phone",
             channel_value="301-555-0100", agency="VETERANS AFFAIRS, DEPARTMENT OF",
             observed_at=date(2026, 6, 2)),
        _obs(notice_id="c", person_name="Jane Doe", channel_kind="email",
             channel_value="jane@dod.gov", agency="DEFENSE, DEPARTMENT OF",
             observed_at=date(2026, 6, 3)),
    ]
    profiles, review = derive_profiles(obs, now=date(2026, 7, 5))
    # same name in the SAME agency merges; the DoD Jane stays separate
    assert len(profiles) == 2
    va = next(p for p in profiles if "VETERAN" in p.agency)
    assert va.sighting_count == 2 and len(va.channels) == 2
    assert review == []


def test_ambiguous_merge_goes_to_review_not_guessed():
    obs = [
        _obs(notice_id="a", person_name="Jane Doe", channel_kind="email",
             channel_value="jane@va.gov", agency="VETERANS AFFAIRS, DEPARTMENT OF",
             observed_at=date(2026, 6, 1)),
        _obs(notice_id="b", person_name="Jane A. Doe", channel_kind="email",
             channel_value="jad@va.gov", agency="VETERANS AFFAIRS, DEPARTMENT OF",
             observed_at=date(2026, 6, 2)),
    ]
    profiles, review = derive_profiles(obs, now=date(2026, 7, 5))
    # NOT auto-merged (conservative) — two profiles remain
    assert len([p for p in profiles if "VETERAN" in p.agency]) == 2
    # ...and the near-match is flagged for a human
    assert len(review) == 1
    names = {c["person_name"] for c in review[0]["candidates"]}
    assert names == {"Jane Doe", "Jane A. Doe"}


# --- 5. backfill idempotency -------------------------------------------------
def _workspace(tmp_path):
    data = tmp_path / "data"
    for sub in ("cleaned", "cache/sam", "cache/sam_notice"):
        (data / sub).mkdir(parents=True)
    record = {"source": "sam.gov", "source_id": NOTICE["noticeId"], "raw_payload": NOTICE}
    (data / "cleaned" / "searches_test.json").write_text(
        json.dumps({"client": "Test", "results": {"sam.gov": [record]}})
    )
    return data


def test_backfill_idempotency(tmp_path):
    data = _workspace(tmp_path)
    store = ContactGraphStore(root=tmp_path / "cg")

    r1 = backfill(store=store, workspace=data, harvested_at=H, log=lambda *_: None)
    assert r1.total_written == 3          # Jane email+phone, Sam email
    assert r1.total_skipped == 0

    # a fresh store instance re-reads the same file: nothing new is written
    store2 = ContactGraphStore(root=tmp_path / "cg")
    r2 = backfill(store=store2, workspace=data, harvested_at=H, log=lambda *_: None)
    assert r2.total_written == 0
    assert r2.total_skipped == 3
    assert len(store2.read_observations()) == 3   # no duplication


def test_backfill_logs_coverage_gap_not_error(tmp_path):
    data = _workspace(tmp_path)
    # a notice with no POC at all -> coverage gap, not a failure
    gap = {"source": "sam.gov", "source_id": "NO-POC",
           "raw_payload": {"noticeId": "NO-POC", "pointOfContact": []}}
    doc = json.loads((data / "cleaned" / "searches_test.json").read_text())
    doc["results"]["sam.gov"].append(gap)
    (data / "cleaned" / "searches_test.json").write_text(json.dumps(doc))

    store = ContactGraphStore(root=tmp_path / "cg")
    report = backfill(store=store, workspace=data, harvested_at=H, log=lambda *_: None)
    assert report.total_gaps >= 1
    assert "NO-POC" in report.coverage_gap_ids
    assert report.total_written == 3  # the good notice still harvested


# --- 6. query ranking --------------------------------------------------------
def test_query_ranking_and_format(tmp_path):
    store = ContactGraphStore(root=tmp_path / "cg")
    obs = []
    # Jane: many sightings, fresh email (grade A)
    for i, d in enumerate([date(2026, 6, 12), date(2026, 5, 1), date(2026, 4, 1)]):
        obs.append(_obs(notice_id=f"j{i}", person_name="Jane Smith", title="Contracting Officer",
                        channel_kind="email", channel_value="jane@noaa.gov",
                        agency="COMMERCE, DEPARTMENT OF", office_path="COMMERCE.NOAA.ACQ",
                        naics="541512", observed_at=d))
    # Sam: single, stale sighting (grade C)
    obs.append(_obs(notice_id="s0", person_name="Sam Vale", title="Contract Specialist",
                    channel_kind="email", channel_value="sam@noaa.gov",
                    agency="COMMERCE, DEPARTMENT OF", office_path="COMMERCE.NOAA.ACQ",
                    naics="541512", observed_at=date(2024, 1, 1)))
    store.append(obs)

    ranked = query(store, agency="COMMERCE", naics="541512", now=date(2026, 7, 5))
    assert [p.person_name for p in ranked] == ["Jane Smith", "Sam Vale"]  # A before C

    top = format_profile(ranked[0])
    assert "Jane Smith" in top and "NAICS 541512" in top and "email grade A" in top

    # filters actually narrow
    assert query(store, naics="000000", now=date(2026, 7, 5)) == []
    assert {p.person_name for p in query(store, keywords="occultation", now=date(2026, 7, 5))} == set()


# --- 7. office rotation hint -------------------------------------------------
def test_rotation_hint_flags_succession():
    office = "COMMERCE.NOAA.ACQ"
    obs = [
        _obs(notice_id="a1", person_name="Alice Old", channel_kind="email",
             channel_value="alice@noaa.gov", agency="COMMERCE, DEPARTMENT OF",
             office_path=office, observed_at=date(2024, 3, 1)),
        _obs(notice_id="b1", person_name="Bob New", channel_kind="email",
             channel_value="bob@noaa.gov", agency="COMMERCE, DEPARTMENT OF",
             office_path=office, observed_at=date(2026, 6, 1)),
    ]
    profiles, _ = derive_profiles(obs, now=date(2026, 7, 5))
    alice = next(p for p in profiles if p.person_name == "Alice Old")
    bob = next(p for p in profiles if p.person_name == "Bob New")
    assert alice.rotation is not None
    assert alice.rotation.likely_successor == "Bob New"
    assert bob.rotation is None  # the current occupant isn't flagged


# --- 8. toggle gates the inline side effect ----------------------------------
def test_toggle_off_writes_nothing(tmp_path, monkeypatch):
    results = {"sam.gov": [{"source": "sam.gov", "source_id": NOTICE["noticeId"],
                            "raw_payload": NOTICE}]}
    monkeypatch.setenv("LILA_ENABLE_CONTACT_GRAPH", "off")
    store = ContactGraphStore(root=tmp_path / "cg")
    summary = harvest_from_results(results, store=store, harvested_at=H)
    assert summary.enabled is False
    assert not store.observations_path.exists()

    monkeypatch.setenv("LILA_ENABLE_CONTACT_GRAPH", "on")
    summary = harvest_from_results(results, store=store, harvested_at=H)
    assert summary.enabled is True and summary.observations_written == 3


# --- 9. description-text channel extraction (addendum) -----------------------
def test_description_text_extraction():
    notice = {
        "noticeId": "DT-1",
        "fullParentPathName": "STATE, DEPARTMENT OF.CST",
        "postedDate": "2026-06-01",
        "description": (
            "Direct questions to Maria Lopez, maria.lopez@state.gov. "
            "Offers go to acquisitions@state.gov or fax. "
            "Call (202) 555-0170 with access issues. "
            "Ref solicitation 19AQMM26R0044, ceiling 1234567890."
        ),
    }
    obs = observations_from_notice(notice, harvested_at=H)
    by_value = {o.channel_value: o for o in obs}

    # adjacent name is attributed; channel source marks the lower-trust surface
    lopez = by_value["maria.lopez@state.gov"]
    assert lopez.person_name == "Maria Lopez"
    assert lopez.channel_source == "description_text"

    # no adjacent name -> UNATTRIBUTED, never guessed
    assert by_value["acquisitions@state.gov"].person_name == UNATTRIBUTED
    assert by_value["(202) 555-0170"].person_name == UNATTRIBUTED
    assert by_value["(202) 555-0170"].channel_kind == "phone"

    # conservative: solicitation numbers and bare digit runs are NOT phones
    assert all("19AQMM" not in (o.channel_value or "") for o in obs)
    assert all("1234567890" != (o.channel_value or "") for o in obs)

    # structured POC fields still outrank: same channel in both surfaces is
    # recorded once, from the POC field
    both = {
        "noticeId": "DT-2",
        "pointOfContact": [{"fullName": "Maria Lopez", "type": "primary",
                            "email": "maria.lopez@state.gov", "phone": None,
                            "title": None, "fax": None}],
        "description": "Contact Maria Lopez, maria.lopez@state.gov.",
    }
    obs2 = observations_from_notice(both, harvested_at=H)
    assert [(o.channel_value, o.channel_source) for o in obs2] == [
        ("maria.lopez@state.gov", "poc_field")
    ]


def test_unattributed_channel_review_path(tmp_path):
    notice = {
        "noticeId": "DT-3",
        "fullParentPathName": "STATE, DEPARTMENT OF.CST",
        "postedDate": "2026-06-01",
        "description": "Responses via email to KayaniBA@state.gov by 11:00 EST.",
    }
    obs = observations_from_notice(notice, harvested_at=H)
    assert obs[0].person_name == UNATTRIBUTED

    profiles, review = derive_profiles(obs, now=date(2026, 7, 5))
    # never merged into a profile...
    assert profiles == []
    # ...flagged for a human instead, with the notice context attached
    assert len(review) == 1
    entry = review[0]
    assert "no clearly adjacent name" in entry["reason"]
    assert entry["notice_id"] == "DT-3"
    assert entry["channel"] == {"kind": "email", "value": "KayaniBA@state.gov"}


# --- 10. both link forms on every observation (addendum) ----------------------
def test_both_link_forms_on_every_observation():
    # cached data carries both forms: use them, nothing derived
    cached = dict(NOTICE)
    cached["description"] = "https://api.sam.gov/opportunities/v2/noticedesc?noticeid=NCT-001"
    obs = observations_from_notice(cached, harvested_at=H)
    assert obs, "fixture must yield observations"
    for o in obs:
        assert o.source_url == "https://sam.gov/opp/NCT-001/view"
        assert o.api_ref == "https://api.sam.gov/opportunities/v2/noticedesc?noticeid=NCT-001"
        assert o.source_url_derived is False
        assert o.api_ref_derived is False

    # cached data carries neither: both derived from the notice ID and marked
    bare = {"noticeId": "BARE-1",
            "pointOfContact": [{"fullName": "Jane Doe", "type": "primary",
                                "email": "jane@x.gov", "phone": None,
                                "title": None, "fax": None}]}
    for o in observations_from_notice(bare, harvested_at=H):
        assert o.source_url == "https://sam.gov/opp/BARE-1/view" and o.source_url_derived
        assert "noticeid=BARE-1" in o.api_ref and o.api_ref_derived


# --- 11. lint: rendered contacts must carry grade + source --------------------
def test_lint_contact_requires_grade_and_source():
    from agents.reports.lint import lint_contact_rendering

    ok = ('<span data-contact="1" data-grade="A" '
          'data-source="https://sam.gov/opp/x">Jane Doe, jane.doe@va.gov</span>')
    assert lint_contact_rendering(ok).ok

    missing = '<span data-contact="1" data-source="https://sam.gov/opp/x">jane.doe@va.gov</span>'
    assert not lint_contact_rendering(missing).ok

    bare = "<p>Reach the CO at robert.cowins@nist.gov</p>"
    assert not lint_contact_rendering(bare).ok

    # a non-federal sender address in a footer is not a graph contact
    assert lint_contact_rendering("<footer>email keith@gtmgroup.com</footer>").ok


# ── POC-field hygiene (2026-07-12): SAM POC blocks bleed phones into name
# fields and publish switchboard placeholders; derivation cleans what it
# reads, the append-only store is never rewritten. Codex UI-assessment
# finding, verified against 66 contaminated + 22 placeholder rows live.

def test_split_name_and_phone_cases():
    from tools.contact_graph.names import split_name_and_phone
    assert split_name_and_phone("COURTNEY BOWERSOCK6146939970") == (
        "COURTNEY BOWERSOCK", "6146939970")
    assert split_name_and_phone("Danielle Udinson 2156979584") == (
        "Danielle Udinson", "2156979584")
    name, phone = split_name_and_phone("Telephone: 2157372522")
    assert name == "" and phone == "2157372522"
    assert split_name_and_phone("Sharon A Benjamin") == (
        "Sharon A Benjamin", None)
    assert split_name_and_phone("Ligon, Rhonda") == ("Ligon, Rhonda", None)
    name, phone = split_name_and_phone("Jane Doe (301) 975-8335")
    assert name == "Jane Doe" and phone == "3019758335"
    assert split_name_and_phone(None) == ("", None)
    assert split_name_and_phone("   ") == ("", None)


def test_plausible_phone_rejects_placeholders():
    from tools.contact_graph.names import plausible_phone
    assert plausible_phone("3034453383") is True
    assert plausible_phone("848-377-5108") is True
    assert plausible_phone("0000000000") is False
    assert plausible_phone("1111111111") is False
    assert plausible_phone("12345") is False
    assert plausible_phone(None) is False
    assert plausible_phone("ext. 4") is False


def test_derivation_cleans_contaminated_names_and_salvages_the_phone():
    from datetime import date
    obs = [
        _obs(notice_id="N1", person_name="COURTNEY BOWERSOCK6146939970",
             channel_kind="email", channel_value="c.bowersock@gsa.gov",
             agency="GSA", observed_at=date(2026, 7, 1)),
        _obs(notice_id="N2", person_name="Telephone: 2157372522",
             channel_kind=None, channel_value=None,
             agency="GSA", observed_at=date(2026, 7, 1)),
    ]
    profiles, _review = derive_profiles(obs, now=date(2026, 7, 12))
    names = [p.person_name for p in profiles]
    assert "COURTNEY BOWERSOCK" in names
    assert all("6146939970" not in n for n in names)
    assert all(not n.lower().startswith("telephone") for n in names)
    courtney = next(p for p in profiles if p.person_name == "COURTNEY BOWERSOCK")
    kinds = {(c.kind, c.value) for c in courtney.channels}
    assert ("email", "c.bowersock@gsa.gov") in kinds
    assert ("phone", "6146939970") in kinds  # salvaged with real provenance
    salvaged = next(c for c in courtney.channels if c.kind == "phone")
    assert salvaged.notice_id == "N1"


def test_placeholder_phone_channels_never_grade():
    from datetime import date
    obs = [
        _obs(notice_id="N1", person_name="Janice Moye",
             channel_kind="phone", channel_value="0000000000",
             agency="DHS", observed_at=date(2026, 7, 1)),
        _obs(notice_id="N1", person_name="Janice Moye",
             channel_kind="email", channel_value="janice.moye@hq.dhs.gov",
             agency="DHS", observed_at=date(2026, 7, 1)),
    ]
    profiles, _review = derive_profiles(obs, now=date(2026, 7, 12))
    janice = next(p for p in profiles if p.person_name == "Janice Moye")
    assert all(c.kind != "phone" for c in janice.channels)
    assert any(c.kind == "email" for c in janice.channels)
