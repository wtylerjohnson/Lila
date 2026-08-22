"""Step 2 — consolidate the 3 search outputs into a ranked, verified, qualified list.

Takes the searches_<client>.json produced by run_searches.py and:
  1. NORMALIZE  — dedupe authoritative SAM.gov notices into the live candidate set.
                    Web leads remain in the sweep for developing-intelligence use.
  2. VERIFY     — SAM notices via deterministic field checks.
  3. CORROBORATE — attach USAspending market evidence by NAICS.
  4. FIT (LLM)  — generate a 'why this fits' rationale per top candidate (reuses agents.decisions.fit).
  5. REVIEW     — write a Step-2 deliverable + alert for human review.

USAspending results are market evidence, not candidates, so they corroborate rather
than compete. The deterministic stages (1-3) are unit-tested offline; the LLM fit step
reuses the already-tested fit layer.
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from typing import Optional

from pydantic import BaseModel, Field

from agents.decisions.engine import DecisionEngine
from agents.decisions.fit import assess_fit
from agents.review import REVIEW_DIR, _slug
from agents.schemas import CapabilityProfile, RawOpportunity
from tools.api.verification import verify_opportunity
from tools.scrape.site import check_url

_CLEANED = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data", "cleaned")


class CandidateRecord(BaseModel):
    opportunity: RawOpportunity
    verified: bool
    verify_detail: str = ""
    market_evidence: Optional[dict] = None
    fit_rationale: Optional[dict] = None


class Step2Report(BaseModel):
    client_name: str
    candidate_count: int
    verified_count: int
    candidates: list[CandidateRecord] = Field(default_factory=list)
    generated_at: Optional[datetime] = None


def profile_from_strategy(client_name: str, strategy) -> CapabilityProfile:
    """Derive a CapabilityProfile from the approved intake strategy (drives fit/scoring)."""
    tech = [k.term for k in strategy.keywords if k.category.value in ("technology", "capability")]
    return CapabilityProfile(
        client_name=client_name,
        naics_codes=strategy.inferred_naics,
        set_aside_eligibility=strategy.set_aside_angles,
        tech_stack=tech,
        past_performance_keywords=[k.term for k in strategy.keywords],
        target_agencies=strategy.target_agencies,
    )


def _load_searches(client_name: str) -> dict:
    # L19: gate-designated artifact name, resolved against _CLEANED (module
    # var, monkeypatchable) so hermetic tests keep their isolation
    d = None
    try:
        from agents.review import load_packet
        d = load_packet(client_name).designator()
    except Exception:  # noqa: BLE001 — no packet = all-agencies
        d = None
    slug = _slug(client_name)
    name = f"searches_{slug}.{d}.json" if d else f"searches_{slug}.json"
    with open(os.path.join(_CLEANED, name)) as f:
        return json.load(f)


def _candidates_from_searches(data: dict) -> list[RawOpportunity]:
    """Return only authoritative SAM records from the live-notice bucket.

    General-web results may describe forecasts, news, or recompetes. They stay
    in ``results["web"]`` for Horizon research and never enter qualification.
    The record-level source check also rejects a non-SAM row accidentally
    written into the SAM bucket.
    """
    out: list[RawOpportunity] = []
    seen: set[tuple[str, str]] = set()
    block = data.get("results", {}).get("sam.gov")
    if not isinstance(block, list):  # may be an {"error": ...} dict
        return out
    for rec in block:
        if not isinstance(rec, dict) or rec.get("source") != "sam.gov":
            continue
        opp = RawOpportunity.model_validate(rec)
        if opp.source != "sam.gov":
            continue
        key = (opp.source, opp.source_id)
        if key not in seen:
            seen.add(key)
            out.append(opp)
    return out


def _market_index(data: dict) -> dict[str, dict]:
    idx: dict[str, dict] = {}
    for ev in data.get("results", {}).get("usaspending.gov", []) or []:
        if isinstance(ev, dict) and ev.get("naics_code"):
            idx[ev["naics_code"]] = ev.get("summary", {})
    return idx


def _verify(opp: RawOpportunity) -> tuple[bool, str]:
    if opp.source == "web":
        ok, snippet = check_url(str(opp.api_url)) if opp.api_url else (False, "no url")
        return ok, ("reachable: " + snippet[:120]) if ok else "unreachable / 404"
    vr = verify_opportunity(opp)
    return vr.verified, "; ".join(vr.issues) or "ok"


def consolidate(client_name: str) -> tuple[list[CandidateRecord], dict[str, dict]]:
    """Deterministic Step-2 core: normalize + verify + attach market evidence."""
    data = _load_searches(client_name)
    market = _market_index(data)
    records: list[CandidateRecord] = []
    for opp in _candidates_from_searches(data):
        verified, detail = _verify(opp)
        records.append(
            CandidateRecord(
                opportunity=opp,
                verified=verified,
                verify_detail=detail,
                market_evidence={"summary": market[opp.naics_code]}
                if opp.naics_code and opp.naics_code in market
                else None,
            )
        )
    return records, market


def qualify(
    client_name: str,
    strategy,
    engine: Optional[DecisionEngine] = None,
    top_n: int = 5,
    now: Optional[datetime] = None,
) -> Step2Report:
    """Full Step 2: consolidate, then LLM fit-rationale on the top verified candidates."""
    engine = engine or DecisionEngine()
    profile = profile_from_strategy(client_name, strategy)
    records, _ = consolidate(client_name)

    verified = [r for r in records if r.verified]
    for rec in verified[:top_n]:
        rationale = assess_fit(
            rec.opportunity, profile, rec.market_evidence or {}, engine=engine
        )
        rec.fit_rationale = json.loads(rationale.model_dump_json())

    report = Step2Report(
        client_name=client_name,
        candidate_count=len(records),
        verified_count=len(verified),
        candidates=records,
        generated_at=now or datetime.now(timezone.utc),
    )
    os.makedirs(REVIEW_DIR, exist_ok=True)
    out = os.path.join(REVIEW_DIR, f"{_slug(client_name)}.qualify.json")
    from tools.artifacts import atomic_write_json
    atomic_write_json(out, report.model_dump(mode="json"))
    return report
