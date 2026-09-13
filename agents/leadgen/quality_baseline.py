"""Review-bound minimum quality; selection never deletes assessment history."""
from __future__ import annotations

from pathlib import Path


def check_reviewed_quality(receipt, book) -> dict:
    parents = {p.notice_id: p for p in receipt.parents if p.notice_id}
    by_parent = {}
    for lead in receipt.leads:
        by_parent.setdefault(lead.parent_assessment_id, []).append(lead)
    problems = []
    for case in book.cases:
        rid = case.record.notice_id
        parent = parents.get(rid)
        if not parent:
            problems.append(f"missing reviewed assessment: {rid}")
            continue
        children = by_parent.get(parent.assessment_id, [])
        reviewed = [c for c in children if c.research == case.research and c.targets == case.targets]
        if not reviewed:
            problems.append(f"lost reviewed targets or action: {rid}")
        # These research-only inputs cannot certify a new live bid or seller path.
        if any(c.lead_tier.value in {"LEAD_T1", "LEAD_T2"} for c in reviewed):
            problems.append(f"research-only case falsely promoted: {rid}")
    research_parents={p.subject_id:p for p in receipt.parents if p.research_subject is not None}
    for overlay in book.subjects:
        parent=research_parents.get(overlay.subject_id)
        if parent is None or parent.research != overlay.research or parent.targets != overlay.targets:
            problems.append(f"lost parent research or targets: {overlay.subject_id}")
        elif parent.lead_ids or by_parent.get(parent.assessment_id):
            problems.append(f"research-only subject incorrectly minted a child: {overlay.subject_id}")
    if problems:
        raise ValueError("quality baseline failed: " + "; ".join(problems))
    return {"schema_version": "lead_quality.v1", "passed": True,
            "assess_run_id": receipt.assess_run_id,
            "dossier_sha256": receipt.dossier_sha256,
            "assessment_ids": [p.assessment_id for p in receipt.parents],
            "reviewed_cases": book.model_dump(mode="json"),
            "priority_notice_ids": [c.record.notice_id for c in book.cases if c.research.priority],
            "retained_notice_ids": list(parents),
            "retained_research_subject_ids": list(research_parents),
            "qualification_tiers": {c.lead_id: c.lead_tier.value for c in receipt.leads}}


def preserve_baseline(baseline: dict, directory: Path) -> None:
    import hashlib
    import json

    from tools.artifacts import atomic_write_json
    raw = json.dumps(baseline, sort_keys=True).encode()
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{hashlib.sha256(raw).hexdigest()}.json"
    if not path.exists():
        atomic_write_json(path, baseline)
