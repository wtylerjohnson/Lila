"""Join reviewed research by exact source identity, without changing lead tiers."""
from agents.assess.reviewed_cases import ReviewedCases


def case_projection(run, book: ReviewedCases, projection):
    if book.client_name.casefold() != run.client_name.casefold():
        raise ValueError("reviewed cases belong to a different client")
    from agents.assess.research_subjects import canonical
    research = {s.subject_id:s for s in run.research.items} if run.research else {}
    for overlay in book.subjects:
        subject=research.get(overlay.subject_id)
        if subject is None or subject.reviewed_overlay_json != canonical(overlay.model_dump(mode="json")):
            raise ValueError("reviewed research differs from current Assess; refresh it first")
    live = {r.notice_id: r for r in run.live.records}
    rows = list(projection.get("rows") or [])
    for case in book.cases:
        rid = case.record.notice_id
        if rid not in live:
            raise ValueError(f"reviewed case {rid} is absent from current Assess; refresh it first")
        for target in case.targets:
            # One motion per case, many targets; enrichment cannot invent motions.
            rows.append({"notice_id": rid, "motion_id": f"review:{rid}",
                         "row_class": "adjacency", "name": target.name,
                         "title": target.role, "email": target.email, "phone": target.phone,
                         "source_url": str(target.source_url),
                         "contact_source_class": ("government published"
                             if target.source_kind == "government_published" else target.source_kind)})
    return {**projection, "rows": rows}


def attach_research(batch, book: ReviewedCases):
    from agents.leadgen.enums import NextActionVerb
    parents = {p.assessment_id: p for p in batch.parents}
    cases = {c.record.notice_id: c for c in book.cases}
    leads = []
    for lead in batch.leads:
        case = cases.get(parents[lead.parent_assessment_id].notice_id)
        if case:
            action = lead.next_action.model_copy(update={
                "verb": NextActionVerb.REVIEW_REQUIREMENT_SPAN,
                "object": case.research.next_ask})
            lead = lead.model_copy(update={"targets": case.targets, "research": case.research,
                                           "next_action": action})
        leads.append(lead)
    return batch.model_copy(update={"leads": tuple(leads)})
