"""E1-E8 intake readiness receipts.

Visible in review. Human approval (or the explicit auto-passthrough) is a
separate act. These receipts never invent scope.preset and never launch
a search.
"""

from __future__ import annotations

from typing import Optional

from agents.intake.adversarial import AdversarialRecord
from agents.intake.dossier import CompanyDossier
from agents.intake.identity import IdentityResolution


RECEIPT_IDS = ("E1", "E2", "E3", "E4", "E5", "E6", "E7", "E8")


def _row(eid: str, title: str, ok: bool, detail: str) -> dict:
    return {
        "id": eid,
        "title": title,
        "ok": bool(ok),
        "detail": detail,
    }


def evaluate_readiness(
    *,
    identity: IdentityResolution,
    dossier: Optional[CompanyDossier],
    research=None,
    probes: Optional[list] = None,
    yield_receipt: Optional[dict] = None,
    adversarial: Optional[AdversarialRecord] = None,
) -> dict:
    """Return the E1-E8 receipt block plus an all_ok flag."""
    scrape = getattr(research, "scrape", None) if research is not None else None
    pages = list(getattr(scrape, "pages", None) or []) if scrape is not None else []
    probe_list = list(probes or [])
    cited_probes = 0
    for p in probe_list:
        citations = getattr(p, "citations", None) or (
            p.get("citations") if isinstance(p, dict) else [])
        if citations:
            cited_probes += 1
    store = (yield_receipt or {}).get("store") or {}
    terms = (yield_receipt or {}).get("terms") if yield_receipt else None
    yield_present = yield_receipt is not None
    yield_honest = bool(
        yield_present
        and store.get("status") in {"ready", "empty", "unreadable"}
        and (store.get("status") != "ready" or isinstance(terms, list))
        and not (
            store.get("status") in {"empty", "unreadable"}
            and terms
        )
    )

    e1 = _row(
        "E1", "Identity bound",
        identity.is_bound,
        identity.rationale or identity.status,
    )
    e2 = _row(
        "E2", "Identity-bound website ingested",
        bool(identity.is_bound and pages) or bool(
            identity.is_bound and identity.website_source == "not_found"),
        (
            f"{len(pages)} page(s) read from {identity.website}"
            if pages else
            ("bound domain has no reachable pages" if identity.is_bound
             else "website ingest skipped; identity not bound")
        ),
    )
    e3 = _row(
        "E3", "Structured web probes cited",
        (not identity.is_bound) or cited_probes > 0 or bool(
            research and getattr(research, "web_citations", None)),
        (
            f"{cited_probes} cited probe(s)"
            if probe_list else
            (
                f"{len(getattr(research, 'web_citations', None) or [])} web citation(s)"
                if research is not None else
                "no probes ran"
            )
        ),
    )
    e4 = _row(
        "E4", "Company model present",
        bool(dossier and (dossier.offerings or dossier.unknowns)),
        (
            f"{len(dossier.offerings)} offerings, "
            f"{len(dossier.boundaries)} boundaries, "
            f"{len(dossier.channels)} channels, "
            f"{len(dossier.unknowns)} unknowns"
            if dossier else "no dossier"
        ),
    )
    e5 = _row(
        "E5", "Retrieval representation",
        bool(dossier and (
            dossier.keywords or dossier.naics or dossier.capability_statements)),
        (
            f"{len(dossier.keywords)} keywords, "
            f"{len(dossier.naics)} NAICS, "
            f"{len(dossier.capability_statements)} capability statements"
            if dossier else "no retrieval units"
        ),
    )
    e6 = _row(
        "E6", "Yield receipts",
        yield_honest,
        (yield_receipt or {}).get("note") or "yield sidecar missing",
    )
    adv_complete = bool(adversarial and adversarial.complete)
    e7 = _row(
        "E7", "Adversarial completed",
        adv_complete,
        (
            adversarial.note if adversarial
            else "adversarial record missing; incomplete review is not a pass"
        ),
    )
    e8 = _row(
        "E8", "Adversarial not fail-closed",
        bool(adversarial and adversarial.complete and adversarial.passed),
        (
            f"{len(adversarial.challenges)} challenge(s), passed={adversarial.passed}"
            if adversarial else "no adversarial record"
        ),
    )
    receipts = [e1, e2, e3, e4, e5, e6, e7, e8]
    return {
        "schema_version": "intake_readiness.v1",
        "client_name": identity.query_name,
        "receipts": receipts,
        "all_ok": all(r["ok"] for r in receipts),
        "identity_bound": identity.is_bound,
        "note": (
            "readiness receipts are evidence, not engagement scope; "
            "scope.preset stays operator-owned"
        ),
    }


def render_readiness_markdown(readiness: dict) -> str:
    lines = ["## Intake readiness (E1-E8)", ""]
    for row in readiness.get("receipts") or []:
        mark = "PASS" if row.get("ok") else "FAIL"
        lines.append(
            f"- **{row.get('id')} {row.get('title')}** · {mark} · "
            f"{row.get('detail')}"
        )
    lines.append("")
    lines.append(readiness.get("note") or "")
    lines.append("")
    return "\n".join(lines)
