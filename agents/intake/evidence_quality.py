"""Conservative claim hygiene; rejected interpretations remain in the dossier."""
import re


def customer_context_problem(name: str, excerpt: str) -> str:
    if re.search(r"\b(phishing|ransomware|malicious|attack|attacks)\b", name, re.IGNORECASE):
        return "Threat case-study title is not a named customer organization"
    if re.search(re.escape(name) + r"\s+(?:Office|Windows|browser|add.ins|plug.ins)\b", excerpt, re.IGNORECASE):
        return "Product mentioned in an incident does not establish the vendor as a customer"
    return ""


def review_claim_evidence(dossier):
    """Remove unsafe claims from active facts; retain exact evidence and reason."""
    from agents.intake.dossier import ClaimState, DossierKeyword
    evidence = {e.evidence_id: e for e in dossier.evidence}
    for field in ("customers", "competitors", "offerings"):
        kept = []
        for claim in getattr(dossier, field):
            refs = [evidence[eid] for eid in claim.evidence_ids if eid in evidence]
            reason = ""
            if field == "customers":
                problems = [customer_context_problem(claim.text, e.excerpt) for e in refs]
                if problems and all(problems):
                    reason = problems[0]
            if field == "competitors" and refs and all(
                re.search(r"\*\*.*(?:primary named rival|named rival in)|official customer proof on this", e.excerpt, re.IGNORECASE)
                for e in refs
            ):
                reason = "Synthetic research summary is not a passage from the cited comparison page"
            if field == "offerings" and re.search(r"\bOffice\s+add.ins?\b", claim.text, re.IGNORECASE):
                reason = "Incident software mention does not establish a client offering"
            if reason:
                dossier.kept_out.append(DossierKeyword(
                    term=claim.text, category=field, rationale=reason,
                    state=ClaimState.DISPUTED, evidence_ids=claim.evidence_ids))
                dossier.unknowns.append(f"{field}: {claim.text}: {reason}")
            else:
                kept.append(claim)
        setattr(dossier, field, kept)
    return dossier
