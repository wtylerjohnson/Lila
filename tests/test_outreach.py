"""Outreach rail: research-pass growth, curation (reorder/delete/override),
enrichment round-trip, and the /api/outreach endpoints.

Offline — temp graph store via LILA_CONTACT_GRAPH_DIR, temp handoff dir via
LILA_HANDOFF_DIR (both set by conftest), Flask test client, no LLM.
"""

from __future__ import annotations

import json
import os
import sys
from datetime import date, datetime, timedelta
from pathlib import Path

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

flask = pytest.importorskip("flask")

import ui.server as srv  # noqa: E402
from tools.contact_graph.outreach import OutreachList  # noqa: E402
from tools.contact_graph.schemas import ContactObservation  # noqa: E402
from tools.contact_graph.store import ContactGraphStore  # noqa: E402

TODAY = date.today()
H = datetime(2026, 7, 5, 12, 0, 0)


def _obs(**kw):
    kw.setdefault("harvested_at", H)
    return ContactObservation(**kw)


def _seed_graph(store):
    store.append([
        # on a pursue notice, fresh channel -> top of the list
        _obs(notice_id="P1", person_name="Jane Smith", title="Contracting Officer",
             channel_kind="email", channel_value="jane@noaa.gov",
             agency="COMMERCE, DEPARTMENT OF", office_path="COMMERCE / NOAA",
             observed_at=TODAY - timedelta(days=5)),
        # on a monitor notice
        _obs(notice_id="M1", person_name="Sam Vale", title="Contract Specialist",
             channel_kind="phone", channel_value="555-0101",
             agency="VETERANS AFFAIRS, DEPARTMENT OF",
             office_path="VETERANS AFFAIRS, DEPARTMENT OF",
             observed_at=TODAY - timedelta(days=15)),
        # untriaged but fresh graded channel
        _obs(notice_id="U1", person_name="Ada Fresh", channel_kind="email",
             channel_value="ada@energy.gov", agency="ENERGY, DEPARTMENT OF",
             office_path="ENERGY, DEPARTMENT OF", observed_at=TODAY - timedelta(days=8)),
        # discard-only -> research says irrelevant
        _obs(notice_id="D1", person_name="Rex Discard", channel_kind="email",
             channel_value="rex@dhs.gov", agency="HOMELAND SECURITY, DEPARTMENT OF",
             office_path="HOMELAND SECURITY, DEPARTMENT OF",
             observed_at=TODAY - timedelta(days=3)),
        # untriaged AND stale (grade C only) -> not relevant yet
        _obs(notice_id="U2", person_name="Old Cold", channel_kind="email",
             channel_value="old@gsa.gov", agency="GENERAL SERVICES ADMINISTRATION",
             office_path="GENERAL SERVICES ADMINISTRATION",
             observed_at=TODAY - timedelta(days=500)),
    ])


def _seed_research(tmp_path):
    # write into the SAME dir the LILA_RESEARCH_DATA_DIR env isolation points
    # at (conftest), so direct grow calls and the /refresh endpoint agree
    data = tmp_path / "research_data"
    (data / "cleaned").mkdir(parents=True, exist_ok=True)
    (data / "cleaned" / "searches_test.json").write_text(json.dumps({
        "client": "Test",
        "results": {"triage": {
            "P1": {"verdict": "pursue", "reason": "fit"},
            "M1": {"verdict": "monitor", "reason": "watch"},
            "D1": {"verdict": "discard", "reason": "no fit"},
        }},
    }))
    return data


@pytest.fixture
def rail(tmp_path, monkeypatch):
    monkeypatch.setenv("LILA_CONTACT_GRAPH_DIR", str(tmp_path / "cg"))
    srv._contacts_cache.clear()
    store = ContactGraphStore()
    _seed_graph(store)
    _seed_research(tmp_path)
    ol = OutreachList(store=store)
    ol.grow_from_research(now=TODAY)  # env-resolved research dir
    return ol


@pytest.fixture
def client(rail):
    return srv.app.test_client()


def test_outreach_get_leaves_corrupt_operator_file_untouched(rail, client):
    rail.path.write_text("{", encoding="utf-8")
    before = rail.path.read_bytes()
    response = client.get("/api/outreach")
    assert response.status_code == 503
    assert response.get_json()["state"] == "unavailable"
    assert "outreach list is unavailable" in response.get_json()["error"]
    assert rail.path.read_bytes() == before
    assert list(rail.path.parent.glob("outreach.corrupt-*.json")) == []


# ---- growth from research ----------------------------------------------------
def test_grow_ranks_and_filters(rail):
    names = [e["person_name"] for e in rail.load()["entries"]]
    # pursue > monitor > untriaged-graded; discard-only and stale-untriaged excluded
    assert names == ["Jane Smith", "Sam Vale", "Ada Fresh"]
    notes = {e["person_name"]: e["source_note"] for e in rail.load()["entries"]}
    assert "pursue" in notes["Jane Smith"]
    assert "monitor" in notes["Sam Vale"]


def test_grow_is_idempotent_and_appends_only(rail, tmp_path):
    before = [e["id"] for e in rail.load()["entries"]]
    r = rail.grow_from_research(now=TODAY)
    assert r["added"] == 0
    assert [e["id"] for e in rail.load()["entries"]] == before

    # a new research pass surfaces a new person -> appended at the END
    rail.store.append([_obs(notice_id="P2", person_name="New Arrival",
                            channel_kind="email", channel_value="new@va.gov",
                            agency="VETERANS AFFAIRS, DEPARTMENT OF",
                            office_path="VETERANS AFFAIRS, DEPARTMENT OF",
                            observed_at=TODAY - timedelta(days=1))])
    r = rail.grow_from_research(now=TODAY)
    assert r["added"] == 1
    assert rail.load()["entries"][-1]["person_name"] == "New Arrival"


def test_deleted_names_never_come_back(rail, tmp_path):
    eid = rail.load()["entries"][0]["id"]  # Jane
    assert rail.remove(eid)
    r = rail.grow_from_research(now=TODAY)
    assert "Jane Smith" not in r["names"]
    assert all(e["id"] != eid for e in rail.load()["entries"])
    # manual re-add lifts the tombstone
    rail.add_manual("Jane Smith", "COMMERCE, DEPARTMENT OF")
    assert any(e["id"] == eid for e in rail.load()["entries"])


# ---- curation -----------------------------------------------------------------
def test_reorder_requires_permutation(rail):
    ids = [e["id"] for e in rail.load()["entries"]]
    assert rail.reorder(list(reversed(ids)))
    assert [e["id"] for e in rail.load()["entries"]] == list(reversed(ids))
    assert not rail.reorder(ids[:1])          # dropping entries is not a reorder
    assert not rail.reorder(ids + ["ghost"])  # inventing entries is not either


def test_override_and_clear(rail):
    eid = rail.load()["entries"][0]["id"]
    e = rail.set_override(eid, email="corrected@va.gov", phone="555-9999")
    assert e["overrides"] == {"email": "corrected@va.gov", "phone": "555-9999"}
    e = rail.set_override(eid, phone="")   # empty clears
    assert "phone" not in e["overrides"]
    rendered = {x["id"]: x for x in rail.render()}[eid]
    assert rendered["email"]["manual"] is True
    assert rendered["email"]["value"] == "corrected@va.gov"


# ---- enrichment round-trip ------------------------------------------------------
def test_enrichment_spec_and_merge(rail, tmp_path):
    out = str(tmp_path / "handoff")
    path, count = rail.export_enrichment_spec(out_dir=out)
    # Jane needs phone; Sam needs email; Ada needs phone
    assert count == 3 and Path(path).exists()
    spec = json.loads(Path(path).read_text())
    assert spec["kind"] == "apollo_enrichment"
    sam = next(p for p in spec["people"] if p["name"] == "Sam Vale")
    assert "email" in sam["need"]
    assert all(e["enrich_status"] == "requested" for e in rail.load()["entries"])

    # merge the "result file" back: exact match updates, ambiguity never guesses
    rail.add_manual("Sam Vale", "DEFENSE, DEPARTMENT OF")  # second Sam -> ambiguous by bare name
    r = rail.merge_enriched([
        {"name": "Jane Smith", "phone": "301-555-7777"},
        {"name": "Sam Vale", "email": "sam@va.gov"},                # 2 Sams, no agency
        {"name": "Sam Vale", "agency": "VETERANS AFFAIRS, DEPARTMENT OF",
         "email": "sam@va.gov"},                                     # agency disambiguates
        {"name": "Nobody Known", "email": "x@y.gov"},
    ])
    assert "Jane Smith" in r["updated"] and "Sam Vale" in r["updated"]
    assert r["ambiguous"] == ["Sam Vale"] and r["unmatched"] == ["Nobody Known"]
    jane = next(e for e in rail.load()["entries"] if e["person_name"] == "Jane Smith")
    assert jane["overrides"]["phone"] == "301-555-7777"
    assert jane["enrich_status"] == "enriched"


def test_enrichment_never_touches_the_graph(rail, tmp_path):
    """The hard boundary: enriched channels stay in the outreach layer."""
    obs_before = rail.store.observations_path.read_bytes()
    rail.set_override(rail.load()["entries"][0]["id"], email="enriched@x.com")
    rail.merge_enriched([{"name": "Ada Fresh", "phone": "555-1234"}])
    assert rail.store.observations_path.read_bytes() == obs_before


# ---- review-workflow regressions ------------------------------------------------
def test_renamed_entry_still_merges_enrichment(rail):
    """Operator corrects a published misspelling; the enrichment result comes
    back under the corrected name and must still match (round-trip survives)."""
    eid = rail.load()["entries"][0]["id"]  # Jane Smith
    rail.set_override(eid, person_name="Jane Smyth-Corrected")
    r = rail.merge_enriched([{"name": "Jane Smyth-Corrected", "phone": "301-555-0001"}])
    assert r["updated"] == ["Jane Smyth-Corrected"]
    entry = next(e for e in rail.load()["entries"] if e["id"] == eid)
    assert entry["overrides"]["phone"] == "301-555-0001"


def test_merge_tolerates_garbage_rows(rail):
    """Spreadsheet-exported JSON: numeric phones, non-dict rows — tolerated,
    never a crash/500."""
    r = rail.merge_enriched([
        {"name": "Jane Smith", "phone": 3015550001},   # numeric value
        "not a dict", 42, None,                        # garbage rows
        {"name": 12345},                               # numeric name
    ])
    assert r["updated"] == ["Jane Smith"]
    assert r["skipped"] == 3
    assert "12345" in r["unmatched"]
    jane = next(e for e in rail.load()["entries"] if e["person_name"] == "Jane Smith")
    assert jane["overrides"]["phone"] == "3015550001"


def test_corrupt_list_is_sidelined_not_wiped(rail):
    """A corrupt outreach.json must never silently destroy curation: it is
    moved aside for recovery, visibly, before a fresh doc starts."""
    rail.path.write_text("{ this is not json", encoding="utf-8")
    doc = rail.load()
    assert doc["entries"] == [] and "recovered_from" in doc
    corrupt = list(rail.store.root.glob("outreach.corrupt-*.json"))
    assert len(corrupt) == 1
    assert "not json" in corrupt[0].read_text()


def test_tombstone_survives_graph_merge_alias(rail, tmp_path):
    """HIGH-severity regression: deleting a person, then approving a graph
    merge that renames their profile, must NOT resurrect them on the next
    growth pass — tombstones follow merge aliases."""
    # 'Ada Fresh' gets a second published spelling in the graph
    rail.store.append([_obs(notice_id="U9", person_name="Ada B. Fresh",
                            channel_kind="email", channel_value="ada@energy.gov",
                            agency="ENERGY, DEPARTMENT OF",
                            office_path="ENERGY, DEPARTMENT OF",
                            observed_at=TODAY - timedelta(days=2))])
    # operator deletes Ada from the rail, then a human approves the merge
    ada_id = next(e["id"] for e in rail.load()["entries"] if e["person_name"] == "Ada Fresh")
    assert rail.remove(ada_id)
    rail.store.append_decision({"action": "approve", "agency": "ENERGY, DEPARTMENT OF",
                                "names": ["ada fresh", "ada b fresh"]})
    r = rail.grow_from_research(now=TODAY)
    assert all("Ada" not in n for n in r["names"])  # not resurrected under either name


def test_merged_profile_keeps_joining_existing_entry(rail, tmp_path):
    """The inverse case: an entry listed BEFORE a merge decision must keep
    joining its (now renamed) profile — no duplicate entry, no in_graph=False."""
    rail.store.append([_obs(notice_id="U9", person_name="Ada B. Fresh",
                            channel_kind="phone", channel_value="555-8888",
                            agency="ENERGY, DEPARTMENT OF",
                            office_path="ENERGY, DEPARTMENT OF",
                            observed_at=TODAY - timedelta(days=2))])
    rail.store.append_decision({"action": "approve", "agency": "ENERGY, DEPARTMENT OF",
                                "names": ["ada fresh", "ada b fresh"]})
    r = rail.grow_from_research(now=TODAY)
    assert all("Ada" not in n for n in r["names"])  # no duplicate under merged name
    ada = next(x for x in rail.render() if "Ada" in x["person_name"])
    assert ada["in_graph"] is True
    assert ada["phone"]["value"] == "555-8888"  # merged profile's channel joined


def test_operator_order_survives_growth(rail, tmp_path):
    """Reorder, then a new research pass: the operator's custom order is
    untouched and the new arrival appends at the end."""
    ids = [e["id"] for e in rail.load()["entries"]]
    custom = [ids[2], ids[0], ids[1]]
    assert rail.reorder(custom)
    rail.store.append([_obs(notice_id="P9", person_name="New Arrival",
                            channel_kind="email", channel_value="new@va.gov",
                            agency="VETERANS AFFAIRS, DEPARTMENT OF",
                            office_path="VETERANS AFFAIRS, DEPARTMENT OF",
                            observed_at=TODAY - timedelta(days=1))])
    rail.grow_from_research(now=TODAY)
    after = [e["id"] for e in rail.load()["entries"]]
    assert after[:3] == custom
    assert rail.load()["entries"][-1]["person_name"] == "New Arrival"


# ---- endpoints ------------------------------------------------------------------
def test_endpoints_roundtrip(client, tmp_path, monkeypatch):
    monkeypatch.setenv("LILA_HANDOFF_DIR", str(tmp_path / "handoff2"))
    entries = client.get("/api/outreach").get_json()["entries"]
    assert [e["person_name"] for e in entries] == ["Jane Smith", "Sam Vale", "Ada Fresh"]
    assert entries[0]["email"]["grade"] == "A"

    ids = [e["id"] for e in entries]
    assert client.post("/api/outreach/reorder", json={"ids": list(reversed(ids))}).get_json()["ok"]
    assert client.post("/api/outreach/reorder", json={"ids": ids[:1]}).status_code == 400

    assert client.post("/api/outreach/override",
                       json={"id": ids[0], "phone": "555-0000"}).get_json()["overrides"]["phone"] == "555-0000"
    assert client.post("/api/outreach/override", json={"id": "ghost||x"}).status_code == 404

    r = client.post("/api/outreach/enrich").get_json()
    assert r["count"] >= 1 and os.path.exists(r["path"])

    r = client.post("/api/outreach/merge",
                    json={"rows": [{"name": "Ada Fresh", "phone": "555-4321"}]}).get_json()
    assert r["updated"] == ["Ada Fresh"]

    assert client.post("/api/outreach/remove", json={"id": ids[0]}).get_json()["ok"]
    assert client.post("/api/outreach/remove", json={"id": ids[0]}).status_code == 404
    assert len(client.get("/api/outreach").get_json()["entries"]) == 2

    added = client.post("/api/outreach/add",
                        json={"person_name": "Hand Pick", "agency": "X AGENCY"}).get_json()
    assert added["added_by"] == "manual"
    assert client.post("/api/outreach/add", json={}).status_code == 400

    # refresh reads the env-isolated research dir (conftest), not the real repo
    # data; everything relevant is already listed, so this is a true no-op
    r = client.post("/api/outreach/refresh").get_json()
    assert r["added"] == 0


def test_endpoints_reject_malformed_bodies(client):
    """Valid-JSON-but-wrong-shape bodies are 400s, never 500s."""
    for path in ("/api/outreach/reorder", "/api/outreach/remove",
                 "/api/outreach/add", "/api/outreach/override"):
        for body in ([1, 2], "a string", 42):
            resp = client.post(path, json=body)
            assert resp.status_code in (400, 404), (path, body, resp.status_code)
    assert client.post("/api/outreach/reorder", json={"ids": [1, {}]}).status_code == 400
    assert client.post("/api/outreach/add", json={"person_name": 42}).status_code == 400
    assert client.post("/api/outreach/add",
                       json={"person_name": "X", "agency": 7}).status_code == 400
    ids = [e["id"] for e in client.get("/api/outreach").get_json()["entries"]]
    assert client.post("/api/outreach/override",
                       json={"id": ids[0], "email": 42}).status_code == 400
    r = client.post("/api/outreach/merge", json={"rows": ["junk", 42]}).get_json()
    assert r["skipped"] == 2  # tolerated, not a 500


def test_rail_markup_present():
    html = open(os.path.join(os.path.dirname(srv.__file__), "index.html")).read()
    assert 'id="orail"' in html                   # the rail exists
    assert 'class="orail blank"' in html          # home: blank navy column
    assert "setRailContext(true)" in html         # client view: that client's contacts
    assert html.count("setRailContext(false)") >= 3  # blank on home/contacts/review
    assert "orailCollapsed" in html               # collapse state persists
    assert "toggleRail" in html                   # open/close control wired
    assert "oAddForm" in html                     # manual prospecting form wired
    assert "hubtabs" in html                      # Clients | Contacts hub tabs
    assert "TARGETS" in html
    assert "scope.slug, 'targets', scope.workstationId" in html
    assert "o-why" in html                        # every target shows its reason
    assert "POCS ON LIVE PURSUITS" in html        # promotable reasoned POCs
    # contacts view: reorder + hide as a view overlay
    assert "ctMove" in html and "ctHide" in html
    assert "/api/contacts/prefs" in html
    assert "master database is never changed" in html


def test_contacts_view_reorder_hide_never_touch_the_graph(rail, client, tmp_path, monkeypatch):
    """Reorder + hide in the Contacts view are a per-operator overlay: the
    master database (derived from append-only observations) is never changed."""
    monkeypatch.setattr(srv, "REVIEW_DIR", str(tmp_path / "review"))  # prefs -> temp state
    (tmp_path / "review").mkdir()
    srv._contacts_cache.clear()

    before = client.get("/api/contacts?per_page=50").get_json()
    db_profiles = client.get("/api/contacts/summary").get_json()["profiles"]
    jane = next(p for p in before["profiles"] if p["person_name"] == "Jane Smith")
    sam = next(p for p in before["profiles"] if p["person_name"] == "Sam Vale")

    # HIDE Jane -> gone from the view, count drops, database unchanged
    assert client.post("/api/contacts/prefs", json={"hide": jane["key"]}).get_json()["hidden"] == 1
    after = client.get("/api/contacts?per_page=50").get_json()
    assert "Jane Smith" not in [p["person_name"] for p in after["profiles"]]
    assert after["total"] == before["total"] - 1 and after["hidden"] == 1
    assert client.get("/api/contacts/summary").get_json()["profiles"] == db_profiles

    # REORDER -> Sam pinned to the top; still no graph write
    client.post("/api/contacts/prefs", json={"order": [sam["key"]]})
    top = client.get("/api/contacts?per_page=50").get_json()["profiles"][0]
    assert top["person_name"] == "Sam Vale"
    assert client.get("/api/contacts/summary").get_json()["profiles"] == db_profiles

    # RESTORE -> Jane returns, view whole again
    client.post("/api/contacts/prefs", json={"unhide_all": True})
    restored = client.get("/api/contacts?per_page=50").get_json()
    assert "Jane Smith" in [p["person_name"] for p in restored["profiles"]]
    assert restored["hidden"] == 0


# ---- client tagging + the client-scoped contacts endpoint --------------------
def test_add_manual_client_tag_and_channels(rail):
    e = rail.add_manual("Kim Ford", "GENERAL SERVICES ADMINISTRATION",
                        title="CO", client="testco", email="kim.ford@gsa.gov")
    assert e["client"] == "testco"
    assert e["overrides"]["email"] == "kim.ford@gsa.gov"
    # a later re-add keeps the tag and never clobbers an existing override
    e2 = rail.add_manual("Kim Ford", "GENERAL SERVICES ADMINISTRATION",
                         email="other@x.gov")
    assert e2["client"] == "testco"
    assert e2["overrides"]["email"] == "kim.ford@gsa.gov"
    row = next(r for r in rail.render() if r["person_name"] == "Kim Ford")
    assert row["email"]["manual"] is True


def test_client_targets_every_name_has_a_reason(rail, client, tmp_path, monkeypatch):
    """The rail is TARGETS, not a relevance dump: POC on a live pursuit,
    watchlist sighting, or added for this client. A shared agency alone is
    NOT a reason — that person stays in the database."""
    review, cleaned = tmp_path / "review", tmp_path / "cleaned"
    review.mkdir(exist_ok=True)
    cleaned.mkdir(exist_ok=True)
    (review / "testco.review.json").write_text(json.dumps({
        "client_name": "Testco", "status": "approved", "strategy": {}}))
    (cleaned / "searches_testco.json").write_text(json.dumps({
        "client": "Testco",
        "results": {
            "sam.gov": [
                {"source_id": "P1", "title": "NOAA Video Wall",
                 "agency": "COMMERCE, DEPARTMENT OF.NOAA",
                 "naics_code": "334310", "response_deadline": "2026-08-15"},
                {"source_id": "M1", "title": "Forming AV Program",
                 "agency": "GSA", "naics_code": "334310",
                 "response_deadline": "2026-10-01"},
            ],
            "triage": {"P1": {"verdict": "pursue", "reason": "fit"},
                       "M1": {"verdict": "monitor", "reason": "forming"}},
        }}))
    monkeypatch.setattr(srv, "REVIEW_DIR", str(review))
    monkeypatch.setattr(srv, "CLEANED_DIR", str(cleaned))

    rail.add_manual("Kim Ford", "GENERAL SERVICES ADMINISTRATION", client="testco")
    rail.add_manual("Zed Other", "ENVIRONMENTAL PROTECTION AGENCY", client="otherco")
    # a POC on the pursue notice who is NOT on the outreach list yet
    store = ContactGraphStore()
    store.append([_obs(notice_id="P1", person_name="Nora Poc",
                       title="Contracting Officer", channel_kind="email",
                       channel_value="nora@noaa.gov",
                       agency="COMMERCE, DEPARTMENT OF",
                       office_path="COMMERCE / NOAA",
                       observed_at=TODAY - timedelta(days=2))])
    srv._contacts_cache.clear()

    data = client.get("/api/client/testco/targets").get_json()
    by_name = {e["person_name"]: e for e in data["targets"]}
    # Jane sits on the pursue notice P1 -> pursuit reason, ranked first
    assert "POC on pursuit #1 · NOAA Video Wall" == by_name["Jane Smith"]["reason"]
    assert data["targets"][0]["person_name"] == "Jane Smith"
    # Sam sits on the monitor notice -> watchlist sighting
    assert by_name["Sam Vale"]["reason"].startswith("sighted on watchlist item")
    # manual add for THIS client -> stated as such; other client's absent
    assert by_name["Kim Ford"]["reason"] == "added for this client"
    assert "Zed Other" not in by_name
    # the noise cut: Ada shares nothing with this client's live work
    assert "Ada Fresh" not in by_name
    # reasoned POC not yet listed -> promotable, never duplicated
    pocs = {p["person_name"]: p for p in data["pursuit_pocs"]}
    assert "Nora Poc" in pocs and "pursuit #1" in pocs["Nora Poc"]["reason"]
    assert "Jane Smith" not in pocs
