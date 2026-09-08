"""Assessment-bound lead generation supplied alongside the Market Map.

Pure press and scoring after a read-only ledger export. A missing lead input
is disclosed without discarding the independently usable market assessment.
"""
from __future__ import annotations

from collections import Counter
from pathlib import Path

from agents.leadgen.eval.checks import score_pack
from agents.leadgen.eval.render import render_csv
from agents.leadgen.export_assess import export_current_assess_run
from agents.leadgen.press import run_press
from agents.leadgen.press_html import render_html


def build_leadgen_companion(client_name: str, root: Path) -> dict:
    envelope = export_current_assess_run(
        client_name, state_dir=root / "data" / "state" / "assess_runs",
        review_dir=root / "data" / "review")
    receipt = run_press(assess=envelope, client_name=client_name)
    payload = receipt.model_dump(mode="json")
    run = envelope["run"]
    evidence_dates = {
        row["notice_id"]: min(dates) if dates else None
        for row in run["live"]["records"]
        for dates in [[e["retrieved_at"] for e in row["authoritative_evidence"]
                       if e.get("retrieved_at")]]
    }
    card = score_pack(payload) if receipt.parents else None
    return {
        "status": "complete",
        "assess_run_id": receipt.assess_run_id,
        "assessment_as_of": receipt.as_of.isoformat(),
        "assessment_count": len(receipt.parents),
        "lead_count": len(receipt.leads),
        "tiers": dict(Counter(lead.lead_tier.value for lead in receipt.leads)),
        "evidence_dates": evidence_dates,
        "assessment": envelope,
        "receipt": payload,
        "html": render_html(receipt),
        "scorecard_csv": render_csv(card) if card else "",
    }


def restore_assessment_population(pack, companion: dict, *, conn=None):
    """Retain the full stored assessment population and recover missing facts.

    Reuses the notice-store writer's record adapter. Existing facts are never
    overwritten, source dates are preserved, and no lead gate is advanced.
    """
    from agents.golden_press.l1_store import _to_record
    from agents.golden_press.notice_join import leverage_rank
    from agents.golden_press.records import GoldenRecord

    result = pack.model_copy(deep=True)
    records = {r.record_id: r for r in result.records}
    run = (companion.get("assessment") or {}).get("run") or {}
    for row in (run.get("live") or {}).get("records", []):
        rid = row["notice_id"]
        if rid not in records:
            evidence = row.get("authoritative_evidence") or []
            records[rid] = GoldenRecord(
                record_id=rid, lane="L1_notice", title=row["title"],
                agency=row.get("agency"), office=row.get("office"),
                response_deadline=row.get("response_deadline"),
                description=row.get("requirement_excerpt"),
                url=next((e.get("source_url") for e in evidence if e.get("source_url")), None),
                retrieved_at=min((e["retrieved_at"] for e in evidence if e.get("retrieved_at")), default=None),
                source_fields={"classification": row.get("classification"),
                               "recommendation": row.get("recommendation"),
                               "assess_run_id": run.get("run_id")})
    recovered = []
    if conn is not None:
        for rid, record in records.items():
            if record.lane != "L1_notice":
                continue
            row = conn.execute("SELECT * FROM notices WHERE notice_id = ?", (rid,)).fetchone()
            if row is None:
                continue
            source = _to_record(row, record.entity_hits, leverage_rank(row["notice_type"]))
            fields = []
            for field in ("description", "contact_name", "contact_email", "contact_phone",
                          "contact_secondary_email", "contact_quality", "contact_use",
                          "notice_type", "notice_leverage_rank", "office", "naics", "psc",
                          "set_aside", "posted_date", "response_deadline", "url"):
                if not getattr(record, field) and getattr(source, field):
                    setattr(record, field, getattr(source, field))
                    fields.append(field)
            if fields:
                record.source_fields["retained_store_detail"] = {
                    "fields": fields, "last_seen": source.retrieved_at,
                    "source": "SAM notice store", "notice_id": rid}
                # Never relabel old evidence with the release/materialization date.
                dates = [d for d in (record.retrieved_at, source.retrieved_at) if d]
                record.retrieved_at = min(dates) if dates else None
                recovered.append(rid)
    result.records = list(records.values())
    result.research["assessment_retention"] = {
        "assess_run_id": run.get("run_id"), "original_records": len(pack.records),
        "retained_records": len(result.records), "recovered_details": recovered}
    return result
