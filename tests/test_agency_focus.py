"""Agency Focus: resolver, autocomplete ranking, and the deterministic scope."""

from run_agency_report import scope_results
from tools.agencies import DEFAULT_CHIPS, find, matches_record, suggest


def test_find_resolves_abbr_name_and_fragment():
    assert find("cbp")["abbr"] == "CBP"
    assert find("CBP")["name"] == "U.S. Customs and Border Protection"
    assert find("Department of Homeland Security")["abbr"] == "DHS"
    assert find("customs and border")["abbr"] == "CBP"
    assert find("faa")["abbr"] == "FAA"
    assert find("zzz-not-an-agency") is None


def test_suggest_ranks_abbr_prefix_first_and_prefills_chips():
    # empty query returns the prefill chips (the "users enjoy prefilled forms" set)
    empty = suggest("")
    assert [a["abbr"] for a in empty] == DEFAULT_CHIPS[:len(empty)] or \
        {a["abbr"] for a in empty} == set(DEFAULT_CHIPS)
    # "d" ranks abbr-prefix hits (DHS, DoD...) ahead of contains-matches
    d = [a["abbr"] for a in suggest("d")]
    assert d[0] in ("DHS", "DoD", "DOT", "DOS", "DOJ", "DOI", "DOC", "DOE",
                    "DOL", "DISA", "DLA", "DARPA")
    # a component query completes
    assert suggest("transportation sec")[0]["abbr"] == "TSA"


def test_matches_record_department_captures_components():
    dhs, cbp = find("DHS"), find("CBP")
    sam_path = "HOMELAND SECURITY, DEPARTMENT OF.US CUSTOMS AND BORDER PROTECTION"
    assert matches_record(sam_path, dhs)          # department pass
    assert matches_record(sam_path, cbp)          # component pass
    assert matches_record("US CUSTOMS AND BORDER PROTECTION", dhs)  # child-only string
    assert not matches_record("GENERAL SERVICES ADMINISTRATION", dhs)
    assert not matches_record("", dhs)


def _results():
    return {
        "sam.gov": [
            {"source_id": "n1", "title": "Charts",
             "agency": "HOMELAND SECURITY, DEPARTMENT OF / US CUSTOMS AND BORDER PROTECTION"},
            {"source_id": "n2", "title": "KVM",
             "agency": "GENERAL SERVICES ADMINISTRATION / FAS"},
        ],
        "triage": {"n1": {"verdict": "monitor", "reason": "r"},
                   "n2": {"verdict": "discard", "reason": "r"}},
        "expiring_awards": {"rows": [
            {"award_id": "A1", "awarding_agency": "Department of Homeland Security",
             "awarding_sub_agency": "U.S. Customs and Border Protection",
             "end_date": "2026-09-01"},
            {"award_id": "A2", "awarding_agency": "General Services Administration",
             "awarding_sub_agency": "FAS", "end_date": "2026-10-01"},
        ], "basis": "b"},
        "forecast_signals": {"total_records": 745, "matched": [
            {"record": {"title": "x", "component": "CBP/OIT", "agency": "DHS"},
             "reasons": ["keywords: aviation"]},
            {"record": {"title": "y", "component": "TSA/OIT", "agency": "DHS"},
             "reasons": ["exact NAICS"]},
        ]},
        "news": {"items": [
            {"title": "CBP expands aviation data buys", "summary": ""},
            {"title": "GSA schedule news", "summary": ""},
        ]},
        "subawards": {"primes": [{"name": "GDIT", "total": 1.0}]},
        "research_picture": {"headline": "whole market"},
        "client_awards": {"rows": [{"award_id": "K1", "url": "https://u"}]},
    }


def test_scope_results_filters_agency_keyed_layers_and_drops_market_wide():
    cbp = find("CBP")
    scoped, stats = scope_results(_results(), cbp)
    assert [n["source_id"] for n in scoped["sam.gov"]] == ["n1"]
    assert list(scoped["triage"]) == ["n1"]              # verdicts travel with notices
    assert [r["award_id"] for r in scoped["expiring_awards"]["rows"]] == ["A1"]
    assert "scoped to CBP" in scoped["expiring_awards"]["basis"]
    assert [m["record"]["component"] for m in scoped["forecast_signals"]["matched"]] == ["CBP/OIT"]
    assert [i["title"] for i in scoped["news"]["items"]] == ["CBP expands aviation data buys"]
    # whole-market layers never masquerade as agency-scoped
    for gone in ("subawards", "research_picture"):
        assert gone not in scoped
    # internal-only layers pass through untouched
    assert scoped["client_awards"]["rows"][0]["award_id"] == "K1"
    assert stats["notices"] == "1/2"


def test_scope_results_department_pass_includes_components():
    dhs = find("DHS")
    scoped, _ = scope_results(_results(), dhs)
    assert [n["source_id"] for n in scoped["sam.gov"]] == ["n1"]
    # TSA line survives a DHS department pass
    comps = [m["record"]["component"] for m in scoped["forecast_signals"]["matched"]]
    assert comps == ["CBP/OIT", "TSA/OIT"]


# ── search scope on the review packet (client-flow C) ──

def test_set_scope_validates_and_persists(tmp_path, monkeypatch):
    import agents.review as review_mod
    from agents.decisions.schemas import IntakeStrategy
    from agents.review import (
        ReviewPacket, RevisionError, load_packet, set_scope,
    )
    monkeypatch.setattr(review_mod, "REVIEW_DIR", str(tmp_path))
    monkeypatch.setattr(review_mod, "_path",
                        lambda c: str(tmp_path / f"{c.lower().replace(' ', '_')}.review.json"))
    s = IntakeStrategy(client_name="Acme", pursuit_strategy="p",
                       confidence=0.9, review_gate="g")
    p = ReviewPacket(client_name="Acme", strategy=s)
    open(review_mod._path("Acme"), "w").write(p.model_dump_json())

    # unknown agency -> loud rejection, nothing persisted silently
    try:
        set_scope("Acme", {"agencies": ["CBP", "Ministry of Silly Walks"]})
        raise AssertionError("should have raised")
    except RevisionError as e:
        assert "Ministry of Silly Walks" in str(e)

    # valid scope persists; resolver returns full agency records
    set_scope("Acme", {"agencies": ["CBP", "faa"]})
    loaded = load_packet("Acme")
    assert loaded.search_scope == {"agencies": ["CBP", "faa"]}
    assert [a["abbr"] for a in loaded.scope_agencies()] == ["CBP", "FAA"]

    # empty agency list collapses to all; None stays None (both mean all)
    set_scope("Acme", {"agencies": []})
    assert load_packet("Acme").search_scope == {"all": True}
    assert load_packet("Acme").scope_agencies() == []
