"""Intake adversarial pass: pressure-test the dossier against evidence.

Argues against citations, yield titles, and the evidence ledger, never
against the dossier's own prose as if it were a source. Incomplete review
is not a pass. One bounded repair pass may drop unsupported claims; it
cannot invent new capabilities.
"""

from __future__ import annotations

from typing import Literal, Optional

from pydantic import BaseModel, Field

from agents.intake.dossier import ClaimState, CompanyDossier
from agents.intake.identity import IdentityResolution


class Challenge(BaseModel):
    kind: Literal[
        "identity", "unsupported_capability", "polysemy",
        "naics_overbreadth", "missing_offering", "incomplete",
    ]
    severity: Literal["block", "warn"]
    statement: str
    evidence_ids: list[str] = Field(default_factory=list)
    external_refs: list[str] = Field(default_factory=list)


class AdversarialRecord(BaseModel):
    schema_version: str = "intake_adversarial.v1"
    client_name: str
    complete: bool = False
    passed: bool = False
    challenges: list[Challenge] = Field(default_factory=list)
    repairs: list[str] = Field(default_factory=list)
    note: str = ""


_COMMON_NAME_TOKENS = frozenset({
    "united", "american", "national", "federal", "general", "pacific",
    "atlantic", "metro", "city", "state", "first", "standard", "apex",
    "pioneer", "summit", "horizon", "atlas", "vertex", "delta", "alpha",
    "omega", "next", "core", "prime", "global", "digital", "data",
    "network", "systems", "solutions", "services", "group", "partners",
})


def _yield_titles(yield_receipt: Optional[dict]) -> list[str]:
    titles: list[str] = []
    for row in (yield_receipt or {}).get("terms") or []:
        if not isinstance(row, dict):
            continue
        for title in row.get("sample_titles") or []:
            if title and title not in titles:
                titles.append(str(title))
    return titles


def _citations(dossier: CompanyDossier, yield_receipt: Optional[dict]) -> list[str]:
    refs = [e.url for e in dossier.evidence if e.url]
    refs.extend(_yield_titles(yield_receipt))
    store = (yield_receipt or {}).get("store") or {}
    if store.get("status") and store.get("status") != "ready":
        refs.append(f"store:{store.get('status')}")
    return [r for r in refs if r]


def challenge_dossier(
    dossier: CompanyDossier,
    *,
    yield_receipt: Optional[dict] = None,
    product_ingest: Optional[dict] = None,
    ran: bool = True,
) -> AdversarialRecord:
    """Structural, fail-closed challenge. No model required."""
    rec = AdversarialRecord(
        client_name=dossier.client_name, complete=bool(ran), passed=False)
    if not ran:
        rec.challenges.append(Challenge(
            kind="incomplete", severity="block",
            statement="adversarial review did not run; incomplete review is not a pass",
        ))
        rec.note = "incomplete"
        return rec

    identity: IdentityResolution = dossier.identity
    if not identity.is_bound:
        rec.challenges.append(Challenge(
            kind="identity", severity="block",
            statement=(
                identity.question
                or "identity is not bound; refusing to treat the name as the company"
            ),
            external_refs=[c.official_domain or c.website or c.name
                           for c in identity.candidates[:4]],
        ))

    ev_ids = {e.evidence_id for e in dossier.evidence}
    cited = _citations(dossier, yield_receipt)

    for off in dossier.offerings:
        missing = [i for i in off.evidence_ids if i not in ev_ids]
        if off.state in (ClaimState.CORROBORATED, ClaimState.COMPANY_ASSERTED):
            if not off.evidence_ids or missing:
                rec.challenges.append(Challenge(
                    kind="unsupported_capability", severity="block",
                    statement=(
                        f"offering {off.text[:80]!r} is {off.state.value} "
                        "without ledger evidence"
                    ),
                    evidence_ids=list(off.evidence_ids),
                ))
        if off.state == ClaimState.INFERRED and not cited:
            rec.challenges.append(Challenge(
                kind="unsupported_capability", severity="warn",
                statement=(
                    f"offering {off.text[:80]!r} is inferred with no external citation"
                ),
            ))

    ingest_names = []
    for row in (product_ingest or {}).get("capabilities") or []:
        name = (row.get("name") if isinstance(row, dict) else str(row) or "").strip()
        if name:
            ingest_names.append(name)
    offering_blob = " ".join(o.text.casefold() for o in dossier.offerings)
    for name in ingest_names:
        if name.casefold() not in offering_blob:
            rec.challenges.append(Challenge(
                kind="missing_offering", severity="warn",
                statement=(
                    f"product-surface crawl named {name!r} but the dossier "
                    "offerings do not carry it"
                ),
                external_refs=[name],
            ))

    for entry in dossier.naics:
        if len((entry.rationale or "").split()) < 5:
            rec.challenges.append(Challenge(
                kind="naics_overbreadth", severity="block",
                statement=(
                    f"NAICS {entry.code} has no grounded rationale "
                    "(need a concrete why-this-client explanation)"
                ),
            ))
        if entry.role == "core" and entry.state == ClaimState.INFERRED and not entry.evidence_ids:
            rec.challenges.append(Challenge(
                kind="naics_overbreadth", severity="block",
                statement=f"NAICS {entry.code} is marked core without evidence",
            ))

    tokens = [
        t for t in (identity.query_name or "").casefold().split()
        if t not in {"inc", "llc", "ltd", "corp", "co", "the", "and"}
    ]
    collision = [t for t in tokens if t in _COMMON_NAME_TOKENS]
    if collision and identity.is_bound:
        rec.challenges.append(Challenge(
            kind="polysemy", severity="warn",
            statement=(
                "company name uses common tokens "
                + ", ".join(collision)
                + "; confirm the bound domain is the intended firm and "
                "that exclusions will be operator-authored later"
            ),
            external_refs=[identity.official_domain or ""],
        ))

    store = (yield_receipt or {}).get("store") or {}
    titles = _yield_titles(yield_receipt)
    if store.get("status") == "ready" and titles:
        # Titles are the falsifier. A capability term whose only yield title
        # is an obvious collision is a polysemy challenge, not a health grade.
        for row in (yield_receipt or {}).get("terms") or []:
            term = str((row or {}).get("term") or "")
            samples = [str(t).casefold() for t in (row or {}).get("sample_titles") or []]
            if term and samples and all(term.casefold() not in s for s in samples):
                rec.challenges.append(Challenge(
                    kind="polysemy", severity="warn",
                    statement=(
                        f"yield titles for {term!r} do not contain the term; "
                        "inspect the receipt before treating the count as demand"
                    ),
                    external_refs=list((row or {}).get("sample_titles") or [])[:3],
                ))

    blocking = [c for c in rec.challenges if c.severity == "block"]
    rec.passed = rec.complete and not blocking
    rec.note = (
        "passed" if rec.passed
        else ("blocked" if blocking else "complete_with_warnings")
    )
    return rec


def bounded_repair(
    dossier: CompanyDossier,
    record: AdversarialRecord,
) -> tuple[CompanyDossier, AdversarialRecord]:
    """One repair pass: drop unsupported asserted/corroborated offerings.

    Does not invent replacements. Re-runs the structural challenge.
    """
    drop_texts = {
        c.statement for c in record.challenges
        if c.kind == "unsupported_capability" and c.severity == "block"
    }
    if not drop_texts:
        return dossier, record

    kept = []
    removed = []
    for off in dossier.offerings:
        needle = f"offering {off.text[:80]!r}"
        if any(needle in stmt for stmt in drop_texts):
            removed.append(off.text)
            continue
        kept.append(off)
    if not removed:
        return dossier, record

    repaired = dossier.model_copy(deep=True)
    repaired.offerings = kept
    for name in removed:
        repaired.unknowns.append(
            f"removed unsupported offering during bounded repair: {name[:80]}"
        )
    repaired.unknowns.append(
        "bounded repair dropped unsupported capabilities; it did not invent replacements"
    )
    second = challenge_dossier(repaired, ran=True)
    second.repairs = [
        f"dropped unsupported offering: {name[:80]}" for name in removed
    ]
    return repaired, second


def run_adversarial(
    dossier: CompanyDossier,
    *,
    yield_receipt: Optional[dict] = None,
    product_ingest: Optional[dict] = None,
    engine=None,
) -> tuple[CompanyDossier, AdversarialRecord]:
    """Structural challenge plus optional model pressure-test.

    A model failure or skip cannot mint a pass: the structural record is
    the floor. If the model does not return, complete stays whatever the
    structural pass already set; we never mark complete=False after a
    successful structural run just because the LLM was absent.
    """
    first = challenge_dossier(
        dossier, yield_receipt=yield_receipt,
        product_ingest=product_ingest, ran=True)
    dossier, record = bounded_repair(dossier, first)

    if engine is None:
        return dossier, record

    try:
        from pydantic import BaseModel as _BM

        class _ModelChallenge(_BM):
            challenges: list[Challenge] = Field(default_factory=list)
            note: str = ""

        context = {
            "identity": dossier.identity.model_dump(mode="json"),
            "evidence": [e.model_dump(mode="json") for e in dossier.evidence],
            "yield_titles": _yield_titles(yield_receipt),
            "citations": _citations(dossier, yield_receipt),
            "offerings": [o.model_dump(mode="json") for o in dossier.offerings],
            "naics": [n.model_dump(mode="json") for n in dossier.naics],
        }
        extra = engine.deliberate(
            layer="intake-adversarial",
            system_prompt=(
                "You are an adversarial reviewer of a company dossier. "
                "Argue ONLY against the evidence ledger, citation URLs, and "
                "yield titles. Do not treat the dossier summary as evidence. "
                "Challenge wrong identity, unsupported capabilities, name "
                "polysemy, NAICS overbreadth, and offerings named on the "
                "site but missing from the model. Return structured challenges. "
                "If evidence is thin, say incomplete rather than passing."
            ),
            context=context,
            schema=_ModelChallenge,
        )
        for ch in extra.challenges:
            record.challenges.append(ch)
        if extra.note:
            record.note = (record.note + "; model: " + extra.note).strip("; ")
        blocking = [c for c in record.challenges if c.severity == "block"]
        record.passed = record.complete and not blocking
    except Exception as exc:  # noqa: BLE001 - model skip is not a pass upgrade
        record.challenges.append(Challenge(
            kind="incomplete", severity="warn",
            statement=f"model adversarial pass failed ({type(exc).__name__}); "
                      "structural record stands",
        ))
    return dossier, record
