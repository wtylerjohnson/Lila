"""Agency projection of a sweep's results: ONE owner (2026-07-12).

The 2026-07-10 NETSCOUT DHS report leaked DISA/IRS displacement rows
because the buyer-map layer postdated the scoper; the fix lived only in
run_agency_report, and the L18/L19 filter-first convergence bypassed it:
a filter-first scoped artifact deliberately keeps free context layers
(buyer map, news, expiring awards, forecast) WHOLE-MARKET, and the
full-federal capture path trusted the artifact without re-scoping, so a
DHS deliverable rendered a DISA displacement window as a forming play
(2026-07-12, operator-caught). Every consumer of a FOCUS-scoped sweep
now projects context layers through this module before any join.
"""

from __future__ import annotations

import copy


def scope_results(results: dict, agency) -> tuple[dict, dict]:
    """Deterministic agency scope of a sweep's results. Returns (scoped,
    stats). Agency-keyed layers filter; whole-market layers that would
    misrepresent an agency lane (subawards, research picture) are dropped and
    surface as gaps; internal-only layers (client_awards) pass through.

    `agency` is one agency dict or a list of them; a multi-agency focus keeps
    rows matching ANY selected agency (union, never intersection)."""
    from tools.agencies import matches_record
    agencies = agency if isinstance(agency, list) else [agency]
    agency = {"abbr": "+".join(a.get("abbr") or "" for a in agencies),
              "name": " / ".join(a.get("name") or "" for a in agencies)}
    r = copy.deepcopy(results)
    stats: dict = {}

    def hit(*strings) -> bool:
        joined = " / ".join(s for s in strings if s)
        return any(matches_record(joined, a) for a in agencies)

    notices = [n for n in (r.get("sam.gov") or []) if hit(n.get("agency"))]
    stats["notices"] = f"{len(notices)}/{len(r.get('sam.gov') or [])}"
    kept_ids = {n.get("source_id") for n in notices}
    r["sam.gov"] = notices
    r["triage"] = {k: v for k, v in (r.get("triage") or {}).items()
                   if k in kept_ids}

    ex = r.get("expiring_awards") or {}
    if ex.get("rows"):
        rows = [x for x in ex["rows"]
                if hit(x.get("awarding_agency"), x.get("awarding_sub_agency"))]
        stats["expiring"] = f"{len(rows)}/{len(ex['rows'])}"
        r["expiring_awards"] = {**ex, "rows": rows,
                                "basis": ex.get("basis", "") + f" · scoped to {agency['abbr']}"}

    fc = r.get("forecast_signals") or {}
    if fc.get("matched") is not None:
        matched = [m for m in fc["matched"]
                   if hit((m.get("record") or m).get("component") or "",
                          (m.get("record") or m).get("agency") or "")]
        stats["forecast_lines"] = f"{len(matched)}/{len(fc['matched'])}"
        r["forecast_signals"] = {**fc, "matched": matched}

    news = (r.get("news") or {}).get("items")
    if news is not None:
        kept = [i for i in news
                if hit(i.get("title", "") + " " + i.get("summary", ""))]
        stats["news"] = f"{len(kept)}/{len(news)}"
        r["news"] = {**r["news"], "items": kept}

    # INCUMBENT BUYER MAP (2026-07-10): agency-keyed, so it FILTERS — the
    # NETSCOUT DHS report leaked DISA/IRS displacement rows because this
    # layer postdated the scoper. Buyers keep only when their office or
    # agency matches; window/sole-source aggregates recompute from the kept.
    bm = r.get("incumbent_buyer_map") or {}
    if bm.get("buyers"):
        kept_b = [b for b in bm["buyers"]
                  if hit(b.get("buyer"), b.get("agency"))]
        stats["buyer_map"] = f"{len(kept_b)}/{len(bm['buyers'])}"
        r["incumbent_buyer_map"] = {
            **bm, "buyers": kept_b,
            "displacement_windows": [b for b in kept_b
                                     if b.get("displacement_window")],
            "population_label": (bm.get("population_label") or "") +
            f" · scoped to {agency['abbr']}"}

    # whole-market layers a single-agency report must not imply as scoped
    for k in ("subawards", "research_picture", "contract_awards"):
        r.pop(k, None)
    return r, stats


def scope_focus_results(searches: dict) -> dict:
    """The results a REPORT JOIN may consume from this sweep.

    A FOCUS-scoped sweep deliberately carries whole-market context layers
    (L18); every join projects them through the agency filter here so a
    scoped deliverable can never render a foreign agency's row as its own
    (the 2026-07-12 DISA-in-DHS leak). ALL-scope sweeps pass through
    untouched at zero cost; sam.gov/triage are already scoped and filter
    idempotently; the census and USAspending market totals stay whole-market
    context by doctrine.
    """
    searches = searches or {}
    results = searches.get("results") or {}
    scope = searches.get("search_scope") or {}
    selected = scope.get("agencies") or []
    if scope.get("mode") != "focus" or not selected:
        return results
    from tools.agencies import find
    agencies = []
    for sel in selected:
        found = find((sel or {}).get("abbr") or (sel or {}).get("name") or "")
        if found:
            agencies.append(found)
    if not agencies:
        return results
    scoped, _stats = scope_results(results, agencies)
    # The live lane is scoped at the SOURCE (filter-first sweep) and guarded
    # separately (strict allowlist, census lints); re-filtering it here would
    # also hide malformed legacy rows from the parity packet's reconciliation,
    # which exists to EXPLAIN them. The leak class was context layers only.
    for live_key in ("sam.gov", "triage"):
        if live_key in results:
            scoped[live_key] = results[live_key]
    return scoped


def focus_agencies(searches: dict) -> list[dict]:
    """The gate's resolved agency dicts for a FOCUS sweep; [] otherwise."""
    scope = (searches or {}).get("search_scope") or {}
    selected = scope.get("agencies") or []
    if scope.get("mode") != "focus" or not selected:
        return []
    from tools.agencies import find
    out = []
    for sel in selected:
        found = find((sel or {}).get("abbr") or (sel or {}).get("name") or "")
        if found:
            out.append(found)
    return out


_AGENCY_FIELDS = ("agency", "awarding_agency", "awarding_sub_agency",
                  "awarding_office", "buyer", "component")


def _department_abbrs(agency_string: str) -> set:
    """Every DEPARTMENT abbr an agency string affirmatively belongs to.

    A component resolves to its parent department; a department to itself.
    Empty when the string matches no known agency — an UNKNOWN string is not
    proof of foreignness (it may be an unlisted in-scope component, e.g. the
    Secret Service under DHS), so containment never blocks on ignorance.
    """
    from tools.agencies import AGENCIES, matches_record
    depts = set()
    for a in AGENCIES:
        if matches_record(agency_string, a, include_components=False):
            depts.add(a["abbr"] if a["parent"] is None else a["parent"])
    return depts


def agency_is_foreign(agency_string, gate_agencies) -> bool:
    """True ONLY when the string affirmatively resolves to a department
    outside the gate (DISA->DoD against a DHS gate). In-gate and
    unclassifiable strings are not foreign. This is the safe fail-closed
    predicate: it never blocks a legitimate in-scope component just because
    the reference table does not list it."""
    if not agency_string or not isinstance(agency_string, str):
        return False
    from tools.agencies import matches_record
    if any(matches_record(agency_string, a) for a in gate_agencies):
        return False  # directly in gate (dept or listed component)
    resolved = _department_abbrs(agency_string)
    if not resolved:
        return False  # unknown: not proof of foreignness
    gate_depts = {a["abbr"] if a["parent"] is None else a["parent"]
                  for a in gate_agencies}
    return bool(resolved - gate_depts)


def value_focus_violation(value, agencies) -> str:
    """Fail-closed boundary check for one fact/row payload (2026-07-12).

    Returns '' when no agency-bearing field affirmatively resolves OUTSIDE
    the gate; otherwise names the offending field. Nested 'opportunity'
    payloads are checked too. Deterministic and side-effect free. Uses the
    affirmatively-foreign predicate so an unlisted in-scope component is
    never rejected.
    """
    if not agencies or not isinstance(value, dict):
        return ""
    for key in _AGENCY_FIELDS:
        raw = value.get(key)
        if agency_is_foreign(raw, agencies):
            return f"{key}={str(raw)[:60]}"
    nested = value.get("opportunity")
    if isinstance(nested, dict):
        deeper = value_focus_violation(nested, agencies)
        if deeper:
            return deeper
    return ""


def document_focus_violations(doc, searches) -> list[str]:
    """Render-boundary fail-closed gate (2026-07-12): the LAST check before a
    scoped deliverable ships. Returns a list of out-of-gate agency strings
    found on any ACTIONABLE document surface (pursuit board, pipeline,
    partnering plays, buyer incumbents). Empty list = clean. Both report
    runners treat a nonempty list as a blocking violation, so even a future
    path that skips the fact-pack gate cannot render a foreign agency as a
    DHS row.
    """
    agencies = focus_agencies(searches)
    if not agencies or doc is None:
        return []
    bad: list[str] = []

    def _check(agency, where):
        if agency_is_foreign(agency, agencies):
            bad.append(f"{where}: {str(agency)[:60]}")

    for p in getattr(doc.board, "pursuits", []) or []:
        _check(getattr(p, "agency", None), "pursuit")
    for pl in getattr(doc.partnering, "plays", []) or []:
        _check(getattr(pl, "agency", None), f"partnering:{getattr(pl, 'kind', '?')}")
    for b in getattr(doc, "buyer_incumbents", []) or []:
        for agency in (list(getattr(b, "agencies", []) or []) or [None]):
            _check(agency, f"buyer:{getattr(b, 'company', '?')}")
    return bad
