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

from agents.company_research import CompanyResearch
from agents.intake.adapters import (
    apply_kept_out,
    merge_strategy_into_dossier,
    retrieval_frame,
    strategy_from_dossier,
)
from agents.intake.adversarial import (
    Challenge,
    challenge_dossier,
    conflicting_core_naics,
    run_adversarial,
    soften_related_entity_blocks,
)
from agents.intake.dossier import (
    Claim,
    ClaimState,
    CompanyDossier,
    DossierKeyword,
    DossierNaics,
    build_dossier,
)
from agents.intake.extract import (
    excerpt_supports_name,
    extract_naics,
    is_discrete_name,
    is_plausible_naics_code,
    is_product_name,
    looks_like_contract_or_schedule_id,
)
from agents.intake.probes import PROBE_SPECS, ResearchProbe
from tools.scrape.site import ScrapedPage, ScrapeBundle
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
    from tools.scrape.site import SITE_MASTERY_MAX_PAGES
    assert args.max_pages == SITE_MASTERY_MAX_PAGES
    assert args.max_pages >= 24
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


# ---- Arista dossier quality (CONDITIONAL 11/16 follow-up) ------------------- #

_ARISTA_OFFERINGS = """\
## Products
- **EOS** (Extensible Operating System): the network operating system
- CloudVision / AGNI / CUE / UNO for automation and observability
- DANZ Monitoring Fabric
- Network Detection and Response (NDR)
- 7050X and 7280R switch families
"""

_ARISTA_FEDERAL = """\
Arista Networks, Inc. is the public company at arista.com. Federal orders
often route through Arista Networks Government Sales LLC. Research cites
NAICS 334118 for computer terminal and related equipment manufacturing and
NAICS 541519 for other computer related services.
"""

_ARISTA_BOUNDARIES = """\
Name collisions include Arista Records (music), Arista Aviation, Aristan
Project Management, and OAS Aircraft Support. Those firms are not this
company and must stay out of the search vocabulary.
"""


def _arista_identity():
    return bind_identity(
        "Arista Networks",
        [IdentityCandidate(
            name="Arista Networks, Inc.", official_domain="arista.com",
            website="https://www.arista.com", confidence=0.97,
            rationale="official corporate homepage")],
    )


def _arista_research(*, failed_scrape=True):
    pages = []
    if failed_scrape:
        pages = [ScrapedPage(
            url="https://www.arista.com",
            text="Error loading the page. Enable JavaScript to continue.",
        )]
    return CompanyResearch(
        company_name="Arista Networks",
        website="https://www.arista.com",
        website_source="web_search",
        scrape=ScrapeBundle(root_url="https://www.arista.com", pages=pages,
                            sources=["https://www.arista.com"]),
        web_citations=["https://www.arista.com/en/products"],
        errors=["scrape: homepage load error"],
    )


def _arista_probes():
    return [
        ResearchProbe(
            name="offerings", query="products",
            findings=_ARISTA_OFFERINGS,
            citations=["https://www.arista.com/en/products/eos"],
        ),
        ResearchProbe(
            name="federal_footprint", query="federal",
            findings=_ARISTA_FEDERAL,
            citations=["https://www.arista.com/en/company/government"],
        ),
        ResearchProbe(
            name="boundaries", query="exclusions",
            findings=_ARISTA_BOUNDARIES,
            citations=["https://en.wikipedia.org/wiki/Arista_Records"],
        ),
    ]


def test_failed_scrape_and_probe_essay_do_not_become_offerings():
    ident = _arista_identity()
    dossier = build_dossier(
        client_name="Arista Networks",
        identity=ident,
        research=_arista_research(),
        probes=_arista_probes(),
    )
    texts = [o.text for o in dossier.offerings]
    assert texts
    assert all(is_discrete_name(t) for t in texts)
    blob = " ".join(texts).casefold()
    assert "error loading" not in blob
    assert "javascript" not in blob
    assert "name collisions include" not in blob
    assert {"EOS", "CloudVision", "AGNI", "7050X"} <= set(texts)
    assert not any(e.excerpt.strip().casefold() == "citation" for e in dossier.evidence)
    assert all(e.excerpt.strip() for e in dossier.evidence)
    assert {n.code for n in dossier.naics} >= {"334118", "541519"}
    assert all(n.evidence_ids for n in dossier.naics)
    assert all(len((n.rationale or "").split()) >= 5 for n in dossier.naics)
    exclusions = {k.term.casefold() for k in dossier.kept_out}
    assert any("arista records" in x for x in exclusions)
    assert any("aviation" in x for x in exclusions)
    assert any("aristan" in x or "oas aircraft" in x for x in exclusions)
    assert any("government sales" in r.name.casefold()
               for r in dossier.related_entities)
    assert ident.is_bound and ident.official_domain == "arista.com"

    frame = retrieval_frame(dossier)
    tier1 = frame["frame"]["as_ordered"]["tier1"]
    assert any(t in {"EOS", "CloudVision", "AGNI", "DANZ Monitoring Fabric"}
               for t in tier1)
    assert all(is_discrete_name(t) for t in tier1 if t != "Arista Networks")
    assert not any(t.casefold().startswith("error") for t in tier1)
    assert any("CloudVision" in s or "EOS" in s
               for s in frame["capability_statements"])

    rec = challenge_dossier(dossier, ran=True)
    assert not any(
        c.kind == "identity" and c.severity == "block" for c in rec.challenges)
    assert rec.passed is True


def test_dual_entity_does_not_hard_block_correct_bind():
    ident = _arista_identity()
    dossier = build_dossier(
        client_name="Arista Networks",
        identity=ident,
        research=_arista_research(),
        probes=_arista_probes(),
    )
    rec = challenge_dossier(dossier, ran=True)
    rec.challenges.append(Challenge(
        kind="identity", severity="block",
        statement=(
            "Arista Networks, Inc. vs Arista Networks Government Sales LLC "
            "are two legal persons"
        ),
        external_refs=["arista.com"],
    ))
    rec.passed = False
    softened = soften_related_entity_blocks(rec, dossier)
    assert not any(
        c.kind == "identity" and c.severity == "block"
        for c in softened.challenges)
    assert softened.passed is True


def test_wrong_domain_identity_block_is_not_softened():
    ident = _arista_identity()
    dossier = build_dossier(client_name="Arista Networks", identity=ident)
    rec = challenge_dossier(dossier, ran=True)
    rec.challenges.append(Challenge(
        kind="identity", severity="block",
        statement="bound domain is the music label, not the network vendor",
        external_refs=["aristarecords.com"],
    ))
    rec.passed = False
    softened = soften_related_entity_blocks(rec, dossier)
    assert any(
        c.kind == "identity" and c.severity == "block"
        for c in softened.challenges)
    assert softened.passed is False


def test_arista_pipeline_quality_without_injected_engines(tmp_path, monkeypatch):
    import agents.review as review
    from agents.intake.identity import IdentityRoster

    monkeypatch.setattr(review, "REVIEW_DIR", str(tmp_path / "review"))
    monkeypatch.setenv("LILA_CLIENTS_DIR", str(tmp_path / "clients"))
    monkeypatch.setenv("LILA_NOTICE_STORE_DIR", str(tmp_path / "empty_store"))
    monkeypatch.setenv("LILA_ENABLE_INTAKE_AUTO_APPROVE", "off")

    class Engine:
        def web_research(self, **kwargs):
            query = str(kwargs.get("query") or "")
            if "official company" in query.casefold() or "Identify" in query:
                return (
                    "Arista Networks official site https://www.arista.com",
                    ["https://www.arista.com"],
                )
            q = query.casefold()
            if "namesake" in q or "music label" in q or "lookalike spelling" in q:
                return _ARISTA_BOUNDARIES, [
                    "https://en.wikipedia.org/wiki/Arista_Records"]
            if "naics" in q or "federal" in q or "industry classification" in q:
                return _ARISTA_FEDERAL, [
                    "https://www.arista.com/en/company/government"]
            return _ARISTA_OFFERINGS, [
                "https://www.arista.com/en/products/eos"]

        def structure(self, **kwargs):
            schema = kwargs.get("schema")
            name = getattr(schema, "__name__", "")
            if name == "StructuredProductSurface":
                return schema(
                    offerings=["EOS", "CloudVision", "DANZ Monitoring Fabric"],
                    naics=["334118", "541519"],
                    exclusions=["Arista Records", "Arista Aviation",
                                "Aristan Project Management",
                                "OAS Aircraft Support"],
                    related_entities=["Arista Networks Government Sales LLC"],
                )
            return IdentityRoster(candidates=[
                IdentityCandidate(
                    name="Arista Networks, Inc.", official_domain="arista.com",
                    website="https://www.arista.com", confidence=0.97,
                    rationale="official corporate homepage",
                ),
            ])

        def deliberate(self, *a, **k):
            raise RuntimeError("strategy unused")

    monkeypatch.setattr(
        "agents.decisions.engine.research_engine", lambda: Engine())
    monkeypatch.setattr(
        "agents.decisions.engine.DecisionEngine", lambda *a, **k: Engine())
    def _js_shell(url, max_pages=5, **_k):
        return ScrapeBundle(
            root_url=url,
            pages=[ScrapedPage(
                url=url,
                text="Error loading the page. Enable JavaScript.",
            )],
            sources=[url],
        )

    monkeypatch.setattr("tools.scrape.site.scrape_site", _js_shell)
    monkeypatch.setattr("agents.company_research.scrape_site", _js_shell)
    monkeypatch.setattr(
        "agents.intake.pipeline._ingest_bound_site",
        lambda *a, **k: {"capabilities": [], "receipt": {"error": "load"}},
    )

    searches = []
    monkeypatch.setattr(
        "run_searches.main", lambda *a, **k: searches.append(1), raising=False)

    result = run_step1(
        submission_from_client_name("Arista Networks"),
        do_scrape=True, do_web=True, auto_approve=False,
        review_dir=str(tmp_path / "review"), alert_fn=lambda *a: None,
    )
    assert result.identity.is_bound
    assert result.identity.official_domain == "arista.com"
    texts = [o.text for o in result.dossier.offerings]
    assert all(is_discrete_name(t) for t in texts)
    assert {"EOS", "CloudVision", "AGNI", "7050X"} <= set(texts)
    assert {n.code for n in result.dossier.naics} >= {"334118", "541519"}
    assert all(n.evidence_ids for n in result.dossier.naics)
    assert result.dossier.kept_out
    assert not any(
        e.excerpt.strip().casefold() == "citation" for e in result.dossier.evidence)
    frame = result.retrieval
    tier1 = frame["frame"]["as_ordered"]["tier1"]
    assert any(t in {"EOS", "CloudVision"} for t in tier1)
    assert searches == []
    scope = tmp_path / "clients" / "arista_networks" / "engagement_scope.json"
    if scope.is_file():
        assert json.loads(scope.read_text()).get("preset") in ("", None)


def test_product_precision_rejects_noise_and_rival_keeps_agni_7050x():
    ident = _arista_identity()
    probes = [
        ResearchProbe(
            name="offerings", query="products",
            findings=(
                "## Products\n"
                "- EOS and CloudVision AGNI (Guardian for Network Identity)\n"
                "- DANZ Monitoring Fabric\n"
                "- 7050X family and 7280R / 7500R / 7800R switches\n"
                "- Note on scope\n"
                "- 20\n"
                "- Gap\n"
                "- GSA Schedule 70 and NASA SEWP\n"
                "Buyers sometimes compare this to VMware VeloCloud, "
                "which is not an Arista product.\n"
            ),
            citations=["https://www.arista.com/en/products"],
        ),
        ResearchProbe(
            name="federal_footprint", query="federal",
            findings=(
                "NYSE: ANET CIK 0001420800. Federal path is Arista Networks "
                "Government Sales LLC. NAICS 334118 and 541519. Also listed "
                "NAICS 334210 without further use.\n"
            ),
            citations=["https://www.arista.com/en/company/government"],
        ),
        ResearchProbe(
            name="boundaries", query="exclusions",
            findings=(
                "Section header Overview. Do not confuse with Arista Records, "
                "Arista Aviation, Aristan PM, or OAS Aircraft Support. "
                "Bare token Arista is this company.\n"
            ),
            citations=["https://en.wikipedia.org/wiki/Arista_Records"],
        ),
    ]
    dossier = build_dossier(
        client_name="Arista Networks",
        identity=ident,
        research=_arista_research(),
        probes=probes,
    )
    texts = [o.text for o in dossier.offerings]
    assert {"EOS", "CloudVision", "AGNI", "DANZ Monitoring Fabric",
            "7050X", "7280R"} <= set(texts)
    low = {t.casefold() for t in texts}
    assert "20" not in low
    assert "gap" not in low
    assert "note on scope" not in low
    assert "gsa" not in low and "sewp" not in low
    assert "velocloud" not in low
    assert not any(t.casefold() == "arista" for t in texts)
    frame = retrieval_frame(dossier)
    tier1 = [t.casefold() for t in frame["frame"]["as_ordered"]["tier1"]]
    assert "20" not in tier1 and "gap" not in tier1 and "velocloud" not in tier1
    assert any(t == "agni" for t in tier1) and any(t == "7050x" for t in tier1)
    assert all(n.evidence_ids for n in dossier.naics)
    assert {n.code for n in dossier.naics} >= {"334118", "541519"}
    kept = {k.term.casefold() for k in dossier.kept_out}
    assert any("records" in k for k in kept)
    assert any("aviation" in k for k in kept)
    assert any("aristan" in k for k in kept)
    assert any("oas aircraft" in k for k in kept)
    assert "nyse" not in kept
    assert not any(k.startswith("cik") for k in kept)
    assert "arista" not in kept
    assert "overview" not in kept
    rec = challenge_dossier(dossier, ran=True)
    assert rec.passed is True


def test_strategy_naics_without_evidence_are_dropped_not_asserted():
    ident = _arista_identity()
    dossier = build_dossier(
        client_name="Arista Networks",
        identity=ident,
        research=_arista_research(),
        probes=_arista_probes(),
    )
    strategy = strategy_from_dossier(dossier)
    fake = type("S", (), {
        "research_entities": [
            type("E", (), {"name": "VeloCloud", "kind": "product"})(),
            type("E", (), {"name": "20", "kind": "product"})(),
        ],
        "keywords": [],
        "inferred_naics": list(strategy.inferred_naics) + ["334210", "423430"],
        "naics_meta": [],
    })()
    merged = merge_strategy_into_dossier(dossier, fake)
    assert "334210" not in {n.code for n in merged.naics}
    assert "423430" not in {n.code for n in merged.naics}
    assert all(n.evidence_ids for n in merged.naics)
    names = {o.text.casefold() for o in merged.offerings}
    assert "velocloud" not in names
    assert "20" not in names
    assert merged.naics, "must not empty evidenced NAICS to satisfy a gate"


def test_naics_not_emptied_to_pass_e8():
    """E8 must pass with evidenced NAICS present, not because the list is empty."""
    ident = _arista_identity()
    fluff = ("Federal market narrative. " * 40)
    probes = [
        ResearchProbe(
            name="offerings", query="products",
            findings=(
                "CloudVision AGNI (Guardian for Network Identity) and EOS. "
                "The 7050X Series and 7280R family. "
                "WebSearch page title Home | Arista. CVP WAN APL JITC. "
                "Buyers compare this to VMware VeloCloud.\n"
            ),
            citations=["https://www.arista.com/en/products"],
        ),
        ResearchProbe(
            name="federal_footprint", query="federal",
            findings=fluff + (
                "Classification uses NAICS 334118 for computer terminal "
                "equipment and NAICS 541519 for other computer related "
                "services, plus 334210 in the same NAICS discussion.\n"
            ),
            citations=["https://www.arista.com/en/company/government"],
        ),
        ResearchProbe(
            name="boundaries", query="exclusions",
            findings=(
                "or IT contractors generally. not a company in music. "
                "Collisions: Arista Records, aviation services around Arista, "
                "Aristan PM, OAS Aircraft Support.\n"
            ),
            citations=["https://en.wikipedia.org/wiki/Arista_Records"],
        ),
    ]
    dossier = build_dossier(
        client_name="Arista Networks",
        identity=ident,
        research=_arista_research(),
        probes=probes,
    )
    texts = {o.text for o in dossier.offerings}
    assert "AGNI" in texts and "7050X" in texts
    low = {t.casefold() for t in texts}
    assert "velocloud" not in low
    assert "websearch" not in low
    assert "cvp" not in low and "wan" not in low and "jitc" not in low
    assert {n.code for n in dossier.naics} >= {"334118", "541519"}
    assert all(n.evidence_ids for n in dossier.naics)
    assert "no six-digit NAICS" not in " ".join(dossier.unknowns)
    kept = {k.term.casefold() for k in dossier.kept_out}
    assert any("records" in k for k in kept)
    assert any("aviation" in k for k in kept)
    assert any("aristan" in k for k in kept)
    assert any("oas aircraft" in k for k in kept)
    assert "or it" not in kept
    assert "not a company" not in kept
    rec = challenge_dossier(dossier, ran=True)
    assert rec.passed is True
    assert dossier.naics, "E8 green with an empty NAICS list is a gate bypass"


# ---- Press 5: live evidence bottleneck, not extract-only ------------------- #

_PRESS5_OFFERINGS = (
    "Arista ships EOS and CloudVision. DANZ Monitoring Fabric and the "
    "7060X6 and 7800R4 platforms appear on the switching page. "
    "Portfolio essay omits identity products and the 7050 family."
)
_PRESS5_FEDERAL = (
    "Arista holds GSA MAS 47QSWA18D008F and NASA SEWP vehicle 0119Y "
    "for switching. No industry classification codes are stated."
)
_PRESS5_BOUNDARIES = (
    "Name collision: Arista Records is a separate music label. "
    "No other namesakes were researched."
)


def _press5_probes():
    return [
        ResearchProbe(
            name="offerings", query="products",
            findings=_PRESS5_OFFERINGS,
            citations=["https://www.arista.com/en/products"],
        ),
        ResearchProbe(
            name="federal_footprint", query="federal",
            findings=_PRESS5_FEDERAL,
            citations=["https://www.gsaelibrary.gsa.gov"],
        ),
        ResearchProbe(
            name="boundaries", query="exclusions",
            findings=_PRESS5_BOUNDARIES,
            citations=["https://en.wikipedia.org/wiki/Arista_Records"],
        ),
    ]


def test_structured_probes_seek_named_products_naics_and_namesakes():
    names = [name for name, _ in PROBE_SPECS]
    assert "customers" in names
    assert "competitors" in names
    assert names[-1] == "customers"
    blob = " ".join(focus for _, focus in PROBE_SPECS).casefold()
    assert "agni" in blob or "guardian for network identity" in blob
    assert "7050x" in blob
    assert "naics" in blob
    assert "records" in blob or "music" in blob
    assert "aviation" in blob
    assert "award" in blob or "sewp" in blob
    assert "case stud" in blob or "customers" in blob
    assert "compare" in blob or "alternativ" in blob or "vs" in blob


def test_schedule_and_award_ids_are_not_products():
    assert looks_like_contract_or_schedule_id("47QSWA18D008F") is True
    assert looks_like_contract_or_schedule_id("0119Y") is True
    assert is_product_name("47QSWA18D008F") is False
    assert is_product_name("0119Y") is False
    assert is_product_name("7050X") is True
    assert is_product_name("7060X6") is True
    assert is_product_name("7800R4") is True
    assert is_product_name("EOS") is True
    assert is_product_name("CloudVision") is True


def test_press5_schedule_ids_rejected_and_split_brain_naics_flagged():
    """Live Press 5 shape: schedule IDs in evidence, no AGNI/NAICS."""
    ident = _arista_identity()
    dossier = build_dossier(
        client_name="Arista Networks",
        identity=ident,
        research=_arista_research(),
        probes=_press5_probes(),
    )
    texts = {o.text for o in dossier.offerings}
    assert {"EOS", "CloudVision", "DANZ Monitoring Fabric",
            "7060X6", "7800R4"} <= texts
    assert "47QSWA18D008F" not in texts
    assert "0119Y" not in texts
    assert "AGNI" not in texts
    assert "7050X" not in texts
    assert dossier.naics == []
    excerpts = " ".join(e.excerpt or "" for e in dossier.evidence)
    assert "47QSWA18D008F" in excerpts or "0119Y" in excerpts
    assert "AGNI" not in excerpts
    kept = {k.term.casefold() for k in dossier.kept_out}
    assert any("records" in k for k in kept)
    assert not any("aviation" in k for k in kept)

    strategy = {
        "inferred_naics": [
            "334210", "334220", "513210", "423430", "541519", "541512",
        ],
    }
    rec = challenge_dossier(dossier, ran=True, strategy=strategy)
    assert any(
        c.kind == "naics_overbreadth" and c.severity == "block"
        for c in rec.challenges)
    assert rec.passed is False
    ready = evaluate_readiness(
        identity=ident, dossier=dossier, probes=_press5_probes(),
        yield_receipt={
            "store": {"status": "empty", "row_count": 0, "note": "empty"},
            "terms": [],
            "note": "notice store has zero rows; counts are not a market zero",
        },
        adversarial=rec, strategy=strategy,
    )
    by_id = {row["id"]: row for row in ready["receipts"]}
    assert by_id["E5"]["ok"] is False
    assert by_id["E8"]["ok"] is False
    assert ready["all_ok"] is False


def test_probe_text_with_agni_naics_aviation_lands_on_dossier():
    """When probes actually cite the strings, the dossier must capture them."""
    ident = _arista_identity()
    probes = [
        ResearchProbe(
            name="offerings", query="products",
            findings=(
                "Official pages name CloudVision AGNI (Guardian for Network "
                "Identity) and the 7050X series alongside EOS, CloudVision, "
                "and DANZ Monitoring Fabric."
            ),
            citations=["https://www.arista.com/en/products"],
        ),
        ResearchProbe(
            name="federal_footprint", query="federal",
            findings=(
                "The SAM listing states NAICS 334118 for computer terminal "
                "equipment and NAICS 334210 for telephone apparatus "
                "manufacturing."
            ),
            citations=["https://sam.gov"],
        ),
        ResearchProbe(
            name="classifications", query="classifications",
            findings=(
                "Industry classification 541519 appears next to other "
                "computer related services on the same registration."
            ),
            citations=["https://sam.gov"],
        ),
        ResearchProbe(
            name="boundaries", query="exclusions",
            findings=(
                "Namesakes include Arista Aviation, Aristan, and OAS "
                "Aircraft Support, plus Arista Records."
            ),
            citations=["https://en.wikipedia.org/wiki/Arista_Records"],
        ),
    ]
    dossier = build_dossier(
        client_name="Arista Networks",
        identity=ident,
        research=_arista_research(),
        probes=probes,
    )
    texts = {o.text for o in dossier.offerings}
    assert {"EOS", "CloudVision", "AGNI", "7050X",
            "DANZ Monitoring Fabric"} <= texts
    assert {n.code for n in dossier.naics} >= {"334118", "334210", "541519"}
    assert all(n.evidence_ids for n in dossier.naics)
    excerpts = " ".join(e.excerpt or "" for e in dossier.evidence)
    assert "AGNI" in excerpts
    assert "7050X" in excerpts
    assert "NAICS" in excerpts or "334118" in excerpts
    kept = {k.term.casefold() for k in dossier.kept_out}
    assert any("records" in k for k in kept)
    assert any("aviation" in k for k in kept)
    assert any("aristan" in k for k in kept)
    assert any("oas aircraft" in k for k in kept)
    rec = challenge_dossier(dossier, ran=True)
    assert rec.passed is True


def test_press5_pipeline_flags_invented_strategy_naics(
        tmp_path, monkeypatch):
    """After strategy invents NAICS the dossier cannot evidence, E5/E8 fail."""
    import agents.review as review
    from agents.decisions.schemas import IntakeStrategy
    from agents.intake.identity import IdentityRoster

    monkeypatch.setattr(review, "REVIEW_DIR", str(tmp_path / "review"))
    monkeypatch.setenv("LILA_CLIENTS_DIR", str(tmp_path / "clients"))
    monkeypatch.setenv("LILA_NOTICE_STORE_DIR", str(tmp_path / "empty_store"))
    monkeypatch.setenv("LILA_ENABLE_INTAKE_AUTO_APPROVE", "off")

    class Engine:
        def web_research(self, **kwargs):
            query = str(kwargs.get("query") or "")
            q = query.casefold()
            if "official company" in q or "identify" in q:
                return (
                    "Arista Networks official site https://www.arista.com",
                    ["https://www.arista.com"],
                )
            if "namesake" in q or "music label" in q or "lookalike spelling" in q:
                return _PRESS5_BOUNDARIES, [
                    "https://en.wikipedia.org/wiki/Arista_Records"]
            if "naics" in q or "federal" in q or "industry classification" in q:
                return _PRESS5_FEDERAL, [
                    "https://www.gsaelibrary.gsa.gov"]
            return _PRESS5_OFFERINGS, [
                "https://www.arista.com/en/products"]

        def structure(self, **kwargs):
            schema = kwargs.get("schema")
            name = getattr(schema, "__name__", "")
            if name == "StructuredProductSurface":
                return schema(
                    offerings=["EOS", "CloudVision", "DANZ Monitoring Fabric",
                               "7060X6", "7800R4"],
                    naics=[],
                    exclusions=["Arista Records"],
                    related_entities=[],
                )
            return IdentityRoster(candidates=[
                IdentityCandidate(
                    name="Arista Networks, Inc.", official_domain="arista.com",
                    website="https://www.arista.com", confidence=0.97,
                    rationale="official corporate homepage",
                ),
            ])

        def deliberate(self, *a, **k):
            raise RuntimeError("strategy unused")

    monkeypatch.setattr(
        "agents.decisions.engine.research_engine", lambda: Engine())
    monkeypatch.setattr(
        "agents.decisions.engine.DecisionEngine", lambda *a, **k: Engine())
    def _js_shell(url, max_pages=5, **_k):
        return ScrapeBundle(
            root_url=url,
            pages=[ScrapedPage(
                url=url,
                text="Error loading the page. Enable JavaScript.",
            )],
            sources=[url],
        )

    monkeypatch.setattr("tools.scrape.site.scrape_site", _js_shell)
    monkeypatch.setattr("agents.company_research.scrape_site", _js_shell)
    monkeypatch.setattr(
        "agents.intake.pipeline._ingest_bound_site",
        lambda *a, **k: {"capabilities": [], "receipt": {"error": "load"}},
    )

    def fake_strategy(submission, **kwargs):
        return IntakeStrategy(
            client_name=submission.client_name,
            pursuit_strategy="Pursue switching awards from invented codes.",
            inferred_naics=[
                "334210", "334220", "513210", "423430", "541519", "541512",
            ],
            confidence=0.6,
            review_gate="review invented NAICS before treating them as evidenced",
        )

    monkeypatch.setattr("agents.decisions.intake.build_strategy", fake_strategy)

    result = run_step1(
        submission_from_client_name("Arista Networks"),
        do_scrape=True, do_web=True, auto_approve=False,
        review_dir=str(tmp_path / "review"), alert_fn=lambda *a: None,
    )
    texts = {o.text for o in result.dossier.offerings}
    assert "47QSWA18D008F" not in texts
    assert "0119Y" not in texts
    assert {"EOS", "CloudVision"} <= texts
    assert result.dossier.naics == []
    assert result.adversarial.passed is False
    assert any(
        c.kind == "naics_overbreadth" and c.severity == "block"
        for c in result.adversarial.challenges)
    by_id = {row["id"]: row for row in result.readiness["receipts"]}
    assert by_id["E5"]["ok"] is False
    assert by_id["E8"]["ok"] is False
    assert result.readiness["all_ok"] is False


# ---- Press 6: NAICS quality for downstream search / lead-gen --------------- #

_PRESS6_FEDERAL = (
    "Arista Networks states NAICS 334118 for computer terminal equipment "
    "and NAICS 541519 for other computer related services. "
    "Forecast 685031 is a pipeline id, not an industry code. "
    "Arista Aviation listings use NAICS 336413 and 488190."
)


def test_forecast_id_is_not_a_plausible_naics():
    assert is_plausible_naics_code("685031") is False
    assert is_plausible_naics_code("334118") is True
    assert is_plausible_naics_code("336413") is True
    rows = extract_naics(_PRESS6_FEDERAL)
    codes = {c for c, _why, _role in rows}
    assert "685031" not in codes
    assert {"334118", "541519"} <= codes
    aviation = {c: role for c, _why, role in rows if c in {"336413", "488190"}}
    assert aviation
    assert all(role == "boundary" for role in aviation.values())


def test_press6_aviation_naics_and_forecast_id_are_not_core():
    ident = _arista_identity()
    probes = [
        ResearchProbe(
            name="offerings", query="products",
            findings=(
                "Official pages name EOS, CloudVision AGNI (Guardian for "
                "Network Identity), the 7050X series, and DANZ Monitoring "
                "Fabric."
            ),
            citations=["https://www.arista.com/en/products"],
        ),
        ResearchProbe(
            name="federal_footprint", query="federal",
            findings=_PRESS6_FEDERAL,
            citations=["https://sam.gov"],
        ),
        ResearchProbe(
            name="boundaries", query="exclusions",
            findings=(
                "Do not confuse Arista Networks with Arista Aviation "
                "Services or Arista Records. No OAS listing appears here."
            ),
            citations=["https://en.wikipedia.org/wiki/Arista_Records"],
        ),
    ]
    dossier = build_dossier(
        client_name="Arista Networks",
        identity=ident,
        research=_arista_research(),
        probes=probes,
    )
    texts = {o.text for o in dossier.offerings}
    assert "DANZ Monitoring Fabric" in texts
    assert "AGNI" in texts and "7050X" in texts
    cores = {n.code for n in dossier.naics if n.role == "core"}
    all_codes = {n.code for n in dossier.naics}
    parked = {n.code for n in dossier.kept_out_naics}
    assert "685031" not in all_codes
    assert "685031" not in parked
    assert "336413" not in all_codes
    assert "488190" not in all_codes
    assert "336413" not in cores
    assert "488190" not in cores
    assert {"336413", "488190"} <= parked
    assert {"334118", "541519"} <= cores
    kept = {k.term.casefold() for k in dossier.kept_out}
    assert any("aviation" in k for k in kept)
    assert any("records" in k for k in kept)
    assert not any("oas" in k for k in kept)
    for row in dossier.kept_out:
        assert row.evidence_ids
        excerpts = [
            e.excerpt or "" for e in dossier.evidence
            if e.evidence_id in row.evidence_ids
        ]
        assert any(excerpt_supports_name(row.term, ex) for ex in excerpts)

    strategy = strategy_from_dossier(dossier)
    assert "336413" not in strategy.inferred_naics
    assert "488190" not in strategy.inferred_naics
    assert {"336413", "488190"} <= {e.code for e in strategy.kept_out_naics}
    assert {"334118", "541519"} <= set(strategy.inferred_naics)
    wiped = strategy.model_copy(update={"kept_out_naics": []})
    restored = apply_kept_out(wiped, dossier)
    assert {"336413", "488190"} <= {e.code for e in restored.kept_out_naics}
    rec = challenge_dossier(dossier, ran=True, strategy=strategy)
    assert rec.passed is True
    ready = evaluate_readiness(
        identity=ident, dossier=dossier, probes=probes,
        yield_receipt={
            "store": {"status": "empty", "row_count": 0, "note": "empty"},
            "terms": [],
            "note": "notice store has zero rows; counts are not a market zero",
        },
        adversarial=rec, strategy=strategy,
    )
    by_id = {row["id"]: row for row in ready["receipts"]}
    assert by_id["E5"]["ok"] is True
    assert by_id["E8"]["ok"] is True


def test_press6_polluted_core_naics_fail_e5_e8():
    """Aviation cores while Aviation is kept_out must not green E5/E8."""
    ident = _arista_identity()
    dossier = build_dossier(
        client_name="Arista Networks",
        identity=ident,
        research=_arista_research(),
        probes=_arista_probes(),
    )
    dossier.kept_out.append(DossierKeyword(
        term="Arista Aviation", category="exclusion",
        rationale="namesake aviation firm",
        evidence_ids=[],
    ))
    dossier.naics.append(DossierNaics(
        code="336413", role="core",
        rationale="aviation parts manufacturing leaked into core search",
        state=ClaimState.INFERRED,
        evidence_ids=["E999"],
    ))
    strategy = {
        "inferred_naics": [n.code for n in dossier.naics if n.role == "core"],
        "near_misses": [
            {"value": "336413", "kind": "naics"},
            {"value": "488190", "kind": "naics"},
        ],
    }
    rec = challenge_dossier(dossier, ran=True, strategy=strategy)
    assert any(
        c.kind == "naics_overbreadth" and c.severity == "block"
        and "336413" in c.statement
        for c in rec.challenges)
    assert rec.passed is False
    ready = evaluate_readiness(
        identity=ident, dossier=dossier,
        yield_receipt={
            "store": {"status": "empty", "row_count": 0, "note": "empty"},
            "terms": [],
            "note": "notice store has zero rows; counts are not a market zero",
        },
        adversarial=rec, strategy=strategy,
    )
    by_id = {row["id"]: row for row in ready["receipts"]}
    assert by_id["E5"]["ok"] is False
    assert by_id["E8"]["ok"] is False
    assert ready["all_ok"] is False


def test_press16_334210_near_miss_is_not_aviation_conflict():
    """334210 core + Aviation kept_out must not fail E5; aviation stays parked."""
    ident = _arista_identity()
    probes = [
        ResearchProbe(
            name="offerings", query="products",
            findings=(
                "Official pages name EOS, CloudVision AGNI, the 7050X "
                "series, and DANZ Monitoring Fabric."
            ),
            citations=["https://www.arista.com/en/products"],
        ),
        ResearchProbe(
            name="federal_footprint", query="federal",
            findings=(
                "The SAM listing states NAICS 334118 for computer terminal "
                "equipment and NAICS 334210 for telephone apparatus "
                "manufacturing, plus 541519 for other computer related "
                "services."
            ),
            citations=["https://sam.gov"],
        ),
        ResearchProbe(
            name="boundaries", query="exclusions",
            findings=(
                "Do not confuse Arista Networks with Arista Aviation. "
                "Aviation stays out of the search lane."
            ),
            citations=["https://en.wikipedia.org/wiki/Arista_Aviation"],
        ),
    ]
    dossier = build_dossier(
        client_name="Arista Networks",
        identity=ident,
        research=_arista_research(),
        probes=probes,
    )
    cores = {n.code for n in dossier.naics if n.role == "core"}
    parked = {n.code for n in dossier.kept_out_naics}
    assert "334210" in cores
    assert {"336413", "488190"} <= parked
    assert "336413" not in cores
    strategy = {
        "inferred_naics": [n.code for n in dossier.naics if n.role == "core"],
        "near_misses": [
            {"value": "334210", "kind": "naics"},
            {"value": "336413", "kind": "naics"},
            {"value": "488190", "kind": "naics"},
        ],
        "kept_out_naics": [
            {"code": "336413", "role": "boundary"},
            {"code": "488190", "role": "boundary"},
        ],
    }
    assert conflicting_core_naics(dossier, strategy) == []
    rec = challenge_dossier(dossier, ran=True, strategy=strategy)
    assert rec.passed is True
    ready = evaluate_readiness(
        identity=ident, dossier=dossier, probes=probes,
        yield_receipt={
            "store": {"status": "empty", "row_count": 0, "note": "empty"},
            "terms": [],
            "note": "notice store has zero rows; counts are not a market zero",
        },
        adversarial=rec, strategy=strategy,
    )
    by_id = {row["id"]: row for row in ready["receipts"]}
    assert by_id["E5"]["ok"] is True
    assert by_id["E8"]["ok"] is True
    assert {"336413", "488190"} <= {n.code for n in dossier.kept_out_naics}


def test_danz_named_offering_not_visibility_telemetry_umbrella():
    ident = _arista_identity()
    probes = [
        ResearchProbe(
            name="offerings", query="products",
            findings=(
                "## Visibility\n"
                "## Telemetry\n"
                "Network Visibility and Telemetry fabrics include "
                "DANZ Monitoring Fabric next to EOS and CloudVision.\n"
            ),
            citations=["https://www.arista.com/en/products/danz"],
        ),
    ]
    dossier = build_dossier(
        client_name="Arista Networks",
        identity=ident,
        research=_arista_research(),
        probes=probes,
    )
    texts = {o.text for o in dossier.offerings}
    low = {t.casefold() for t in texts}
    assert "DANZ Monitoring Fabric" in texts
    assert "visibility" not in low
    assert "telemetry" not in low
    assert "network visibility" not in low


def test_aristan_style_prompt_echo_is_not_invented():
    ident = _arista_identity()
    probes = [
        ResearchProbe(
            name="boundaries", query="exclusions",
            findings=(
                "Search for a lookalike spelling or an Aristan-style "
                "homophone. None of those firms appear in the cited pages. "
                "Arista Records is a separate music label."
            ),
            citations=["https://en.wikipedia.org/wiki/Arista_Records"],
        ),
    ]
    dossier = build_dossier(
        client_name="Arista Networks",
        identity=ident,
        research=_arista_research(),
        probes=probes,
    )
    kept = {k.term.casefold() for k in dossier.kept_out}
    assert any("records" in k for k in kept)
    assert not any("aristan" in k for k in kept)
