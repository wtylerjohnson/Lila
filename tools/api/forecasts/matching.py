"""Forecast ↔ capability matching — same gates as the SAM path, signals-only.

Reuses the prefilter's NAICS-family and set-aside logic plus keyword matching
on title/description. Output is a scored subset of ForecastRecord; it never
constructs a RawOpportunity, so matches structurally cannot enter the
live-opportunity flow.
"""

from __future__ import annotations

from agents.assess.prefilter import _naics_family, set_aside_matches
from agents.schemas import CapabilityProfile, ForecastRecord
from tools.relevance.engine import score_record
from tools.relevance.taxonomy import CapabilityTaxonomy
from tools.text_match import matching_phrases


def _naics_ok(rec: ForecastRecord, profile: CapabilityProfile) -> bool:
    if not rec.naics_code or not profile.naics_codes:
        return True  # unknown -> inclusive, same rule as the live path
    if rec.naics_code in profile.naics_codes:
        return True
    fam = _naics_family(rec.naics_code)
    return any(_naics_family(c) == fam for c in profile.naics_codes)


def _set_aside_ok(rec: ForecastRecord, profile: CapabilityProfile) -> bool:
    if not rec.set_aside or rec.set_aside.strip().lower() in ("none", "full and open"):
        return True
    if not profile.set_aside_eligibility:
        return False  # restricted forecast, client claims nothing
    return set_aside_matches(rec.set_aside, profile.set_aside_eligibility)


def _keyword_hits(rec: ForecastRecord, keywords: list[str]) -> list[str]:
    hay = f"{rec.title} {rec.description or ''} {rec.naics_label or ''}"
    return matching_phrases(hay, keywords)


def _record_naics_codes(rec: ForecastRecord) -> list[str]:
    """All NAICS values stated for a record, including merged Army rows."""

    values = [rec.naics_code] if rec.naics_code else []
    published = rec.source_fields.get("_published_values")
    if isinstance(published, dict):
        alternatives = published.get("naics_code")
        if isinstance(alternatives, list):
            values.extend(str(value) for value in alternatives if value)
    return sorted({value for value in values if value})


def match_forecasts(
    records: list[ForecastRecord],
    profile: CapabilityProfile,
    keywords: list[str] | None = None,
    *,
    taxonomy: CapabilityTaxonomy | None = None,
    engagement_scope=None,
) -> list[dict]:
    """Score forecasts against the client. Returns signal dicts, sorted by
    strength: {'record': ForecastRecord, 'reasons': [...], 'score': float}.

    A record must clear the hard gates (NAICS family, set-aside eligibility)
    AND show positive evidence (exact NAICS or a keyword hit) — forecasts are
    numerous and vague, so unlike the live path, 'unknown' alone isn't a match.
    """
    if taxonomy is not None:
        return bucket_forecasts(
            records,
            profile,
            keywords=keywords,
            taxonomy=taxonomy,
            engagement_scope=engagement_scope,
        )["capability"]

    kws = keywords if keywords is not None else (
        profile.past_performance_keywords + profile.tech_stack)
    out = []
    for rec in records:
        if not _naics_ok(rec, profile) or not _set_aside_ok(rec, profile):
            continue
        reasons, score = [], 0.0
        if rec.naics_code and rec.naics_code in profile.naics_codes:
            reasons.append(f"exact NAICS {rec.naics_code}")
            score += 0.5
        elif rec.naics_code:
            reasons.append(f"NAICS family {_naics_family(rec.naics_code)}xx")
            score += 0.15
        hits = _keyword_hits(rec, kws)
        if hits:
            reasons.append("keywords: " + ", ".join(sorted(hits)[:4]))
            score += min(0.5, 0.2 * len(hits))
        if score < 0.2:  # no positive evidence -> not a signal, just noise
            continue
        out.append({"record": rec, "reasons": reasons, "score": round(score, 2)})
    out.sort(key=lambda m: -m["score"])
    return out


def bucket_forecasts(
    records: list[ForecastRecord],
    profile: CapabilityProfile,
    keywords: list[str] | None = None,
    *,
    taxonomy: CapabilityTaxonomy | None = None,
    engagement_scope=None,
) -> dict:
    """Three buckets per client (L15): capability-match (keyword evidence in
    the record text), lane-match-only (clears the NAICS lane, no capability
    evidence), no-match. Capability matches surface as HZN candidates with
    the forecast record as the verified signal; lane counts feed the
    screening-stats line. Bucket assignment is exclusive and exhaustive."""
    kws = keywords if keywords is not None else (
        profile.past_performance_keywords + profile.tech_stack)
    capability: list[dict] = []
    lane_only: list[ForecastRecord] = []
    no_match = 0
    for rec in records:
        if not _set_aside_ok(rec, profile):
            no_match += 1
            continue
        stated_codes = _record_naics_codes(rec)
        code_ok = _naics_ok(rec, profile) or any(
            code in profile.naics_codes
            or any(_naics_family(client_code) == _naics_family(code)
                   for client_code in profile.naics_codes)
            for code in stated_codes
        )
        in_lane = bool(stated_codes) and bool(profile.naics_codes) and any(
            code in profile.naics_codes
            or any(_naics_family(client_code) == _naics_family(code)
                   for client_code in profile.naics_codes)
            for code in stated_codes
        )
        if taxonomy is not None:
            scoring_record = rec.model_dump(mode="python")
            universe = taxonomy.code_universe
            if universe is not None and stated_codes:
                scoring_record["naics_code"] = next(
                    (code for code in stated_codes if code in universe.naics),
                    rec.naics_code,
                )
            verdict = score_record(
                scoring_record,
                taxonomy,
                engagement_scope=engagement_scope,
            )
            hits = verdict.core_terms
            adjacent = verdict.adjacent_terms
            relevant = verdict.relevant
            relevance_score = verdict.score
        else:
            if not code_ok:
                no_match += 1
                continue
            hits = _keyword_hits(rec, kws)
            adjacent = []
            relevant = bool(hits)
            # Preserve the legacy keyword match score for clients that have
            # not yet been upgraded to a curated capability taxonomy.
            relevance_score = min(0.5, 0.2 * len(hits))
        if relevant:
            reasons = ["capability: " + ", ".join(sorted(hits)[:4])]
            if adjacent:
                reasons.append(
                    "adjacent: " + ", ".join(sorted(adjacent)[:4]))
            exact_code = next(
                (code for code in stated_codes if code in profile.naics_codes),
                None,
            )
            if exact_code:
                reasons.append(f"exact NAICS {exact_code}")
            capability.append({"record": rec,
                               "reasons": reasons,
                               "score": round(relevance_score, 2),
                               "naics_lane_match": in_lane,
                               "keyword_hits": sorted(hits),
                               "adjacent_hits": sorted(adjacent),
                               "match_spans": [span.model_dump(mode="json") for span in verdict.spans] if taxonomy is not None else []})
        elif in_lane:
            lane_only.append(rec)
        else:
            no_match += 1
    capability.sort(
        key=lambda row: (
            -row["score"],
            row["record"].source.casefold(),
            row["record"].source_id.casefold(),
        )
    )
    return {"capability": capability, "lane_only": lane_only,
            "no_match_count": no_match}


def screen_line(buckets: dict, total: int, source_label: str,
                gaps: list[str] | None = None) -> str:
    """The screening-stats sentence for reports: what was screened, what
    cleared the lane, what capability-matched, what was NOT covered."""
    lane_n = len(buckets["lane_only"]) + sum(
        1 for row in buckets["capability"] if row.get("naics_lane_match")
    )
    line = (
        f"Screened {total} {source_label} forecast records; "
        f"{len(buckets['capability'])} matched the client's capability "
        f"vocabulary, and {lane_n} carried a client-lane NAICS code "
        f"({len(buckets['lane_only'])} NAICS-lane-only)."
    )
    if gaps:
        line += (f" Not screened: {', '.join(gaps)} (no published forecast "
                 f"surface registered).")
    return line
