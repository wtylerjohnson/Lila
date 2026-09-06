"""Step 1 company mastery: identity, dossier, yield, adversarial, name-only.

Offline. No keys, no network, no search fan-out. Doctrine: a company name
is not an identity; an empty notice store is not a market zero; incomplete
adversarial review is not a pass; approval (even auto-passthrough) does
not launch searches or invent scope.preset.
"""

from __future__ import annotations

import json
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from agents.intake.adapters import retrieval_frame, strategy_from_dossier
from agents.intake.adversarial import challenge_dossier, run_adversarial
from agents.intake.dossier import Claim, ClaimState, CompanyDossier, build_dossier
from agents.intake.identity import (
    IdentityCandidate,
    IdentityRoster,
    bind_identity,
    registrable_domain,
    resolve_identity,
)
from agents.intake.pipeline import (
    run_step1,
    submission_from_client_name,
)
from agents.intake.readiness import evaluate_readiness
from agents.intake.yield_sidecar import intake_yield_receipt, notice_store_census
from agents.schemas import IntakeSubmission


# ---- identity gate --------------------------------------------------------- #

def test_form_website_binds_when_no_rival_candidate():
    out = bind_identity(
        "Acme Federal Solutions LLC",
        [],
        form_website="https://acmefederal.com",
    )
    assert out.is_bound
    assert out.official_domain == "acmefederal.com"
    assert out.website_source == "form"
    assert out.question is None


def test_two_official_domains_abstain_with_one_question():
    out = bind_identity(
        "Pioneer",
        [
            IdentityCandidate(
                name="Pioneer Systems", official_domain="pioneersys.com",
                website="https://pioneersys.com", confidence=0.9,
                rationale="IT integrator"),
            IdentityCandidate(
                name="Pioneer Seed", official_domain="pioneer.com",
                website="https://pioneer.com", confidence=0.88,
                rationale="agriculture"),
        ],
    )
    assert out.status == "abstain"
    assert not out.is_bound
    assert out.question and "Which company" in out.question
    assert "pioneersys.com" in out.question
    assert "pioneer.com" in out.question


def test_low_confidence_guess_does_not_bind():
    out = bind_identity(
        "Ghost LLC",
        [IdentityCandidate(
            name="Ghost", official_domain="ghost.directory",
            website="https://ghost.directory", confidence=0.3,
            rationale="directory hit")],
    )
    assert out.status == "abstain"
    assert out.official_domain is None
    assert "bind floor" in out.rationale


def test_same_door_variants_collapse_to_one_bind():
    out = bind_identity(
        "Acme Inc",
        [
            IdentityCandidate(
                name="Acme Inc.", official_domain="acme.com",
                website="https://acme.com", confidence=0.8, rationale="a"),
            IdentityCandidate(
                name="Acme Incorporated", official_domain="acme.com",
                website="https://www.acme.com", confidence=0.95, rationale="b"),
        ],
    )
    assert out.is_bound
    assert out.official_domain == "acme.com"
    assert out.confidence == 0.95


def test_name_only_without_engine_abstains():
    out = resolve_identity("Ambiguous Partners")
    assert out.status == "abstain"
    assert "official website" in (out.question or "").casefold()


def test_name_only_intake_constructs_research_engine_and_binds(
        tmp_path, monkeypatch):
    """Forgotten engines must not force the offline-abstain path.

    The Arista press (NO-GO 3/16) finished in ~410ms with candidates=[]
    because run_step1 left identity_engine=None. find_website('Arista
    Networks') binds https://www.arista.com at 0.95 when an engine is
    actually used. True two-domain ambiguity still abstains elsewhere.
    """
    import agents.review as review

    monkeypatch.setattr(review, "REVIEW_DIR", str(tmp_path / "review"))
    monkeypatch.setenv("LILA_CLIENTS_DIR", str(tmp_path / "clients"))
    monkeypatch.setenv("LILA_NOTICE_STORE_DIR", str(tmp_path / "empty_store"))
    monkeypatch.setenv("LILA_ENABLE_INTAKE_AUTO_APPROVE", "off")

    factory_calls = {"research": 0, "strategy": 0}

    class AristaResearchEngine:
        def web_research(self, **_kwargs):
            return (
                "Arista Networks official corporate homepage is "
                "https://www.arista.com",
                ["https://www.arista.com"],
            )

        def structure(self, **_kwargs):
            return IdentityRoster(candidates=[
                IdentityCandidate(
                    name="Arista Networks",
                    official_domain="arista.com",
                    website="https://www.arista.com",
                    confidence=0.95,
                    rationale="official corporate homepage",
                ),
            ])

    class QuietStrategyEngine:
        def deliberate(self, *a, **k):
            raise RuntimeError("strategy LLM unused in this wiring test")

    def fake_research_engine():
        factory_calls["research"] += 1
        return AristaResearchEngine()

    def fake_decision_engine(*_a, **_k):
        factory_calls["strategy"] += 1
        return QuietStrategyEngine()

    monkeypatch.setattr(
        "agents.decisions.engine.research_engine", fake_research_engine)
    monkeypatch.setattr(
        "agents.decisions.engine.DecisionEngine", fake_decision_engine)

    result = run_step1(
        submission_from_client_name("Arista Networks"),
        do_scrape=False,
        do_web=True,
        auto_approve=False,
        review_dir=str(tmp_path / "review"),
        alert_fn=lambda *a: None,
    )
    assert factory_calls["research"] >= 1
    assert result.identity.is_bound
    assert result.identity.official_domain == "arista.com"
    assert result.identity.website == "https://www.arista.com"
    assert result.identity.status == "bound"
    assert "name alone" not in result.identity.rationale
    assert factory_calls["strategy"] >= 1


def test_name_only_without_web_still_abstains_offline(tmp_path, monkeypatch):
    """--no-websearch still takes the honest offline path for a bare name."""
    import agents.review as review

    monkeypatch.setattr(review, "REVIEW_DIR", str(tmp_path / "review"))
    monkeypatch.setenv("LILA_CLIENTS_DIR", str(tmp_path / "clients"))
    monkeypatch.setenv("LILA_NOTICE_STORE_DIR", str(tmp_path / "empty_store"))

    constructed = []

    def boom():
        constructed.append("research")
        raise AssertionError("offline path must not construct a research engine")

    monkeypatch.setattr(
        "agents.intake.pipeline._default_research_engine", boom)

    result = run_step1(
        submission_from_client_name("Arista Networks"),
        do_scrape=False, do_web=False, auto_approve=False,
        review_dir=str(tmp_path / "review"), alert_fn=lambda *a: None,
    )
    assert constructed == []
    assert result.identity.status == "abstain"
    assert not result.identity.is_bound
    assert "name alone" in result.identity.rationale


def test_empty_name_is_blocked():
    out = bind_identity("  ", [])
    assert out.status == "blocked"


def test_registrable_domain_strips_www():
    assert registrable_domain("https://www.Example.com/about") == "example.com"
    assert registrable_domain("") is None


# ---- dossier schema -------------------------------------------------------- #

def test_dossier_schema_records_claim_states_and_unknowns():
    ident = bind_identity(
        "Acme Federal Solutions LLC",
        [], form_website="https://acmefederal.com")
    sub = IntakeSubmission(
        client_name="Acme Federal Solutions LLC",
        website="https://acmefederal.com",
        primary_services="Cloud migration and DevSecOps",
        known_naics=["541512"],
    )
    ingest = {
        "capabilities": [
            {"name": "Cloud Security Monitoring",
             "found_on": "https://acmefederal.com/solutions"},
        ],
    }
    dossier = build_dossier(
        client_name=sub.client_name, identity=ident,
        submission=sub, product_ingest=ingest)
    assert dossier.schema_version == "intake_dossier.v1"
    states = {c.state for c in dossier.offerings}
    assert ClaimState.COMPANY_ASSERTED in states
    assert any(n.code == "541512" for n in dossier.naics)
    assert dossier.capability_statements
    assert any(k.term == "Cloud Security Monitoring" for k in dossier.keywords)


def test_unbound_identity_dossier_does_not_invent_the_company():
    ident = resolve_identity("Pioneer")
    dossier = build_dossier(client_name="Pioneer", identity=ident)
    assert not ident.is_bound
    assert dossier.unknowns
    assert not any(n.code for n in dossier.naics)


# ---- yield sidecar --------------------------------------------------------- #

def test_empty_store_is_named_empty_not_false_zeros(tmp_path, monkeypatch):
    from tools import notice_store as ns

    monkeypatch.setenv("LILA_NOTICE_STORE_DIR", str(tmp_path / "empty"))
    conn = ns.connect(tmp_path / "empty" / "notices.db")
    census = notice_store_census(conn=conn)
    assert census["status"] == "empty"
    receipt = intake_yield_receipt(
        ["cloud migration", "observability"], conn=conn, client_name="Acme")
    assert receipt["store"]["status"] == "empty"
    assert receipt["terms"] == []
    assert "not a market zero" in receipt["note"]


def test_ready_store_returns_counts_and_titles(tmp_path, monkeypatch):
    from tools import notice_store as ns

    monkeypatch.setenv("LILA_NOTICE_STORE_DIR", str(tmp_path / "store"))
    conn = ns.connect(tmp_path / "store" / "notices.db")
    conn.execute(
        "INSERT INTO notices (notice_id, title, description_prefix, "
        "first_seen, last_seen) VALUES (?,?,?,?,?)",
        ("n1", "Cloud migration support", "enterprise cloud migration",
         "2026-09-01", "2026-09-01"))
    conn.commit()
    receipt = intake_yield_receipt(["cloud migration"], conn=conn)
    assert receipt["store"]["status"] == "ready"
    assert receipt["store"]["row_count"] == 1
    assert receipt["terms"][0]["count"] == 1
    assert "Cloud migration support" in receipt["terms"][0]["sample_titles"]
    assert "verdict" not in receipt["terms"][0]


# ---- adversarial fail-closed ----------------------------------------------- #

def _bound_dossier(**kwargs):
    ident = bind_identity(
        "Acme Federal Solutions LLC", [],
        form_website="https://acmefederal.com")
    base = dict(
        client_name="Acme Federal Solutions LLC",
        identity=ident,
        offerings=[Claim(
            text="Cloud migration", state=ClaimState.COMPANY_ASSERTED,
            evidence_ids=["E001"], rationale="stated on the form")],
        evidence=[{
            "evidence_id": "E001", "source_kind": "form",
            "excerpt": "Cloud migration",
        }],
    )
    base.update(kwargs)
    return CompanyDossier.model_validate(base)


def test_adversarial_incomplete_is_not_a_pass():
    rec = challenge_dossier(_bound_dossier(), ran=False)
    assert rec.complete is False
    assert rec.passed is False
    assert any(c.kind == "incomplete" for c in rec.challenges)


def test_adversarial_unbound_identity_blocks():
    ident = resolve_identity("Pioneer")
    dossier = build_dossier(client_name="Pioneer", identity=ident)
    rec = challenge_dossier(dossier, ran=True)
    assert rec.complete is True
    assert rec.passed is False
    assert any(c.kind == "identity" and c.severity == "block"
               for c in rec.challenges)


def test_adversarial_unsupported_offering_blocks_and_repairs():
    dossier = _bound_dossier(offerings=[Claim(
        text="Quantum teleportation", state=ClaimState.CORROBORATED,
        evidence_ids=["MISSING"], rationale="invented")])
    repaired, rec = run_adversarial(dossier)
    assert rec.complete is True
    assert any("removed unsupported offering" in u for u in repaired.unknowns)
    assert rec.repairs


def test_thin_naics_rationale_is_overbreadth():
    dossier = _bound_dossier(naics=[{
        "code": "541512", "role": "core", "rationale": "IT",
        "state": "inferred",
    }])
    rec = challenge_dossier(dossier, ran=True)
    assert any(c.kind == "naics_overbreadth" for c in rec.challenges)
    assert rec.passed is False


# ---- adapters / retrieval -------------------------------------------------- #

def test_adapter_does_not_emit_search_plan_when_identity_unbound():
    ident = resolve_identity("Pioneer")
    dossier = build_dossier(client_name="Pioneer", identity=ident)
    strategy = strategy_from_dossier(dossier)
    assert strategy.searches == []
    assert strategy.requires_human_review is True
    assert strategy.inferred_naics == []


def test_adapter_emits_strategy_and_frame_lanes_from_bound_dossier():
    ident = bind_identity(
        "Acme Federal Solutions LLC", [],
        form_website="https://acmefederal.com")
    sub = IntakeSubmission(
        client_name="Acme Federal Solutions LLC",
        known_naics=["541512"],
        primary_services="Cloud migration",
        certifications=["SDVOSB"],
    )
    dossier = build_dossier(
        client_name=sub.client_name, identity=ident, submission=sub,
        product_ingest={"capabilities": [{"name": "Cloud Security Monitoring"}]})
    strategy = strategy_from_dossier(dossier, sub)
    assert strategy.client_name == sub.client_name
    assert "541512" in strategy.inferred_naics
    assert {s.source for s in strategy.searches} >= {
        "sam.gov", "usaspending.gov", "web"}
    assert strategy.requires_human_review is True
    frame = retrieval_frame(dossier)
    assert frame["capability_statements"]
    assert "bm25_terms" in frame["lanes"]
    assert "dense_queries" in frame["lanes"]


# ---- name-only pipeline + auto-approve + no search ------------------------- #

def test_name_only_bound_path_auto_approves_without_searching(
        tmp_path, monkeypatch):
    import agents.review as review
    from agents.review import load_approved

    monkeypatch.setattr(review, "REVIEW_DIR", str(tmp_path / "review"))
    monkeypatch.setenv("LILA_CLIENTS_DIR", str(tmp_path / "clients"))
    monkeypatch.setenv("LILA_NOTICE_STORE_DIR", str(tmp_path / "empty_store"))
    monkeypatch.setenv("LILA_ENABLE_INTAKE_AUTO_APPROVE", "on")

    searches_launched = []
    monkeypatch.setattr(
        "run_searches.main",
        lambda *a, **k: searches_launched.append(1), raising=False)

    sub = submission_from_client_name(
        "Acme Federal Solutions LLC",
        website="https://acmefederal.com",
    )
    result = run_step1(
        sub, do_scrape=False, do_web=False, auto_approve=True,
        review_dir=str(tmp_path / "review"), alert_fn=lambda *a: None,
    )
    assert result.identity.is_bound
    assert result.auto_approved is True
    assert result.status == "approved"
    assert result.dossier is not None
    assert result.yield_receipt["store"]["status"] == "empty"
    assert result.adversarial is not None
    assert result.adversarial.complete is True
    assert (tmp_path / "review" / "acme_federal_solutions_llc.dossier.json").is_file()
    assert (tmp_path / "review" / "acme_federal_solutions_llc.intake_yield.json").is_file()
    assert (tmp_path / "review" / "acme_federal_solutions_llc.intake_adversarial.json").is_file()
    assert (tmp_path / "review" / "acme_federal_solutions_llc.intake_readiness.json").is_file()
    approved = load_approved("Acme Federal Solutions LLC")
    assert approved.client_name == "Acme Federal Solutions LLC"
    assert searches_launched == []
    # client files exist and still do not invent scope.preset
    scope_path = tmp_path / "clients" / "acme_federal_solutions_llc" / "engagement_scope.json"
    if scope_path.is_file():
        scope = json.loads(scope_path.read_text())
        assert scope.get("preset") in ("", None)


def test_auto_approve_off_leaves_pending(tmp_path, monkeypatch):
    import agents.review as review
    from agents.review import load_approved, load_packet

    monkeypatch.setattr(review, "REVIEW_DIR", str(tmp_path / "review"))
    monkeypatch.setenv("LILA_CLIENTS_DIR", str(tmp_path / "clients"))
    monkeypatch.setenv("LILA_NOTICE_STORE_DIR", str(tmp_path / "empty_store"))
    monkeypatch.setenv("LILA_ENABLE_INTAKE_AUTO_APPROVE", "off")

    sub = submission_from_client_name(
        "Acme Federal Solutions LLC",
        website="https://acmefederal.com",
    )
    result = run_step1(
        sub, do_scrape=False, do_web=False, auto_approve=False,
        review_dir=str(tmp_path / "review"), alert_fn=lambda *a: None,
    )
    assert result.auto_approved is False
    assert load_packet("Acme Federal Solutions LLC").status.value == "pending"
    with pytest.raises(PermissionError):
        load_approved("Acme Federal Solutions LLC")


def test_ambiguous_name_does_not_auto_approve_or_scrape(tmp_path, monkeypatch):
    import agents.review as review
    from agents.company_research import WebsiteGuess

    monkeypatch.setattr(review, "REVIEW_DIR", str(tmp_path / "review"))
    monkeypatch.setenv("LILA_CLIENTS_DIR", str(tmp_path / "clients"))
    monkeypatch.setenv("LILA_NOTICE_STORE_DIR", str(tmp_path / "empty_store"))

    scraped = []

    class AmbiguousEngine:
        def web_research(self, **_kwargs):
            return "Pioneer Systems vs Pioneer Seed", ["https://a.example", "https://b.example"]

        def structure(self, **_kwargs):
            return type("R", (), {"candidates": [
                IdentityCandidate(
                    name="Pioneer Systems", official_domain="pioneersys.com",
                    website="https://pioneersys.com", confidence=0.9,
                    rationale="IT"),
                IdentityCandidate(
                    name="Pioneer Seed", official_domain="pioneer.com",
                    website="https://pioneer.com", confidence=0.86,
                    rationale="ag"),
            ], "ambiguity_note": "two firms"})()

    monkeypatch.setattr(
        "tools.scrape.site.scrape_site",
        lambda url, max_pages=5: scraped.append(url) or None)

    result = run_step1(
        submission_from_client_name("Pioneer"),
        research_engine=AmbiguousEngine(),
        do_scrape=True, do_web=True, auto_approve=True,
        review_dir=str(tmp_path / "review"), alert_fn=lambda *a: None,
    )
    assert result.identity.status == "abstain"
    assert result.auto_approved is False
    assert scraped == []
    assert result.adversarial.passed is False
    e1 = next(r for r in result.readiness["receipts"] if r["id"] == "E1")
    assert e1["ok"] is False


def test_name_only_cli_does_not_require_submission():
    from run_intake import build_arg_parser, load_submission

    args = build_arg_parser().parse_args(["--client", "Acme Federal Solutions LLC"])
    sub = load_submission(args)
    assert sub.client_name == "Acme Federal Solutions LLC"
    assert sub.website is None
    with pytest.raises(SystemExit):
        build_arg_parser().parse_args([])


def test_cli_submission_and_client_are_mutually_exclusive():
    from run_intake import build_arg_parser

    with pytest.raises(SystemExit):
        build_arg_parser().parse_args([
            "--client", "Acme", "--submission", "x.json"])


def test_step_cmd_name_only_intake():
    pytest.importorskip("flask")
    import ui.server as srv

    cmd = srv._step_cmd("intake", "Acme Federal", {})
    assert cmd[-2:] == ["--client", "Acme Federal"]
    cmd2 = srv._step_cmd(
        "intake", "Acme Federal",
        {"submission_path": "/tmp/x.json", "no_auto_approve": True})
    assert "--submission" in cmd2
    assert "--no-auto-approve" in cmd2


def test_api_run_starts_name_only_intake_without_a_packet(tmp_path, monkeypatch):
    pytest.importorskip("flask")
    import ui.server as srv

    monkeypatch.setattr(srv, "REVIEW_DIR", str(tmp_path / "review"))
    started = []

    def fake_start(step, client, args):
        started.append((step, client, args))
        return "job-name-only"

    monkeypatch.setattr(srv, "start_job", fake_start)
    r = srv.app.test_client().post("/api/run", json={
        "client_name": "Brand New Co", "step": "intake", "args": {}})
    assert r.status_code == 200
    body = r.get_json()
    assert body["job_id"] == "job-name-only"
    assert body["name_only"] is True
    assert started == [("intake", "Brand New Co", {})]


def test_readiness_e1_e8_shape():
    ident = bind_identity(
        "Acme Federal Solutions LLC", [],
        form_website="https://acmefederal.com")
    dossier = build_dossier(
        client_name="Acme Federal Solutions LLC", identity=ident,
        submission=IntakeSubmission(
            client_name="Acme Federal Solutions LLC",
            primary_services="Cloud", known_naics=["541512"]))
    rec = challenge_dossier(dossier, ran=True)
    ready = evaluate_readiness(
        identity=ident, dossier=dossier, yield_receipt={
            "store": {"status": "empty", "row_count": 0, "note": "empty"},
            "terms": [], "note": "notice store has zero rows; counts are not a market zero",
        }, adversarial=rec)
    ids = [row["id"] for row in ready["receipts"]]
    assert ids == ["E1", "E2", "E3", "E4", "E5", "E6", "E7", "E8"]
    assert ready["receipts"][0]["ok"] is True
