"""Name-only intake must construct research engines when callers omit them."""
from __future__ import annotations

from agents.intake.adversarial import AdversarialRecord
from agents.intake.dossier import CompanyDossier
from agents.intake.identity import IdentityResolution
from agents.intake.pipeline import run_step1
from agents.schemas import IntakeSubmission


def _bound_identity(query_name: str = "Arista Networks") -> IdentityResolution:
    return IdentityResolution(
        query_name=query_name,
        status="bound",
        bound_name="Arista Networks",
        official_domain="arista.com",
        website="https://www.arista.com",
        website_source="web_research",
        confidence=0.95,
        rationale="test bind",
        candidates=[],
        question=None,
        errors=[],
    )


def _stub_strategy(client_name: str = "Arista Networks"):
    return type(
        "S",
        (),
        {
            "client_name": client_name,
            "pursuit_strategy": "t",
            "inferred_naics": [],
            "naics_meta": [],
            "keywords": [],
            "target_agencies": [],
            "set_aside_angles": [],
            "research_entities": [],
            "near_misses": [],
            "searches": [],
            "confidence": 0.5,
            "sources_reviewed": ["t"],
            "requires_human_review": True,
            "review_gate": "t",
        },
    )()


def test_run_step1_defaults_research_engine(monkeypatch, tmp_path):
    created = {"research": 0, "decision": 0}
    seen = {"identity_engine": "missing"}

    class _FakeResearchEngine:
        pass

    class _FakeDecision:
        pass

    def fake_research_engine():
        created["research"] += 1
        return _FakeResearchEngine()

    def fake_decision_engine():
        created["decision"] += 1
        return _FakeDecision()

    def fake_resolve(query_name, *, website=None, engine=None, min_confidence=0.72):
        seen["identity_engine"] = engine
        return _bound_identity(query_name)

    monkeypatch.setattr(
        "agents.decisions.engine.research_engine", fake_research_engine
    )
    monkeypatch.setattr(
        "agents.decisions.engine.DecisionEngine", fake_decision_engine
    )
    monkeypatch.setattr("agents.intake.pipeline.resolve_identity", fake_resolve)
    monkeypatch.setattr(
        "agents.company_research.research_company",
        lambda *a, **k: type(
            "R",
            (),
            {
                "scrape": None,
                "web_findings": "",
                "web_citations": [],
                "errors": [],
            },
        )(),
    )
    monkeypatch.setattr(
        "agents.intake.pipeline.run_structured_probes", lambda *a, **k: []
    )
    monkeypatch.setattr(
        "agents.review.request_approval",
        lambda strategy, alert_fn=None: type(
            "P", (), {"status": type("S", (), {"value": "pending"})()}
        )(),
    )
    monkeypatch.setattr("agents.review.maybe_auto_approve", lambda *a, **k: None)
    monkeypatch.setattr("agents.intake.pipeline.persist_sidecars", lambda *a, **k: {})
    monkeypatch.setattr(
        "agents.intake.pipeline._append_mastery_markdown", lambda *a, **k: None
    )
    monkeypatch.setattr(
        "agents.intake.pipeline.build_dossier",
        lambda **k: CompanyDossier(
            client_name="Arista Networks",
            identity=_bound_identity(),
            summary="t",
        ),
    )
    monkeypatch.setattr(
        "agents.intake.pipeline.intake_yield_receipt",
        lambda *a, **k: {"store": {"status": "empty", "row_count": 0}},
    )
    monkeypatch.setattr(
        "agents.intake.pipeline.run_adversarial",
        lambda dossier, **k: (
            dossier,
            AdversarialRecord(
                client_name="Arista Networks", complete=True, passed=True
            ),
        ),
    )
    monkeypatch.setattr(
        "agents.intake.pipeline.evaluate_readiness",
        lambda **k: {"all_ok": False, "receipts": []},
    )
    monkeypatch.setattr("agents.intake.pipeline.retrieval_frame", lambda dossier: {})
    monkeypatch.setattr(
        "agents.intake.pipeline.strategy_from_dossier",
        lambda dossier, submission: _stub_strategy(submission.client_name),
    )
    monkeypatch.setattr(
        "agents.decisions.intake.build_strategy",
        lambda *a, **k: _stub_strategy(),
    )

    result = run_step1(
        IntakeSubmission(client_name="Arista Networks"),
        do_scrape=False,
        do_web=True,
        auto_approve=False,
        research_engine=None,
        engine=None,
        review_dir=str(tmp_path),
    )
    assert created["research"] == 1
    assert created["decision"] == 1
    assert isinstance(seen["identity_engine"], _FakeResearchEngine)
    assert result.identity.status == "bound"
    assert result.identity.official_domain == "arista.com"
