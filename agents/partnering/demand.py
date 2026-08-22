"""Subcontracting-demand signal; pure summarizer over SOURCED award rows.

Input rows come from SubawardsSource.demand_awards(): above-threshold prime
awards whose recipients USAspending categorizes other_than_small_business
(the recipient business-category filter is the citable size source; an
other-than-small awardee is definitionally outside small-business
set-asides, so the subcontracting-plan requirement applies).

An unverified threshold (config None) degrades the whole signal to N/A with
a flag; never a guess.
"""

from __future__ import annotations

from typing import Any, Optional

from agents.partnering.config import (
    SUBCONTRACTING_PLAN_THRESHOLD_USD, SUBCONTRACTING_THRESHOLD_PROVENANCE,
)


def subcontracting_demand(
    rows_by_naics: dict[str, list[dict]],
    *, threshold_usd: Optional[float] = SUBCONTRACTING_PLAN_THRESHOLD_USD,
) -> dict[str, Any]:
    """{na, flag?, threshold_usd, provenance, by_naics: {naics ->
    {recipients: [{name, total, award_count}], rows}}}. Recipients ranked by
    above-threshold dollars; the primes most likely to carry subcontracting
    plans in the client's lanes."""
    if threshold_usd is None:
        return {"na": True,
                "flag": "subcontracting-plan threshold unverified at this "
                        "review; demand signal N/A (FAR 19.702 figure "
                        "adjusts under inflation reviews; verify and set "
                        "SUBCONTRACTING_PLAN_THRESHOLD_USD)",
                "threshold_usd": None,
                "provenance": SUBCONTRACTING_THRESHOLD_PROVENANCE,
                "by_naics": {}}
    out: dict[str, Any] = {"na": False, "threshold_usd": threshold_usd,
                           "provenance": SUBCONTRACTING_THRESHOLD_PROVENANCE,
                           "by_naics": {}}
    for naics, rows in (rows_by_naics or {}).items():
        clean = [r for r in rows or [] if isinstance(r, dict) and not r.get("error")]
        recipients: dict[str, dict] = {}
        for r in clean:
            name = r.get("recipient")
            amt = r.get("amount")
            if not name or not isinstance(amt, (int, float)):
                continue
            rec = recipients.setdefault(name, {"name": name, "total": 0.0,
                                               "award_count": 0})
            rec["total"] += amt
            rec["award_count"] += 1
        out["by_naics"][naics] = {
            "recipients": sorted(recipients.values(),
                                 key=lambda x: -x["total"]),
            "rows": clean,
        }
    return out
