"""Contacts view: endpoint filters/pagination, agency-mark fallback, aggregates,
merge-review wiring.

Offline — temp contact-graph store via LILA_CONTACT_GRAPH_DIR (conftest sets it
to tmp_path, so the real graph is untouchable), Flask test client, no LLM.
"""

from __future__ import annotations

import os
import sys
from datetime import date, datetime, timedelta

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

flask = pytest.importorskip("flask")

import ui.server as srv  # noqa: E402
from tools.contact_graph.schemas import ContactObservation  # noqa: E402
from tools.contact_graph.store import ContactGraphStore  # noqa: E402

TODAY = date.today()
H = datetime(2026, 7, 5, 12, 0, 0)


def _obs(**kw):
    kw.setdefault("harvested_at", H)
    return ContactObservation(**kw)


@pytest.fixture
def graph(tmp_path, monkeypatch):
    """A small graph: fresh KO at NOAA, stale specialist at an unmapped agency,
    and an ambiguous near-match pair at the VA."""
    monkeypatch.setenv("LILA_CONTACT_GRAPH_DIR", str(tmp_path / "cg"))
    srv._contacts_cache.clear()
    store = ContactGraphStore()
    store.append([
        _obs(notice_id="n1", person_name="Jane Smith", title="Contracting Officer",
             role_type="primary", channel_kind="email", channel_value="jane@noaa.gov",
             agency="COMMERCE, DEPARTMENT OF",
             office_path="COMMERCE, DEPARTMENT OF / NATIONAL OCEANIC AND ATMOSPHERIC ADMINISTRATION",
             naics="541512", observed_at=TODAY - timedelta(days=5),
             source_url="https://sam.gov/opp/n1/view"),
        _obs(notice_id="n2", person_name="Jane Smith", title="Contracting Officer",
             role_type="primary", channel_kind="email", channel_value="jane@noaa.gov",
             agency="COMMERCE, DEPARTMENT OF",
             office_path="COMMERCE, DEPARTMENT OF / NATIONAL OCEANIC AND ATMOSPHERIC ADMINISTRATION",
             naics="541512", observed_at=TODAY - timedelta(days=20)),
        _obs(notice_id="n3", person_name="Sam Vale", title="Contract Specialist",
             role_type="secondary", channel_kind="phone", channel_value="555-0101",
             agency="OBSCURE COMMISSION", office_path="OBSCURE COMMISSION",
             observed_at=TODAY - timedelta(days=400)),
        _obs(notice_id="n4", person_name="Pat Doe", channel_kind="email",
             channel_value="pat.doe@va.gov", agency="VETERANS AFFAIRS, DEPARTMENT OF",
             office_path="VETERANS AFFAIRS, DEPARTMENT OF",
             observed_at=TODAY - timedelta(days=40)),
        _obs(notice_id="n5", person_name="Pat A. Doe", channel_kind="email",
             channel_value="pad@va.gov", agency="VETERANS AFFAIRS, DEPARTMENT OF",
             office_path="VETERANS AFFAIRS, DEPARTMENT OF",
             observed_at=TODAY - timedelta(days=10)),
    ])
    return store


@pytest.fixture
def client(graph):
    return srv.app.test_client()


# ---- endpoint filters + pagination ------------------------------------------
def test_filters(client):
    assert client.get("/api/contacts").get_json()["total"] == 4

    r = client.get("/api/contacts?agency=commerce").get_json()
    assert [p["person_name"] for p in r["profiles"]] == ["Jane Smith"]

    r = client.get("/api/contacts?office=oceanic").get_json()
    assert r["total"] == 1 and r["profiles"][0]["logo_domain"] == "noaa.gov"

    r = client.get("/api/contacts?seat=contracting officer").get_json()
    assert r["total"] == 1
    r = client.get("/api/contacts?seat=secondary").get_json()  # role_type matches too
    assert r["total"] == 1 and r["profiles"][0]["person_name"] == "Sam Vale"

    assert client.get("/api/contacts?naics=541512").get_json()["total"] == 1
    assert client.get("/api/contacts?grade=A").get_json()["total"] >= 1
    assert client.get("/api/contacts?grade=C").get_json()["total"] == 1  # stale Sam
    assert client.get("/api/contacts?q=vale").get_json()["total"] == 1
    assert client.get("/api/contacts?q=nobody").get_json()["total"] == 0


def test_pagination_and_sort(client):
    r = client.get("/api/contacts?per_page=2&page=1").get_json()
    assert len(r["profiles"]) == 2 and r["total"] == 4
    r2 = client.get("/api/contacts?per_page=2&page=2").get_json()
    assert len(r2["profiles"]) == 2
    assert {p["person_name"] for p in r["profiles"]}.isdisjoint(
        {p["person_name"] for p in r2["profiles"]})

    # relevance default: Jane (2 sightings, fresh) outranks stale single-sighting Sam
    names = [p["person_name"] for p in client.get("/api/contacts").get_json()["profiles"]]
    assert names.index("Jane Smith") < names.index("Sam Vale")

    by_name = [p["person_name"] for p in
               client.get("/api/contacts?sort=name").get_json()["profiles"]]
    assert by_name == sorted(by_name, key=str.lower)

    by_last = [p["last_observed"] for p in
               client.get("/api/contacts?sort=last_observed").get_json()["profiles"]]
    assert by_last == sorted(by_last, key=lambda x: x or "", reverse=True)

    assert client.get("/api/contacts?page=zap").status_code == 400


# ---- unmapped agency falls back, never a broken image ------------------------
def test_agency_mark_mapping_and_fallback(client):
    rows = {p["person_name"]: p for p in
            client.get("/api/contacts?per_page=50").get_json()["profiles"]}
    assert rows["Jane Smith"]["logo_domain"] == "noaa.gov"   # office-level match wins
    assert rows["Sam Vale"]["logo_domain"] is None           # unmapped -> initial tile
    assert rows["Pat Doe"]["logo_domain"] == "va.gov"        # agency-level match


def test_agency_domain_helper_direct():
    assert srv.agency_domain("DEPT OF DEFENSE") == "defense.gov"
    assert srv.agency_domain("DEPT OF DEFENSE", "X / DEPT OF THE NAVY / Y") == "navy.mil"
    assert srv.agency_domain("NO SUCH AGENCY") is None
    assert srv.agency_domain(None, None) is None


# ---- aggregates ---------------------------------------------------------------
def test_summary_counts(client):
    s = client.get("/api/contacts/summary").get_json()
    assert s["profiles"] == 4
    assert s["observations"] == 5
    assert s["offices"] == 3
    assert s["observed_30d"] == 2          # Jane (5d) + Pat A. Doe (10d)
    assert s["review_pending"] == 1        # the Pat Doe / Pat A. Doe near-match


# ---- profile detail -----------------------------------------------------------
def test_profile_detail_observations(client):
    d = client.get("/api/contacts/profile?name=Jane%20Smith"
                   "&agency=commerce,%20department%20of").get_json()
    assert d["sighting_count"] == 2
    assert len(d["observations"]) == 2
    assert d["observations"][0]["source_url"]  # source-linked
    assert client.get("/api/contacts/profile?name=ghost&agency=x").status_code == 404


# ---- merge review wiring ------------------------------------------------------
def test_merge_review_approve_and_reject(client):
    cands = client.get("/api/contacts/review").get_json()
    merge = next(c for c in cands if c.get("candidates"))
    names = [c["normalized_name"] for c in merge["candidates"]]
    assert set(names) == {"pat doe", "pat a doe"}

    # bad request guard
    assert client.post("/api/contacts/review", json={"action": "approve"}).status_code == 400

    # approve -> one merged profile, queue drained
    r = client.post("/api/contacts/review", json={
        "action": "approve", "agency": merge["agency"], "names": names}).get_json()
    assert r["ok"] and r["review_pending"] == 0
    va = client.get("/api/contacts?agency=veterans").get_json()
    assert va["total"] == 1
    assert va["profiles"][0]["sighting_count"] == 2
    # merged profile's detail carries BOTH aliases' observations
    d = client.get(f"/api/contacts/profile?name={va['profiles'][0]['normalized_name']}"
                   "&agency=veterans%20affairs,%20department%20of").get_json()
    assert len(d["observations"]) == 2


def test_merge_review_reject_keeps_separate(client):
    merge = next(c for c in client.get("/api/contacts/review").get_json()
                 if c.get("candidates"))
    names = [c["normalized_name"] for c in merge["candidates"]]
    r = client.post("/api/contacts/review", json={
        "action": "reject", "agency": merge["agency"], "names": names}).get_json()
    assert r["ok"] and r["review_pending"] == 0  # decided either way leaves the queue
    assert client.get("/api/contacts?agency=veterans").get_json()["total"] == 2
