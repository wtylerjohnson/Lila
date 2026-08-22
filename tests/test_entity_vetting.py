"""Entity vetting: verdicts, failure-honesty, report badges. No network."""
from tools.api.base import REGISTRY
from tools.api.entity_vetting import EntityVettingSource
from agents.reports.target_report import render_target_report

ENTITY_OK = {"entityData": [{"entityRegistration": {
    "legalBusinessName": "Carahsoft Technology Corp.",
    "registrationStatus": "Active", "ueiSAM": "UEI123", "cageCode": "1P3F5"}}]}
NO_HITS = {"entityData": []}
EXCLUDED = {"excludedEntity": [{"exclusionType": "Ineligible", "excludingAgencyName": "GSA"}]}


def _src(monkeypatch, entity, exclusions):
    import tools.api.entity_vetting as ev
    def fake_get(url, params=None, **kw):
        return entity if "v3/entities" in url else exclusions
    monkeypatch.setattr(ev, "get_json", fake_get)
    return EntityVettingSource(api_key="k")


def test_registered():
    assert "entity_vetting" in {s.name for s in REGISTRY.all()}


def test_vetted_verdict(monkeypatch):
    v = _src(monkeypatch, ENTITY_OK, {"excludedEntity": []}).vet("Carahsoft Technology Corp.")
    assert v["verdict"] == "vetted" and v["uei"] == "UEI123" and v["excluded"] is False


def test_excluded_wins_over_registration(monkeypatch):
    v = _src(monkeypatch, ENTITY_OK, EXCLUDED).vet("BadCo")
    assert v["verdict"] == "EXCLUDED" and v["exclusion_detail"][0]["agency"] == "GSA"


def test_not_registered(monkeypatch):
    v = _src(monkeypatch, NO_HITS, {"excludedEntity": []}).vet("GhostCo")
    assert v["verdict"] == "not_registered"


def test_lookup_failure_is_honest(monkeypatch):
    import tools.api.entity_vetting as ev
    def boom(url, params=None, **kw): raise RuntimeError("rate limited")
    monkeypatch.setattr(ev, "get_json", boom)
    v = EntityVettingSource(api_key="k").vet("AnyCo")
    assert v["verdict"] == "unverified" and "rate limited" in (v.get("detail") or "")


def test_vet_many_dedupes_and_limits(monkeypatch):
    src = _src(monkeypatch, ENTITY_OK, {"excludedEntity": []})
    out = src.vet_many(["A", "a", "B"] + [f"C{i}" for i in range(20)], limit=5)
    assert out["vetted_count"] == 5 and out["skipped"] > 0


def test_report_renders_vetting_badges():
    plan = {"client_name": "Testco", "strategy_note": "angle",
            "known_pocs": [], "citations": [],
            "searches": [
                {"org_name": "GoodCo", "org_type": "prime", "person_titles": ["VP Capture"],
                 "seniorities": [], "rationale": "r"},
                {"org_name": "BadCo", "org_type": "prime", "person_titles": ["VP"],
                 "seniorities": [], "rationale": "r"}]}
    vetting = {"orgs": {
        "GoodCo": {"verdict": "vetted", "uei": "U1", "cage": "C1"},
        "BadCo": {"verdict": "EXCLUDED"}}}
    html = render_target_report(plan, vetting=vetting)
    assert "SAM VETTED" in html and "UEI U1" in html
    assert "EXCLUDED — DO NOT ENGAGE" in html
