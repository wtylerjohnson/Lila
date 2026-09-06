"""Step 1 orchestrator: name-only mastery through the review gate.

Order is load-bearing:
  1. identity resolution
  2. identity-bound website ingest + structured probes
  3. dossier
  4. yield sidecar
  5. adversarial (fail-closed)
  6. adapter -> IntakeStrategy
  7. persist sidecars + request_approval
  8. optional auto-passthrough through decide()

Nothing in this module launches run_searches, outreach, or invents
scope.preset.
"""

from __future__ import annotations

import os
from typing import Optional

from pydantic import BaseModel, Field

from agents.intake import AUTO_APPROVE_TOGGLE
from agents.intake.adapters import (
    apply_kept_out,
    apply_site_honesty,
    merge_strategy_into_dossier,
    retrieval_frame,
    strategy_from_dossier,
)
from agents.intake.adversarial import AdversarialRecord, run_adversarial
from agents.intake.dossier import CompanyDossier, build_dossier
from agents.intake.extract import site_has_usable_text, structure_product_surface
from agents.intake.identity import IdentityResolution, resolve_identity
from agents.intake.probes import ResearchProbe, run_structured_probes
from tools.scrape.site import SITE_MASTERY_MAX_PAGES, official_hub_hints
from agents.intake.readiness import evaluate_readiness, render_readiness_markdown
from agents.intake.yield_sidecar import intake_yield_receipt
from agents.schemas import IntakeSubmission
from tools.toggles import is_enabled


class Step1Result(BaseModel):
    client_name: str
    status: str
    identity: IdentityResolution
    auto_approved: bool = False
    dossier: Optional[CompanyDossier] = None
    yield_receipt: dict = Field(default_factory=dict)
    adversarial: Optional[AdversarialRecord] = None
    readiness: dict = Field(default_factory=dict)
    retrieval: dict = Field(default_factory=dict)
    sidecar_paths: dict = Field(default_factory=dict)
    errors: list[str] = Field(default_factory=list)
    exit_code: int = 0


def intake_auto_approve_enabled() -> bool:
    """Default ON. Set LILA_ENABLE_INTAKE_AUTO_APPROVE=off to restore the click."""
    return is_enabled(AUTO_APPROVE_TOGGLE, True)


def _slug(name: str) -> str:
    from tools.slug import client_slug
    return client_slug(name)


def persist_sidecars(
    client_name: str,
    *,
    dossier: CompanyDossier,
    yield_receipt: dict,
    adversarial: AdversarialRecord,
    readiness: dict,
    retrieval: dict,
    review_dir: Optional[str] = None,
) -> dict:
    import agents.review as review
    from tools.artifacts import atomic_write_json

    root = review_dir or review.REVIEW_DIR
    os.makedirs(root, exist_ok=True)
    stem = os.path.join(root, _slug(client_name))
    paths = {
        "dossier": stem + ".dossier.json",
        "intake_yield": stem + ".intake_yield.json",
        "intake_adversarial": stem + ".intake_adversarial.json",
        "intake_readiness": stem + ".intake_readiness.json",
        "intake_retrieval": stem + ".intake_retrieval.json",
    }
    atomic_write_json(paths["dossier"], dossier.model_dump(mode="json"))
    atomic_write_json(paths["intake_yield"], yield_receipt)
    atomic_write_json(paths["intake_adversarial"],
                      adversarial.model_dump(mode="json"))
    atomic_write_json(paths["intake_readiness"], readiness)
    atomic_write_json(paths["intake_retrieval"], retrieval)
    return paths


def _append_mastery_markdown(
    client_name: str,
    *,
    identity: IdentityResolution,
    readiness: dict,
    yield_receipt: dict,
    adversarial: AdversarialRecord,
    review_dir: Optional[str] = None,
) -> None:
    import agents.review as review

    root = review_dir or review.REVIEW_DIR
    md_path = os.path.join(root, _slug(client_name) + ".review.md")
    blocks = ["", "## Company identity", ""]
    blocks.append(f"- Query name: {identity.query_name}")
    blocks.append(f"- Status: {identity.status}")
    if identity.official_domain:
        blocks.append(f"- Official domain: {identity.official_domain}")
    if identity.question:
        blocks.append(f"- Question: {identity.question}")
    blocks.append(f"- Rationale: {identity.rationale}")
    blocks.append("")
    blocks.append(render_readiness_markdown(readiness))
    store = (yield_receipt or {}).get("store") or {}
    blocks.append("## Intake yield")
    blocks.append("")
    blocks.append(f"- Store: {store.get('status', 'unknown')} "
                  f"({store.get('row_count', 0)} rows)")
    blocks.append(f"- {(yield_receipt or {}).get('note', '')}")
    for row in (yield_receipt or {}).get("terms") or []:
        titles = "; ".join(row.get("sample_titles") or []) or "(no titles)"
        blocks.append(
            f"- {row.get('term')}: {row.get('count')} · {titles}"
        )
    if store.get("status") != "ready":
        blocks.append("- Honest empty/unreadable store: not a market zero.")
    blocks.append("")
    blocks.append("## Intake adversarial")
    blocks.append("")
    blocks.append(
        f"- complete={adversarial.complete} passed={adversarial.passed} "
        f"· {adversarial.note}"
    )
    for ch in adversarial.challenges:
        blocks.append(f"- [{ch.severity}/{ch.kind}] {ch.statement}")
    blocks.append("")
    blocks.append(
        "Approval (human or auto-passthrough) still does not launch searches "
        "and does not invent scope.preset."
    )
    blocks.append("")
    text = "\n".join(blocks)
    try:
        existing = open(md_path, encoding="utf-8").read() if os.path.exists(md_path) else ""
        if "## Intake readiness (E1-E8)" not in existing:
            with open(md_path, "a", encoding="utf-8") as fh:
                fh.write(text)
    except OSError:
        pass


def _default_research_engine():
    """Fast research engine (web search / identity). Callers may inject a fake."""
    from agents.decisions.engine import research_engine as make_research_engine
    return make_research_engine()


def _default_strategy_engine():
    """Judgment engine for strategy composition. Callers may inject a fake."""
    from agents.decisions.engine import DecisionEngine
    return DecisionEngine()


def _ingest_bound_site(website: str, *, fetcher=None, max_pages: int = 20) -> dict:
    try:
        from tools.capability_ingest import ingest
        return ingest(website, max_pages=max_pages, fetcher=fetcher)
    except Exception as exc:  # noqa: BLE001
        return {"capabilities": [], "receipt": {"error": str(exc)}}


def run_step1(
    submission: IntakeSubmission,
    *,
    engine=None,
    research_engine=None,
    max_pages: int = SITE_MASTERY_MAX_PAGES,
    do_scrape: bool = True,
    do_web: bool = True,
    auto_approve: Optional[bool] = None,
    review_dir: Optional[str] = None,
    yield_conn=None,
    ingest_fetcher=None,
    scrape_fn=None,
    alert_fn=None,
    sec_customers=None,
    sec_fetch=None,
) -> Step1Result:
    """Run the mastery pipeline to a review packet. Never starts a sweep."""
    import agents.review as review
    from agents.company_research import research_company
    from agents.decisions.intake import build_strategy

    name = submission.client_name
    form_website = str(submission.website) if submission.website else None
    # Name-only intake must not silently take the offline-abstain path just
    # because a caller forgot to pass engines (Arista press NO-GO, 2026-09-06).
    # Construct the same defaults research_company / build_strategy already
    # use. --no-websearch (do_web False) still skips the live identity search.
    if do_web and research_engine is None:
        research_engine = _default_research_engine()
    if engine is None:
        engine = _default_strategy_engine()
    identity_engine = research_engine if do_web else None
    identity = resolve_identity(
        name, website=form_website, engine=identity_engine)

    errors = list(identity.errors)
    research = None
    probes: list[ResearchProbe] = []
    product_ingest: dict = {"capabilities": [], "receipt": {}}

    page_budget = max(int(max_pages or 0), SITE_MASTERY_MAX_PAGES)
    if identity.is_bound and (do_scrape or do_web):
        bound_url = identity.website
        research = research_company(
            name,
            website=bound_url,
            engine=research_engine,
            max_pages=page_budget,
            do_scrape=do_scrape,
            do_web=False,  # structured probes replace the one-blob worker
        )
        if scrape_fn is not None and do_scrape and bound_url:
            try:
                research.scrape = scrape_fn(bound_url, max_pages=page_budget)
            except Exception as exc:  # noqa: BLE001
                errors.append(f"scrape: {exc}")
        if do_scrape and bound_url:
            product_ingest = _ingest_bound_site(
                bound_url, fetcher=ingest_fetcher,
                max_pages=page_budget)
        if do_web and research_engine is not None:
            scrape = getattr(research, "scrape", None)
            probes = run_structured_probes(
                identity, research_engine,
                official_urls=official_hub_hints(bound_url, scrape),
                site_thin=not site_has_usable_text(scrape),
            )
            citations = []
            findings = []
            for p in probes:
                findings.append(f"[{p.name}] {p.findings}")
                citations.extend(p.citations)
                if p.error:
                    errors.append(f"probe {p.name}: {p.error}")
            research.web_findings = "\n\n".join(findings)
            research.web_citations = list(dict.fromkeys(citations))
        if research is not None:
            errors.extend(research.errors)

    product_surface = None
    if identity.is_bound and do_web and research_engine is not None and probes:
        product_surface = structure_product_surface(
            research_engine, probes, identity=identity)

    in_pytest = bool(os.environ.get("PYTEST_CURRENT_TEST"))
    if (
        sec_customers is None
        and identity.is_bound
        and do_web
        and (sec_fetch is not None or not in_pytest)
    ):
        try:
            from agents.intake.sec_customers import fetch_bound_sec_customers
            fetcher = sec_fetch or fetch_bound_sec_customers
            sec_customers = fetcher(identity.bound_name or name, client_name=name)
        except Exception as exc:  # noqa: BLE001 - live SEC is optional
            errors.append(f"sec roster: {exc}")
            sec_customers = []

    dossier = build_dossier(
        client_name=name,
        identity=identity,
        submission=submission,
        research=research,
        product_ingest=product_ingest,
        probes=probes,
        product_surface=product_surface,
        sec_customers=sec_customers or [],
    )

    phrases = [k.term for k in dossier.keywords] + [
        u.offering for u in dossier.capability_statements if u.offering]
    yield_receipt = intake_yield_receipt(
        phrases, conn=yield_conn, client_name=name)

    dossier, adversarial = run_adversarial(
        dossier,
        yield_receipt=yield_receipt,
        product_ingest=product_ingest,
        engine=engine,
    )

    readiness = evaluate_readiness(
        identity=identity,
        dossier=dossier,
        research=research,
        probes=probes,
        yield_receipt=yield_receipt,
        adversarial=adversarial,
    )
    retrieval = retrieval_frame(dossier)

    strategy = strategy_from_dossier(dossier, submission)
    if identity.is_bound and engine is None:
        engine = _default_strategy_engine()
    if identity.is_bound and engine is not None:
        try:
            strategy = build_strategy(
                submission,
                scrape=getattr(research, "scrape", None),
                engine=engine,
                research=research,
            )
            # Adapter-grounded retrieval units stay on the dossier; the
            # generated strategy remains the reviewable search plan.
        except Exception as exc:  # noqa: BLE001
            errors.append(f"strategy composer: {exc}")
            strategy = strategy_from_dossier(dossier, submission)

    strategy = apply_kept_out(strategy, dossier)
    strategy = apply_site_honesty(strategy, identity, research)
    enriched = merge_strategy_into_dossier(dossier, strategy)
    if enriched is not dossier:
        dossier = enriched
    # Always re-score after the strategy composer. A no-op merge must not
    # leave E5/E8 green when strategy invented NAICS the dossier cannot
    # evidence.
    dossier, adversarial = run_adversarial(
        dossier,
        yield_receipt=yield_receipt,
        product_ingest=product_ingest,
        engine=None,
        strategy=strategy,
    )
    retrieval = retrieval_frame(dossier)
    readiness = evaluate_readiness(
        identity=identity,
        dossier=dossier,
        research=research,
        probes=probes,
        yield_receipt=yield_receipt,
        adversarial=adversarial,
        strategy=strategy,
    )

    if review_dir is not None:
        review.REVIEW_DIR = review_dir

    def _alert(packet, md_path):
        if alert_fn is not None:
            alert_fn(packet, md_path)

    packet = review.request_approval(strategy, alert_fn=_alert)
    paths = persist_sidecars(
        name, dossier=dossier, yield_receipt=yield_receipt,
        adversarial=adversarial, readiness=readiness, retrieval=retrieval,
        review_dir=review.REVIEW_DIR,
    )
    _append_mastery_markdown(
        name, identity=identity, readiness=readiness,
        yield_receipt=yield_receipt, adversarial=adversarial,
        review_dir=review.REVIEW_DIR,
    )

    should_auto = auto_approve if auto_approve is not None else intake_auto_approve_enabled()
    packet_after = review.maybe_auto_approve(
        name, allowed=bool(should_auto and identity.is_bound),
        review_dir=review.REVIEW_DIR,
    )
    auto_approved = bool(packet_after and packet_after.status.value == "approved")
    status = (packet_after or packet).status.value
    if not identity.is_bound:
        status = identity.status

    exit_code = 0
    if identity.status == "blocked":
        exit_code = 2
    elif identity.status == "abstain":
        exit_code = 0  # artifacts persisted; operator can answer and retry

    return Step1Result(
        client_name=name,
        status=status,
        identity=identity,
        auto_approved=auto_approved,
        dossier=dossier,
        yield_receipt=yield_receipt,
        adversarial=adversarial,
        readiness=readiness,
        retrieval=retrieval,
        sidecar_paths=paths,
        errors=errors,
        exit_code=exit_code,
    )


def submission_from_client_name(
    client_name: str,
    *,
    website: Optional[str] = None,
) -> IntakeSubmission:
    """Name-only submission. Optional website is enrichment, not required."""
    payload = {"client_name": client_name.strip()}
    if website:
        payload["website"] = website
    return IntakeSubmission.model_validate(payload)
