"""Full-store frame re-screen: the tiered buyer-language frame against every
stored notice and every wired forecast source. Zero LLM, zero network, zero
metered quota; bash is its sanctioned lane (CLAUDE.md, diagnostic scripts).

Work order A1 (operator, 2026-08-17). The frame rides
clients/<slug>/frame_tiers.json, the single record of the operator's tiered
vocabulary: tier 1 names, tier 2 buyer language, tier 3 context. The keep
rule is the operator's own: a record is kept only on a tier 1 or tier 2
sentence match; tier 3 is context and never qualifies a record alone.

Nothing here re-judges relevance on its own vocabulary. Every match rides
the owners that already exist:

  tier 1 names        -> sam_lanes.build_matchers / reject_entity_hit, the
                         shared entity guard (vendor proximity for ambiguous
                         names, reseller rules, witnessed negative context)
  tier 2 / 3 language -> term_tiers.evaluate_term, the guard ladder
                         (distinctive alone; phrases need a tech code;
                         single common words need a tech code AND a domain
                         anchor)
  leverage            -> notice_join.leverage_rank (1 Sources Sought / RFI,
                         2 Special Notice / Presolicitation,
                         3 Solicitation, 4 Award: history, never printed as
                         an opportunity)
  family dedupe       -> notice_join.dedupe_by_title_agency

THE SENTENCE IS THE RECEIPT. Every kept record carries the exact sentence
the qualifying term matched in, extracted verbatim from the stored title or
description text, so "why is this here" is always answerable with quoted
record text (the vocabulary-receipt doctrine, 2026-07-31).

Bare "Purview" is measured, never trusted: the token is the English idiom
("under the purview of") in most federal text, so bare occurrences are
counted and sampled in the receipt for corpus witnessing but never qualify
a keep. Vendor-qualified "Microsoft Purview" rides the entity route.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from datetime import date
from types import SimpleNamespace
from typing import Any, Iterable, Optional

from agents.golden_press.notice_join import (
    SOLICITABLE_MAX_RANK, dedupe_by_title_agency, leverage_rank,
)
from agents.golden_press.retrieval import (
    TECH_NAICS_PREFIXES, TECH_PSC_PREFIXES, _notice_url,
)
from agents.golden_press.sam_lanes import (
    _corpus_negatives, _pattern, aliases, build_matchers, reject_entity_hit,
)
from agents.golden_press.term_tiers import (
    TIER_COMMON, TIER_PHRASE, _find_all, anchor_near, evaluate_term,
    load_guards, negative_context_hit, tier_of,
)

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_FORECAST_STORE_DIR = os.path.join(_ROOT, "data", "state", "forecast_store")
_OUT_DIR = os.path.join(_ROOT, "data", "state", "frame_rescreen")

_SELECT = """
SELECT notice_id, title, notice_type, agency, subtier, office, posted,
       deadline, naics, psc, set_aside, url, description_prefix, last_seen,
       poc_title, poc_name, poc_email, poc_phone,
       poc_secondary_name, poc_secondary_email, poc_secondary_phone
  FROM notices
"""

_SENTENCE_SPLIT = re.compile(r"(?<=[.!?])\s+|\n+")
_MAX_SENTENCE = 320

# The bare-token idiom measurement (never a keep). Word-boundary, same
# discipline as term_tiers._find_all.
_PURVIEW = re.compile(r"(?<![A-Za-z0-9])purview(?![A-Za-z0-9])", re.I)
_PURVIEW_IDIOM = re.compile(
    r"(?:under|within|outside|in|beyond)\s+(?:the\s+|its\s+|their\s+)?"
    r"(?:[A-Za-z]+(?:['’]s)?\s+){0,3}purview"
    r"|purview\s+of"
    r"|['’]s\s+purview", re.I)


def load_frame(client_slug: str) -> dict:
    path = os.path.join(_ROOT, "clients", client_slug, "frame_tiers.json")
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


def frame_vocabulary(frame: dict) -> dict:
    """The screen inputs derived from the frame file, one place.

    Returns entities by kind (tier 1 names through the entity guard), the
    capability terms with the operator's tier per term, and the vendor name.
    """
    routing = frame.get("screen_routing") or {}
    products = list(routing.get("tier1_client_names") or [])
    rivals = list(routing.get("tier1_rival_names") or [])
    vendor = products[0] if products else frame.get("client_name", "")

    tier_by_term: dict[str, int] = {}
    for name in products + rivals:
        tier_by_term[name.casefold()] = 1
    cap_terms: list[str] = []
    for term in frame["as_ordered"]["tier2"]:
        tier_by_term[term.casefold()] = 2
        cap_terms.append(term)
    for term in routing.get("tier2_added_by_routing", []):
        if term not in cap_terms:
            tier_by_term[term.casefold()] = 2
            cap_terms.append(term)
    for term in frame["as_ordered"]["tier3"]:
        tier_by_term[term.casefold()] = 3
        cap_terms.append(term)

    # Entity names never ride the capability list (the SteelHead lesson,
    # shared by every screening lane): the guarded entity route is their
    # only door. "Microsoft Purview" is such a name even though the operator
    # files it under tier 2; the entity route matches it alone and the
    # tier map still reports it as tier 2.
    entity_keys = {n.casefold() for n in products + rivals}
    stripped = [t for t in cap_terms if t.casefold() in entity_keys]
    cap_terms = [t for t in cap_terms if t.casefold() not in entity_keys]

    return {
        "vendor": vendor,
        "entities": {"product": products, "competitor": rivals},
        "capability_terms": cap_terms,
        "tier_by_term": tier_by_term,
        "capability_terms_stripped_to_entity_route": stripped,
    }


def _has_tech_code(naics: Any, psc: Any) -> bool:
    n, p = str(naics or ""), str(psc or "")
    return (any(n.startswith(x) for x in TECH_NAICS_PREFIXES)
            or any(p.startswith(x) for x in TECH_PSC_PREFIXES))


def sentence_for(text: str, span: tuple[int, int], title_len: int) -> str:
    """The verbatim sentence containing the span; the title when the match
    sits inside it. Clamped so a run-on federal paragraph stays printable."""
    if span[0] < title_len:
        sentence = text[:title_len].strip()
        prefix = "TITLE: "
    else:
        start, end = 0, len(text)
        for m in _SENTENCE_SPLIT.finditer(text):
            if m.end() <= span[0]:
                start = m.end()
            elif m.start() >= span[1]:
                end = m.start()
                break
        sentence = text[start:end].strip()
        prefix = ""
    if len(sentence) > _MAX_SENTENCE:
        base = 0 if span[0] < title_len else start
        left = max(span[0] - _MAX_SENTENCE // 2, base)
        sentence = "... " + " ".join(
            text[left:left + _MAX_SENTENCE].split()) + " ..."
    return prefix + " ".join(sentence.split())


def _passing_span(term: str, text: str, *, has_code: bool,
                  guards: dict) -> Optional[tuple[int, int]]:
    """The first span that satisfies the same ladder leg evaluate_term
    accepted, so the printed sentence is the sentence that qualified."""
    tier = tier_of(term)
    for span in _find_all(text, term):
        if negative_context_hit(text, span, guards):
            continue
        if tier == TIER_PHRASE and has_code:
            return span
        if tier == TIER_COMMON and has_code and anchor_near(text, span, guards):
            return span
        if tier not in (TIER_PHRASE, TIER_COMMON):
            return span
    return None


def screen_text(text: str, title_len: int, vocab: dict, matchers: dict,
                vendor_pattern, guards: dict, negatives: tuple,
                has_code: bool, receipt: dict) -> list[dict]:
    """Every qualifying hit on one record's text, with its tier, route and
    receipt sentence. Overlapping hits keep the longest term (the entity
    lineage rule applied to vocabulary)."""
    hits: list[dict] = []

    vendor_present = bool(vendor_pattern.search(text)) if vendor_pattern else False
    product_hits = {n for n, m in matchers.items()
                    if m["kind"] in ("product", "competitor")
                    and m["pattern"].search(text)}
    for name, matcher in matchers.items():
        m = matcher["pattern"].search(text)
        if not m:
            continue
        why = reject_entity_hit(matcher, vendor_present=vendor_present,
                                product_hits=product_hits, text=text,
                                negative_phrases=negatives)
        if why:
            receipt["entity_rejections"][why] = (
                receipt["entity_rejections"].get(why, 0) + 1)
            continue
        tier = vocab["tier_by_term"].get(name.casefold(), 1)
        hits.append({"term": name, "tier": tier, "route": "entity",
                     "span": m.span(), "sentence": sentence_for(
                         text, m.span(), title_len)})

    for term in vocab["capability_terms"]:
        verdict = evaluate_term(term, text, has_tech_code=has_code,
                                distinctive_names=(), guards=guards)
        if verdict is None:
            continue
        if verdict.get("rejected"):
            why = str(verdict.get("why", "guard"))
            receipt["ladder_rejections"][why] = (
                receipt["ladder_rejections"].get(why, 0) + 1)
            continue
        span = _passing_span(term, text, has_code=has_code, guards=guards)
        if span is None:
            continue
        tier = vocab["tier_by_term"][term.casefold()]
        hits.append({"term": term, "tier": tier, "route": "ladder",
                     "ladder_tier": verdict["tier"], "span": span,
                     "sentence": sentence_for(text, span, title_len)})

    hits.sort(key=lambda h: (h["span"][0], -(h["span"][1] - h["span"][0])))
    kept: list[dict] = []
    last_end = -1
    for h in sorted(hits, key=lambda x: (x["span"][0], -len(x["term"]))):
        if h["span"][0] < last_end and kept and len(h["term"]) <= len(kept[-1]["term"]):
            continue
        kept.append(h)
        last_end = h["span"][1]
    return kept


def _prefilter(vocab: dict, matchers: dict) -> re.Pattern:
    """One alternation over every literal so a no-signal row costs one scan."""
    literals: set[str] = set()
    for m in matchers.values():
        literals.update(m["forms"])
    literals.update(vocab["capability_terms"])
    literals.add("purview")
    ordered = sorted(literals, key=len, reverse=True)
    return re.compile(r"(?<![A-Za-z0-9])(" + "|".join(
        re.escape(t) for t in ordered) + r")(?![A-Za-z0-9])", re.I)


def _measure_purview(text: str, receipt: dict, notice_id: str,
                     title: str) -> None:
    spans = _PURVIEW.findall(text)
    if not spans:
        return
    idiom = bool(_PURVIEW_IDIOM.search(text))
    key = "idiom" if idiom else "bare_non_idiom"
    receipt["purview_measurement"][key] = (
        receipt["purview_measurement"].get(key, 0) + 1)
    samples = receipt["purview_measurement"].setdefault("samples", [])
    if len(samples) < 12:
        m = _PURVIEW.search(text)
        lo, hi = max(0, m.start() - 70), min(len(text), m.end() + 70)
        samples.append({"notice_id": notice_id, "idiom": idiom,
                        "title": title[:80],
                        "context": " ".join(text[lo:hi].split())})


def rescreen_store(frame: dict, *, conn=None, today: Optional[date] = None,
                   ) -> tuple[list[dict], dict]:
    """(keeps, receipt): every stored notice with a qualifying tier 1 or
    tier 2 sentence match, over the WHOLE store. Rank 4 awards are kept as
    evidence rows, flagged, and never printed as opportunities."""
    today = today or date.today()
    vocab = frame_vocabulary(frame)
    guards = load_guards()
    negatives = _corpus_negatives()
    matchers = build_matchers(vocab["entities"], vocab["vendor"])
    vendor_pattern = _pattern(aliases(vocab["vendor"]))
    pre = _prefilter(vocab, matchers)

    receipt: dict[str, Any] = {
        "store_rows": 0, "prefilter_hits": 0, "screened_with_hits": 0,
        "kept": 0, "kept_awards": 0, "tier3_only_dropped": 0,
        "purview_only_dropped": 0, "deduped_away": 0,
        "entity_rejections": {}, "ladder_rejections": {},
        "purview_measurement": {}, "by_rank": {}, "by_term": {},
        "capability_terms_stripped_to_entity_route":
            vocab["capability_terms_stripped_to_entity_route"],
        "keep_rule": frame.get("keep_rule"),
    }

    owned = conn is None
    if conn is None:
        from tools.notice_store import connect
        conn = connect()
    try:
        try:
            from tools.notice_store import last_ingest
            li = last_ingest(conn)
            receipt["store_last_ingest"] = li["ingest_date"] if li else None
        except Exception:  # noqa: BLE001 - a store without an ingest log
            receipt["store_last_ingest"] = None

        keeps: list[dict] = []
        cursor = conn.execute(_SELECT)
        while True:
            rows = cursor.fetchmany(4000)
            if not rows:
                break
            for row in rows:
                receipt["store_rows"] += 1
                title = str(row["title"] or "")
                text = title + "\n" + str(row["description_prefix"] or "")
                if not pre.search(text):
                    continue
                receipt["prefilter_hits"] += 1
                _measure_purview(text, receipt, str(row["notice_id"] or ""),
                                 title)
                has_code = _has_tech_code(row["naics"], row["psc"])
                hits = screen_text(text, len(title), vocab, matchers,
                                   vendor_pattern, guards, negatives,
                                   has_code, receipt)
                if not hits:
                    continue
                receipt["screened_with_hits"] += 1
                qualifying = [h for h in hits if h["tier"] in (1, 2)]
                if not qualifying:
                    receipt["tier3_only_dropped"] += 1
                    continue
                for h in hits:
                    receipt["by_term"][h["term"]] = (
                        receipt["by_term"].get(h["term"], 0) + 1)
                rank = leverage_rank(row["notice_type"])
                best = qualifying[0]
                keeps.append({
                    "notice_id": str(row["notice_id"] or ""),
                    "title": title,
                    "notice_type": row["notice_type"],
                    "leverage_rank": rank,
                    "agency": row["agency"], "subtier": row["subtier"],
                    "office": row["office"],
                    "posted": row["posted"], "deadline": row["deadline"],
                    "naics": row["naics"], "psc": row["psc"],
                    "set_aside": row["set_aside"],
                    "url": _notice_url(row["notice_id"], row["url"]),
                    "last_seen": row["last_seen"],
                    # A4 (operator order, 2026-08-18): every kept notice
                    # carries its SAM points of contact; they render on the
                    # lead and cost nothing (extract columns).
                    "poc": {
                        "primary_title": row["poc_title"],
                        "primary_name": row["poc_name"],
                        "primary_email": row["poc_email"],
                        "primary_phone": row["poc_phone"],
                        "secondary_name": row["poc_secondary_name"],
                        "secondary_email": row["poc_secondary_email"],
                        "secondary_phone": row["poc_secondary_phone"],
                    },
                    "best_tier": min(h["tier"] for h in qualifying),
                    "matched": [{k: v for k, v in h.items() if k != "span"}
                                for h in hits],
                    "matched_sentence": best["sentence"],
                    "matched_term": best["term"],
                    "matched_tier": best["tier"],
                })
        records = [SimpleNamespace(
            lane="L1_notice", title=k["title"], agency=k["agency"],
            notice_leverage_rank=k["leverage_rank"],
            response_deadline=k["deadline"], record_id=k["notice_id"],
            keep=k) for k in keeps]
        deduped, dropped = dedupe_by_title_agency(records)
        receipt["deduped_away"] = len(dropped)
        keeps = [r.keep for r in deduped]
        for k in keeps:
            key = str(k["leverage_rank"])
            receipt["by_rank"][key] = receipt["by_rank"].get(key, 0) + 1
        receipt["kept"] = len(keeps)
        receipt["kept_awards"] = sum(
            1 for k in keeps
            if (k["leverage_rank"] or 0) > SOLICITABLE_MAX_RANK)
        return keeps, receipt
    finally:
        if owned:
            conn.close()


def _live(keep: dict, today: date) -> bool:
    d = str(keep.get("deadline") or "")[:10]
    try:
        return date.fromisoformat(d) >= today
    except ValueError:
        return False


_MONTH_STAMP = re.compile(r"^(\d{4})-(\d{2})$")


def window_ordinal(stated) -> Optional[int]:
    """Comparable ordinal for a stated forecast window.

    Rides store.timing_ordinal (dates and fiscal quarters) and adds the
    yyyy-mm month stamps agency forecast records actually carry."""
    from tools.api.forecasts.store import timing_ordinal
    ordinal = timing_ordinal(str(stated) if stated else None)
    if ordinal is not None:
        return ordinal
    m = _MONTH_STAMP.match(str(stated or "").strip())
    if m:
        try:
            return date(int(m.group(1)), int(m.group(2)), 28).toordinal()
        except ValueError:
            return None
    return None


DECAY_NOTE = ("stated window passed with no matching notice: the recompete "
              "slipped or moved to a vehicle; evidence, not a candidate "
              "opportunity")


_FY_RE = re.compile(r"(20\d{2})|FY\s*(\d{2})\b", re.I)


def _fiscal_year_of(keep: dict) -> Optional[int]:
    m = _FY_RE.search(str(keep.get("fiscal_year") or ""))
    if not m:
        return None
    return int(m.group(1)) if m.group(1) else 2000 + int(m.group(2))


def classify_window(keep: dict, today: date) -> None:
    """Decay rule (operator review item 2, 2026-08-18): a forecast row is an
    agency's promise dated once. A past-window row is RECLASSIFIED, never
    dropped: it stays evidence (recompete slipped, or went to a vehicle),
    it just is not a candidate opportunity a press may place.

    Split follow-up (2026-08-18): a row with no stated month or date but a
    stated fiscal year is fy_only, dated to that federal FY's end
    (September 30) and sorted by it; only a row with nothing at all stays
    unstated. Measured the same day: the Gateway states no solicitation
    windows but carries fiscal_year on 6,587 of 7,654 rows."""
    basis = None
    stated = keep.get("anticipated_solicitation")
    if window_ordinal(stated) is not None:
        basis = "anticipated_solicitation"
    else:
        stated = keep.get("anticipated_award")
        if window_ordinal(stated) is not None:
            basis = "anticipated_award"
    if basis is None:
        fy = _fiscal_year_of(keep)
        if fy is not None:
            keep["window_basis"] = "fiscal_year"
            keep["fy"] = fy
            # Federal FY ends September 30 of the named year.
            if date(fy, 9, 30).toordinal() >= today.toordinal():
                keep["window_state"] = "fy_only"
            else:
                keep["window_state"] = "stated-past"
                keep["decay_note"] = DECAY_NOTE
            return
        keep["window_state"] = "unstated"
        keep["window_basis"] = None
        return
    keep["window_basis"] = basis
    if window_ordinal(stated) >= today.toordinal():
        keep["window_state"] = "live"
    else:
        keep["window_state"] = "stated-past"
        keep["decay_note"] = DECAY_NOTE


def sort_solicitable(keeps: list[dict], today: date) -> list[dict]:
    """Opportunity ordering: live windows first, then leverage (1 best),
    then the operator tier of the best match, most matched terms, soonest
    deadline. Rank 4 awards and cancellations stay out; they are evidence."""
    rows = [k for k in keeps
            if (k["leverage_rank"] or 99) <= SOLICITABLE_MAX_RANK
            or k["leverage_rank"] is None]
    return sorted(rows, key=lambda k: (
        0 if _live(k, today) else 1,
        k["leverage_rank"] or 9,
        k["best_tier"],
        -len(k["matched"]),
        str(k.get("deadline") or "9999-12-31"),
        k["notice_id"],
    ))


def rescreen_forecasts(frame: dict, *, store_dir: Optional[str] = None,
                       today: Optional[date] = None,
                       ) -> tuple[list[dict], dict]:
    """Every wired forecast source's stored records through the same tiered
    screen. Forecasts are PROGRAM-tier agency-stated intent: they never
    enter the live-opportunity flow, and this output labels them so."""
    store_dir = store_dir or _FORECAST_STORE_DIR
    today = today or date.today()
    vocab = frame_vocabulary(frame)
    guards = load_guards()
    negatives = _corpus_negatives()
    matchers = build_matchers(vocab["entities"], vocab["vendor"])
    vendor_pattern = _pattern(aliases(vocab["vendor"]))

    receipt: dict[str, Any] = {
        "sources": {}, "registered_adapters": [], "storeless_adapters": [],
        "entity_rejections": {}, "ladder_rejections": {},
        "purview_measurement": {}, "kept": 0, "tier3_only_dropped": 0,
    }
    try:
        from tools.api.forecasts import forecast_sources
        adapters = []
        for src in forecast_sources():
            name = getattr(src, "name", None) or getattr(
                src, "source_name", None) or type(src).__name__
            adapters.append(str(name))
        receipt["registered_adapters"] = adapters
    except Exception as exc:  # noqa: BLE001 - registry read is advisory
        receipt["registered_adapters_error"] = f"{type(exc).__name__}: {exc}"

    keeps: list[dict] = []
    files = sorted(f for f in os.listdir(store_dir)
                   if f.endswith(".json")) if os.path.isdir(store_dir) else []
    for fname in files:
        source = fname[:-5]
        path = os.path.join(store_dir, fname)
        try:
            with open(path, encoding="utf-8") as fh:
                data = json.load(fh)
        except (OSError, ValueError) as exc:
            receipt["sources"][source] = {"error": f"{type(exc).__name__}: {exc}"}
            continue
        records = list(data.values()) if isinstance(data, dict) else list(data)
        src_kept = 0
        for rec in records:
            if not isinstance(rec, dict):
                continue
            title = str(rec.get("title") or "")
            text = title + "\n" + str(rec.get("description") or "")
            has_code = _has_tech_code(rec.get("naics_code"), rec.get("psc"))
            hits = screen_text(text, len(title), vocab, matchers,
                               vendor_pattern, guards, negatives,
                               has_code, receipt)
            if not hits:
                continue
            qualifying = [h for h in hits if h["tier"] in (1, 2)]
            if not qualifying:
                receipt["tier3_only_dropped"] += 1
                continue
            best = qualifying[0]
            keeps.append({
                "source": source,
                "source_id": rec.get("source_id"),
                "title": title,
                "agency": rec.get("agency"), "component": rec.get("component"),
                "fiscal_year": rec.get("fiscal_year"),
                "forecast_status": rec.get("forecast_status"),
                "anticipated_solicitation": rec.get("anticipated_solicitation"),
                "anticipated_award": rec.get("anticipated_award"),
                "estimated_value_range": rec.get("estimated_value_range"),
                "naics_code": rec.get("naics_code"), "psc": rec.get("psc"),
                "set_aside": rec.get("set_aside"), "url": rec.get("url"),
                "fiscal_year": rec.get("fiscal_year"),
                "best_tier": min(h["tier"] for h in qualifying),
                "matched": [{k: v for k, v in h.items() if k != "span"}
                            for h in hits],
                "matched_sentence": best["sentence"],
                "matched_term": best["term"],
                "matched_tier": best["tier"],
                "evidence_tier": "PROGRAM (agency-stated intent, "
                                 "never a live posting)",
            })
            src_kept += 1
        receipt["sources"][source] = {"records": len(records),
                                      "kept": src_kept}
    for keep in keeps:
        classify_window(keep, today)
    receipt["kept"] = len(keeps)
    receipt["kept_live_window"] = sum(
        1 for k in keeps if k["window_state"] == "live")
    receipt["kept_fy_only"] = sum(
        1 for k in keeps if k["window_state"] == "fy_only")
    receipt["kept_window_unstated"] = sum(
        1 for k in keeps if k["window_state"] == "unstated")
    receipt["kept_stated_past"] = sum(
        1 for k in keeps if k["window_state"] == "stated-past")
    # Press impact (operator review item 3): the number that changes the
    # deliverable is the live-window keep count, not records-in-store.
    receipt["press_impact_live_window_keeps"] = receipt["kept_live_window"]
    registered = {a.casefold() for a in receipt["registered_adapters"]}
    on_disk = {f[:-5].casefold() for f in files}
    receipt["storeless_adapters"] = sorted(registered - on_disk)
    receipt["adapterless_stores"] = sorted(on_disk - registered)
    _STATE_RANK = {"live": 0, "fy_only": 1, "unstated": 2, "stated-past": 3}
    keeps.sort(key=lambda k: (
        _STATE_RANK.get(k.get("window_state"), 4),
        k.get("fy") or 9999,
        str(k.get("anticipated_solicitation") or "9999-12-31"),
        k["best_tier"], k.get("source") or "", str(k.get("source_id"))))
    return keeps, receipt


def _fmt_notice(i: int, k: dict) -> str:
    rank = k["leverage_rank"]
    tag = {1: "L1 shape-it", 2: "L2 door-open", 3: "L3 bid-now",
           None: "L? unranked"}.get(rank, f"L{rank}")
    deadline = str(k.get("deadline") or "no deadline")[:10]
    lines = [
        f"{i:>3}. [{tag}] {k['notice_type'] or 'unknown type'} "
        f"| due {deadline} | {k['agency'] or 'unknown agency'}",
        f"     {k['title']}",
        f"     tier {k['matched_tier']} match "
        f"({', '.join(sorted({h['term'] for h in k['matched']}))})",
        f"     >> {k['matched_sentence']}",
        f"     {k['url']}",
    ]
    return "\n".join(lines)


def _fmt_forecast(i: int, k: dict) -> str:
    window = k.get("anticipated_solicitation") or "window unstated"
    lines = [
        f"{i:>3}. [{k['source']}] {k['agency'] or ''} "
        f"{('/ ' + k['component']) if k.get('component') else ''} "
        f"| solicitation {window} | {k.get('estimated_value_range') or ''}",
        f"     {k['title']}",
        f"     tier {k['matched_tier']} match ({k['matched_term']}) "
        f"[{k.get('window_state', 'unclassified')}]",
        f"     >> {k['matched_sentence']}",
        f"     {k.get('url') or ''}",
    ]
    return "\n".join(lines)


def main(argv: Optional[list] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--client", default="varonis",
                    help="client slug under clients/ (default varonis)")
    ap.add_argument("--top", type=int, default=100)
    ap.add_argument("--out", default=None,
                    help="JSON artifact path (default "
                         "data/state/frame_rescreen/<slug>.rescreen.json)")
    args = ap.parse_args(argv)

    frame = load_frame(args.client)
    today = date.today()
    keeps, receipt = rescreen_store(frame, today=today)
    f_keeps, f_receipt = rescreen_forecasts(frame, today=today)

    ranked = sort_solicitable(keeps, today)
    top = ranked[:args.top]

    out_path = args.out or os.path.join(
        _OUT_DIR, f"{args.client}.rescreen.json")
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    from tools.artifacts import atomic_write_json
    atomic_write_json(out_path, {
        "client": args.client,
        "work_order": frame.get("work_order"),
        "generated_at": today.isoformat(),
        "keep_rule": frame.get("keep_rule"),
        "notice_receipt": receipt,
        "forecast_receipt": f_receipt,
        "notice_keeps": keeps,
        "forecast_keeps": f_keeps,
        "top_solicitable_ids": [k["notice_id"] for k in top],
    })

    print(f"frame re-screen · {args.client} · {today.isoformat()}")
    print(f"store: {receipt['store_rows']:,} notices scanned "
          f"(last ingest {receipt.get('store_last_ingest')}), "
          f"{receipt['prefilter_hits']:,} carried frame vocabulary, "
          f"{receipt['kept']:,} kept on the tier 1/2 rule "
          f"({receipt['kept_awards']:,} of them award-history evidence), "
          f"{receipt['tier3_only_dropped']:,} tier-3-only dropped, "
          f"{receipt['deduped_away']:,} family duplicates collapsed")
    print(f"artifact: {out_path}")
    print()
    print(f"TOP {len(top)} SOLICITABLE BY LEVERAGE "
          f"(live windows first; awards excluded)")
    print("=" * 78)
    for i, k in enumerate(top, 1):
        print(_fmt_notice(i, k))
        print()
    print(f"FORECAST KEEPS ({len(f_keeps)}) ACROSS WIRED SOURCES: "
          f"PRESS IMPACT {f_receipt.get('press_impact_live_window_keeps', 0)} "
          f"live-window candidate rows; "
          f"{f_receipt.get('kept_fy_only', 0)} fy-only (dated to FY end); "
          f"{f_receipt.get('kept_window_unstated', 0)} window-unstated; "
          f"{f_receipt.get('kept_stated_past', 0)} stated-past "
          f"(evidence, reclassified, never dropped)")
    print("=" * 78)
    for name, row in sorted((f_receipt.get("sources") or {}).items()):
        print(f"  {name}: {row.get('records', '?')} records, "
              f"{row.get('kept', 0)} kept"
              + (f" ({row['error']})" if "error" in row else ""))
    if f_receipt.get("storeless_adapters"):
        print(f"  registered but storeless (never pulled here): "
              f"{', '.join(f_receipt['storeless_adapters'])}")
    print()
    for i, k in enumerate(f_keeps, 1):
        print(_fmt_forecast(i, k))
        print()
    return 0


if __name__ == "__main__":
    sys.exit(main())
