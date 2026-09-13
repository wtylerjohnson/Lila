"""Developing Horizon — pattern-scoped forming opportunities, shown-work only.

The zero-pursuit problem this solves: a cycle with no pursue-grade notice still
needs a defensible forward map. Each horizon item is an inference chain the
reader can audit:

    VERIFIED   cited rows drawn ONLY from the fact bank this module assembles
               (SAM monitor notices, the recompete calendar, forecast screens,
               regulatory events, teaming evidence) — never composed facts
    PATTERN    the inference, stated plainly
    PROJECTION explicitly labeled GTM Group analyst judgment, with its window
    WATCHING   the monitoring trigger + next-review cadence

Compose and refine run through the DecisionEngine (Max-plan routed). The
refine loop is a DIALOGUE gate: the operator issues instructions and the set
re-deliberates, but a hard validator re-checks every round that no signal
cites a source outside the fact bank — an instruction that would require
invention comes back unchanged with a note instead. Only an APPROVED horizon
set ever renders into a deliverable.
"""

from __future__ import annotations

import json
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional
from urllib.parse import urlparse

from pydantic import BaseModel, Field, ValidationError

from agents.assess.binding import binding_drift, build_binding
from agents.assess.contracts import LifecycleStage

_ROOT = Path(__file__).resolve().parents[2]
_REVIEW_DIR = _ROOT / "data" / "review"
HORIZON_SCHEMA_VERSION = 2
_UNSET = object()

# nouns the count-reconciliation lint classifies (agents/reports/lint.py);
# composed horizon prose must not freewrite numbers against them — the PDF
# boundary runs full lint_counts and a bare "three notices" fails the build
_GUARDED_NOUNS = ("pursuit", "opportunit", "notice", "posting", "solicitation",
                  "competitor", "dossier", "agenc", "watchlist")
_GUARDED_CLAIM = re.compile(
    r"\b(?:\d{1,3}|one|two|three|four|five|six|seven|eight|nine|ten|eleven|"
    r"twelve|thirteen|fourteen|fifteen|sixteen|seventeen|eighteen|nineteen|"
    r"twenty)\b(?:\s+[\w\-]+){0,3}?\s+(?:" + "|".join(_GUARDED_NOUNS) + r")",
    re.I)

# Coverage describes what was screened. It can qualify a thesis that also has
# affirmative evidence, but it cannot create a forming procurement by itself.
_COVERAGE_ONLY_KINDS = frozenset({"forecast_screen"})


def _official_source_problem(fact: dict) -> Optional[str]:
    """Return the source-policy failure for one citable fact, if any.

    Horizon is promoted government-market intelligence, not general web
    discovery. SAM monitor rows must resolve to SAM itself; every other fact
    that can support a thesis must use an HTTPS government or military host.
    Client-known rows remain non-citable under the separate gate below, but
    they still retain the same official-source hygiene in the stored bank.
    """
    source = str(fact.get("source") or "").strip()
    kind = str(fact.get("kind") or "").strip()
    try:
        parsed = urlparse(source)
    except ValueError:
        parsed = None
    scheme = (parsed.scheme or "").lower() if parsed else ""
    host = (parsed.hostname or "").lower() if parsed else ""
    if scheme != "https" or not host:
        return "source must use HTTPS"
    if kind == "sam_monitor":
        if host != "sam.gov" and not host.endswith(".sam.gov"):
            return "SAM monitor source must resolve to SAM.gov"
        return None
    if not (host.endswith(".gov") or host.endswith(".mil")):
        return "government fact source must resolve to an HTTPS .gov or .mil host"
    return None


def current_horizon_binding(client_name: str, *, sweep_path: Optional[str] = None,
                            sweep: Any = _UNSET,
                            profile: Any = _UNSET) -> dict:
    """Fingerprint the exact operator-designated sweep and capability profile.

    The result is deterministic and contains no timestamps, so the same inputs
    retain approval while any scope, sweep evidence, or profile change reopens
    the gate.
    """
    from agents.review import gate_designator, sweep_artifact_path
    from tools.capability import load_profile

    path = sweep_path or sweep_artifact_path(client_name)
    active_sweep = sweep
    if active_sweep is _UNSET:
        with open(path, encoding="utf-8") as f:
            active_sweep = json.load(f)
    active_profile = load_profile(client_name) if profile is _UNSET else profile
    if active_profile is None:
        raise ValueError("current capability profile is missing")
    return build_binding(
        scope_designator=gate_designator(client_name) or "all",
        sweep_artifact=os.path.basename(path),
        sweep=active_sweep, profile=active_profile)


class HorizonSignal(BaseModel):
    evidence_id: Optional[str] = Field(
        default=None,
        description="the cited fact-bank H# id; required for Horizon v2")
    text: str = Field(description="one verified fact, plainly stated with its "
                                  "date/number, copied faithfully from the fact bank")
    source: str = Field(description="the fact's source string EXACTLY as given "
                                    "in the fact bank — never altered, never invented")


class HorizonItem(BaseModel):
    id: str = Field(description="short kebab slug, e.g. 'cbp-amo-subscriptions'")
    title: str
    where: str = Field(description="agency / office the window forms at")
    verified: list[HorizonSignal] = Field(min_length=1, max_length=6)
    pattern: str = Field(description="the inference chain across the verified "
                                     "signals, stated plainly")
    projection: str = Field(description="where/when a window forms — written as "
                                        "explicit analyst judgment, never as fact")
    window: str = Field(description="projected timeframe, e.g. 'next 6-12 months'")
    watching: str = Field(description="the monitoring trigger + next-review "
                                      "cadence, e.g. 'new CBP AMO notice in "
                                      "these lanes; reviewed weekly'")
    confidence: str = Field(description="'strong' | 'moderate' | 'early'")
    lifecycle_stage: Optional[LifecycleStage] = Field(
        default=None,
        description="predicted procurement lifecycle stage; required in v2")
    falsifier: Optional[str] = Field(
        default=None,
        description="observable condition that would disprove the projection; "
                    "required in v2")
    watch_trigger: Optional[str] = Field(
        default=None,
        description="specific observable event to monitor; required in v2")
    monitoring_cadence: Optional[str] = Field(
        default=None,
        description="explicit review cadence; required in v2")


class HorizonSet(BaseModel):
    client_name: str
    items: list[HorizonItem] = Field(default_factory=list, max_length=6)
    method_note: str = Field(description="one line: what was screened to build "
                                         "this (record counts by source)")
    refine_note: str = Field(default="", description="set by a refine round when "
                                                     "an instruction could not be "
                                                     "honored without inventing data")


_SYSTEM = """You scope FORMING federal opportunities for GTM Group; pattern work over verified signals, for a client deliverable. Rules, non-negotiable:
- Use ONLY facts from the provided fact_bank. Copy every verified signal's evidence_id, text, AND source EXACTLY from one fact-bank row. If the bank cannot support an item, do not write the item. NEVER invent a notice, date, dollar figure, or source.
- Facts marked "client footprint (INTERNAL...)" are the client's OWN history. They are reasoning fuel ONLY: let them tell you which patterns are real, which buyers are proven, how to rank and set confidence; but NEVER cite them as signals, never restate them in item text, and never make the client's own contracts an item's subject. The client knows their own business; this map tells them ONLY what they don't know: buyer behavior, forming windows, competitor and teaming motion.
- Structure per item: verified signals (the evidence), pattern (the inference across them), projection (your judgment on where/when a window forms; it may be speculative, but it must follow from the cited signals), lifecycle_stage, window, falsifier, watch_trigger, and monitoring_cadence. Also return watching for the current renderer; the application deterministically replaces it with "<watch_trigger>; <monitoring_cadence>".
- The projection is ANALYST JUDGMENT and must read as such ("we judge", "the pattern points to"), never as an announced fact.
- 0-6 items, strongest evidence first. Return zero items when the fact bank cannot support a defensible forming opportunity; never fill a quota. confidence: 'strong' (repeated behavior or dated recompete), 'moderate' (single strong signal + context), 'early' (directional).
- Forecast screen counts, coverage gaps, and the absence of capability matches are coverage context only. They may accompany affirmative evidence, but they never support a forming-opportunity thesis by themselves and absence is not evidence that demand is forming.
- NEVER write a number directly against the words notice(s), solicitation(s), opportunity/-ies, pursuit(s), competitor(s), agency/-ies, dossier(s), or watchlist; those counts are machine-reconciled elsewhere. Say 'sole-source buys', 'award records', 'signals', 'planning records' instead.
- White-label: GTM Group analysis only; no tooling, model, or vendor-of-analysis references.
- PUNCTUATION: never use an em dash anywhere; use commas, colons, periods, or middots instead (house style, machine-enforced).
- method_note: one plain line stating what was screened (use the record counts given in the fact bank header).
- High signal, zero padding. A reader should finish each item knowing exactly why effort belongs there."""

_REFINE_SYSTEM = _SYSTEM + """

You are REFINING an existing horizon set under an operator instruction. Apply
the instruction faithfully; reorder, reframe, sharpen, drop, or merge items as
asked. HARD LIMIT: you may not add a signal whose source is not in the
fact_bank, and you may not strengthen a projection beyond what the cited
signals support. If the instruction requires either, leave the affected
content unchanged and explain why in refine_note (one sentence, plain). Items
the instruction does not touch pass through unchanged."""


# ── fact bank ────────────────────────────────────────────────────────────────

def _fact_scope(*, agency: Any = None, component: Any = None,
                agencies: Any = None) -> dict:
    """Canonical buyer boundary retained with every citable fact.

    A missing buyer is not silently treated as applicable to every agency. It
    is explicitly government-wide so a focused strict projection can decide
    whether the row is context or sufficiently buyer-bound evidence.
    """
    values: list[str] = []
    for raw in (agencies if isinstance(agencies, (list, tuple)) else [],
                [agency]):
        for value in raw:
            clean = str(value or "").strip()
            if clean and clean not in values:
                values.append(clean)
    clean_component = str(component or "").strip()
    if not values:
        return {"kind": "government_wide"}
    scope = {"kind": "agency", "agencies": values}
    if clean_component:
        scope["component"] = clean_component
    return scope


def _bank_entry(facts: list, text: str, source: Optional[str], kind: str,
                *, agency: Any = None, component: Any = None,
                agencies: Any = None, source_record_id: Any = None,
                retrieved_at: Any = None, data_as_of: Any = None) -> None:
    if not source or not text:
        return
    row = {"id": f"H{len(facts) + 1}", "text": text.strip(),
           "source": str(source).strip(), "kind": kind,
           "scope": _fact_scope(
               agency=agency, component=component, agencies=agencies)}
    if source_record_id:
        row["source_record_id"] = str(source_record_id).strip()
    if retrieved_at:
        row["retrieved_at"] = retrieved_at
    if data_as_of:
        row["data_as_of"] = str(data_as_of).strip()
    facts.append(row)


def _append_program_rows(facts: list, rows: list[dict]) -> None:
    """Assign canonical H ids to factory rows without re-deriving evidence."""
    for row in rows:
        facts.append({"id": f"H{len(facts) + 1}", **row})


def build_fact_bank(results: dict, triage: Optional[dict] = None, *,
                    retrieved_at: Optional[str] = None) -> list[dict]:
    """Every fact the composer may cite, each with its source. Deterministic,
    tolerant of missing layers — an absent source contributes nothing rather
    than failing the build."""
    facts: list[dict] = []
    triage = triage or (results.get("triage") or {})
    from agents.reports.horizon_discovery import pull_official_horizon_sources
    official = pull_official_horizon_sources(results)

    # Monitor verdict selects which authoritative SAM rows enter the bank, but
    # the model's reason is analysis, not notice evidence. Keep the citable row
    # limited to fields the notice itself supplied. If ranking context later
    # needs the reason, it must travel outside the citable fact bank.
    for n in results.get("sam.gov") or []:
        v = triage.get(n.get("source_id")) or {}
        if v.get("verdict") != "monitor":
            continue
        rp = n.get("raw_payload") or {}
        url = rp.get("url") or n.get("api_url")
        bits = [f"{rp.get('type', 'notice')}: \"{n.get('title', '')}\"",
                n.get("agency") or ""]
        if n.get("response_deadline"):
            bits.append(f"response {n['response_deadline']}")
        _bank_entry(
            facts, " · ".join(b for b in bits if b), url, "sam_monitor",
            agency=(n.get("agency") or rp.get("fullParentPathName")
                    or rp.get("agency")),
            component=(rp.get("subtier") or rp.get("subTier")
                       or rp.get("component")),
            source_record_id=n.get("source_id"),
            retrieved_at=n.get("retrieved_at") or rp.get("retrieved_at"),
            data_as_of=n.get("posted_date") or rp.get("postedDate"),
        )

    # recompete calendar: awards in the lanes ending inside the window
    ex = results.get("expiring_awards") or {}
    for r in (ex.get("rows") or [])[:30]:
        amt = r.get("amount")
        amt_s = f" (${amt:,.0f})" if isinstance(amt, (int, float)) else ""
        _bank_entry(
            facts,
            f"award to {r.get('recipient')} at "
            f"{r.get('awarding_sub_agency') or r.get('awarding_agency')} ends "
            f"{r.get('end_date')}{amt_s} · NAICS {r.get('naics')}",
            r.get("url"), "expiring_award",
            agency=r.get("awarding_agency"),
            component=r.get("awarding_sub_agency"),
            source_record_id=r.get("award_id"),
            retrieved_at=r.get("retrieved_at"),
            data_as_of=r.get("data_as_of") or r.get("end_date"))

    # forecast screen: coverage context. Presence and absence are observable
    # screening results, but neither creates a forming-opportunity thesis. The
    # validator permits this row only alongside affirmative evidence.
    fc = results.get("forecast_signals") or {}
    if fc.get("total_records"):
        matched = fc.get("matched") or []
        from tools.api.source_catalog import (
            forecast_source_label, forecast_source_url,
        )
        source_summaries = [
            row for row in (fc.get("sources") or [])
            if isinstance(row, dict) and row.get("source")
        ]
        if not source_summaries:
            # Legacy forecast_signals artifacts predate source summaries and
            # were APFS-only. New multi-source sweeps always carry sources.
            source_summaries = [{
                "source": "dhs_apfs",
                "label": forecast_source_label("dhs_apfs"),
                "url": forecast_source_url("dhs_apfs"),
                "record_count": fc["total_records"],
            }]
        for summary in source_summaries:
            identity = str(summary.get("source") or "")
            source_count = summary.get("record_count")
            if not isinstance(source_count, int):
                source_count = fc["total_records"]
            source_matches = [
                row for row in matched
                if str((row.get("record") or row).get("source") or "")
                == identity
            ]
            if len(source_summaries) == 1:
                text = (
                    fc.get("screen_line")
                    or (
                        f"Agency acquisition planning screen: {source_count} "
                        f"published planning records reviewed; "
                        f"{len(source_matches)} matched the client's "
                        f"capability vocabulary"
                    )
                )
            else:
                text = (
                    f"{summary.get('label') or forecast_source_label(identity)} "
                    f"screen: {source_count} published planning records "
                    f"reviewed; {len(source_matches)} matched the client's "
                    f"capability vocabulary"
                )
            _bank_entry(
                facts, text,
                summary.get("url") or forecast_source_url(identity),
                "forecast_screen",
                agencies=fc.get("scope_agencies"),
                retrieved_at=summary.get("retrieved_at"),
                data_as_of=summary.get("data_as_of"),
            )
        # Store change events come through the signals-only factory. A
        # disappeared line is only an observed source change and never implies
        # that a solicitation posted.
        _append_program_rows(
            facts, official.get("forecast_store_changes") or [])
        # only capability-relevant lines are citable horizon facts; lane-only
        # matches (broad-NAICS noise like Oracle maintenance in 541519) stay
        # in the screen count and never become quotable "stated intent"
        capability_matches = [m for m in (fc.get("matched") or [])
                              if m.get("keyword_hits") or any(
                                  str(x).startswith("keywords:")
                                  for x in (m.get("reasons") or []))]
        for m in capability_matches[:10]:
            r = m.get("record") or m
            identity = str(r.get("source") or "")
            source_label = forecast_source_label(identity)
            _bank_entry(
                facts,
                f'{source_label} line: "{r.get("title", "")}" · '
                f"{r.get('component') or ''} · anticipated "
                f"{r.get('anticipated_solicitation') or 'timing not stated'} · "
                f"{r.get('estimated_value_range') or ''}",
                r.get("url"), "forecast_line",
                agency=r.get("agency"), component=r.get("component"),
                source_record_id=r.get("source_id"),
                retrieved_at=r.get("retrieved_at"),
                data_as_of=r.get("data_as_of"))
    elif official.get("forecast_store_changes"):
        # Change events remain citable even when a legacy payload omitted the
        # aggregate screen count. The adapter still requires an official URL.
        _append_program_rows(
            facts, official["forecast_store_changes"])

    # the client's OWN federal delivery — INTERNAL REASONING ONLY. The
    # footprint tells US which patterns are real (proven category, proven
    # buyer) and how to rank/confidence-set items; it is NEVER told back to
    # the client, who knows their own business. client_known entries may not
    # be cited as signals and their award ids may not appear in item text —
    # validate_horizon enforces both deterministically.
    ca = results.get("client_awards") or {}
    for r in (ca.get("rows") or [])[:12]:
        amt = r.get("amount")
        amt_s = f" (${amt:,.0f})" if isinstance(amt, (int, float)) else ""
        _bank_entry(
            facts,
            f"client footprint (INTERNAL: informs judgment, never restated): "
            f"{r.get('award_id')} at "
            f"{r.get('awarding_sub_agency') or r.get('awarding_agency')}"
            f"{amt_s}, period {r.get('start_date')} → {r.get('end_date')}"
            + (f" · {str(r.get('description'))[:90]}" if r.get("description") else ""),
            r.get("url"), "client_past_performance",
            agency=r.get("awarding_agency"),
            component=r.get("awarding_sub_agency"),
            source_record_id=r.get("award_id"),
            retrieved_at=r.get("retrieved_at"),
            data_as_of=r.get("data_as_of") or r.get("end_date"))
        facts[-1]["client_known"] = True
        if r.get("award_id"):
            facts[-1]["award_ref"] = str(r["award_id"])

    # the DoD award wire: who is winning in the lanes right now — fresh,
    # keyword-matched contract announcements with the match recorded
    dw = results.get("dod_contracts") or {}
    for it in (dw.get("items") or [])[:10]:
        _bank_entry(
            facts,
            f"DoD award announcement ({it.get('published', '')}): "
            f"{it.get('title', '')} · {str(it.get('body', ''))[:140]} "
            f"[matched: {', '.join(it.get('matched') or [])}]",
            it.get("url"), "dod_award_wire",
            agency="Department of Defense",
            component=it.get("component"),
            source_record_id=it.get("record_id") or it.get("id"),
            retrieved_at=it.get("retrieved_at"),
            data_as_of=it.get("data_as_of") or it.get("published"))

    # Official budget and watchdog rows are mapped by registered adapters.
    _append_program_rows(facts, official.get("budget_pressure") or [])
    _append_program_rows(facts, official.get("watchdogs") or [])

    # money forming: capability-matched bills, money vehicles flagged
    # Normal sweeps persist this collector under ``congress``. Read the older
    # ``congress_gov`` artifact key only when the canonical key is absent;
    # an explicit current empty/error result must not resurrect stale legacy
    # evidence from a mixed artifact.
    cg = (results.get("congress") if "congress" in results
          else results.get("congress_gov"))
    cg = cg if isinstance(cg, dict) else {}
    for it in (cg.get("items") or [])[:8]:
        veh = f" [{it['money_vehicle']}]" if it.get("money_vehicle") else ""
        _bank_entry(
            facts,
            f"Congress: {it.get('bill', '')} \"{it.get('title', '')[:110]}\"{veh} · "
            f"latest action {it.get('action_date', '')}: "
            f"{str(it.get('latest_action', ''))[:90]} "
            f"[matched: {', '.join(it.get('matched') or [])}]",
            it.get("url"), "legislation", agency=it.get("agency"),
            agencies=it.get("agencies"), component=it.get("component"),
            source_record_id=it.get("bill") or it.get("id"),
            retrieved_at=it.get("retrieved_at"),
            data_as_of=it.get("data_as_of") or it.get("action_date"))

    # New sources join the same fact bank without changing its validation or
    # approval gate. Federal Register stays last to preserve existing H ids
    # whenever the new Regulations.gov and CISA payloads are absent.
    _append_program_rows(facts, official.get("regulations_gov") or [])
    _append_program_rows(facts, official.get("cisa_kev") or [])
    _append_program_rows(facts, official.get("federal_register") or [])
    # Stored PROGRAM sources append in their stable source-catalog order.
    # Their factory mappers preserve the official record identity and never
    # touch the live-solicitation collection path.
    _append_program_rows(facts, official.get("dsip_topics") or [])
    _append_program_rows(
        facts, official.get("reginfo_unified_agenda") or [])
    _append_program_rows(facts, official.get("darpa_opportunities") or [])
    _append_program_rows(facts, official.get("dod_budget_exhibits") or [])
    _append_program_rows(facts, official.get("foreign_assistance") or [])
    # These legacy collectors gained the same PROGRAM seam later. Append them
    # so existing H ids remain stable for unchanged earlier source rows.
    _append_program_rows(facts, official.get("grants_gov") or [])
    _append_program_rows(facts, official.get("sbir_gov") or [])

    # Preserve the closest record/source retrieval stamp. The compose-time
    # capture moment is a legacy fallback only, never an overwrite.
    fallback_retrieval = (
        retrieved_at
        or datetime.now(timezone.utc).isoformat(timespec="seconds")
    )
    for fact in facts:
        fact["retrieved_at"] = _normalized_retrieval(
            fact.get("retrieved_at") or fallback_retrieval)
    return facts


def _normalized_retrieval(value: Any) -> str:
    """Timezone-aware UTC ISO stamp; invalid source metadata fails named."""
    try:
        if isinstance(value, datetime):
            parsed = value
        else:
            parsed = datetime.fromisoformat(
                str(value).strip().replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc).isoformat(timespec="seconds")
    except (AttributeError, TypeError, ValueError) as exc:
        raise ValueError(
            "Horizon fact bank requires a valid retrieval timestamp") from exc


# ── compose / validate / refine ──────────────────────────────────────────────

_V2_ITEM_FIELDS = ("lifecycle_stage", "falsifier", "watch_trigger",
                   "monitoring_cadence")


def _payload_schema_version(payload: dict) -> int:
    raw = payload.get("schema_version", 1)
    if type(raw) is not int or raw not in (1, HORIZON_SCHEMA_VERSION):
        raise ValueError(f"unsupported Horizon schema_version {raw!r}")
    return raw


def _materialize_v2_render_fields(hset: HorizonSet) -> HorizonSet:
    """Keep the established renderer field, with split v2 semantics authoritative."""
    for item in hset.items:
        if item.watch_trigger and item.monitoring_cadence:
            item.watching = f"{item.watch_trigger}; {item.monitoring_cadence}"
    return hset


def _serialized_set(hset: HorizonSet, schema_version: int) -> dict:
    """Serialize without adding nullable v2 fields to a refined v1 artifact."""
    data = json.loads(hset.model_dump_json())
    if schema_version == 1:
        for item in data.get("items") or []:
            for field in _V2_ITEM_FIELDS:
                item.pop(field, None)
            for signal in item.get("verified") or []:
                signal.pop("evidence_id", None)
    return data


def validate_horizon(hset: HorizonSet, bank: list[dict], *,
                     schema_version: int = 1,
                     expected_client_name: Optional[str] = None) -> list[str]:
    """Deterministic gates shared by compose, approval, and report loading."""
    if schema_version not in (1, HORIZON_SCHEMA_VERSION):
        return [f"unsupported Horizon schema_version {schema_version!r}"]
    v2 = schema_version == HORIZON_SCHEMA_VERSION
    problems: list[str] = []
    if (expected_client_name is not None
            and hset.client_name != expected_client_name):
        problems.append(
            "horizon set client_name does not match artifact client "
            f"('{hset.client_name}' != '{expected_client_name}')")

    item_ids: dict[str, int] = {}
    for index, item in enumerate(hset.items):
        item_id = item.id.strip()
        if not item_id:
            problems.append(f"horizon item at index {index} has no id")
            continue
        if item_id in item_ids:
            problems.append(f"horizon item id '{item_id}' is duplicated")
        else:
            item_ids[item_id] = index

    by_id: dict[str, list[dict]] = {}
    for i, fact in enumerate(bank):
        if not isinstance(fact, dict):
            problems.append(f"fact-bank row {i} is not an object")
            continue
        source_problem = _official_source_problem(fact)
        if source_problem:
            problems.append(f"fact-bank row {i} {source_problem}")
        if (fact.get("kind") == "sam_monitor"
                and re.search(r"\btriage\s*:",
                              str(fact.get("text") or ""), re.I)):
            problems.append(
                f"fact-bank row {i} SAM monitor includes non-source triage "
                "analysis")
        if v2 and hset.items:
            fid = fact.get("id") if isinstance(fact, dict) else None
            if not isinstance(fid, str) or not fid.strip():
                problems.append(f"fact-bank row {i} has no evidence id")
            else:
                by_id.setdefault(fid, []).append(fact)
        retrieved = fact.get("retrieved_at")
        if not isinstance(retrieved, str) or not retrieved.strip():
            if v2 and hset.items:
                problems.append(f"fact-bank row {i} has no retrieved_at")
        else:
            try:
                parsed = datetime.fromisoformat(
                    retrieved.strip().replace("Z", "+00:00"))
                if parsed.tzinfo is None or parsed.utcoffset() is None:
                    problems.append(
                        f"fact-bank row {i} retrieved_at must be timezone-aware")
            except ValueError:
                problems.append(
                    f"fact-bank row {i} has invalid retrieved_at '{retrieved}'")
    if v2 and hset.items:
        for fid, rows in by_id.items():
            if len(rows) > 1:
                problems.append(f"fact-bank evidence id '{fid}' is duplicated")

    valid_bank = [fact for fact in bank if isinstance(fact, dict)]
    allowed = {f["source"] for f in valid_bank if "source" in f}
    by_claim: dict[tuple[str, str], list[dict]] = {}
    for fact in valid_bank:
        key = (str(fact.get("source") or ""),
               re.sub(r"\s+", " ", str(fact.get("text") or "").strip()))
        by_claim.setdefault(key, []).append(fact)
    # client-known facts (the client's own footprint) are internal reasoning
    # only — citing the client back to themselves is as hard-blocked as
    # fabrication: the map tells them only what they DON'T know
    known_sources = {f["source"] for f in valid_bank
                     if f.get("client_known") and "source" in f}
    known_refs = {f["award_ref"] for f in valid_bank
                  if f.get("client_known") and f.get("award_ref")}
    for item in hset.items:
        if v2:
            for field in _V2_ITEM_FIELDS:
                value = getattr(item, field)
                if value is None or (isinstance(value, str) and not value.strip()):
                    problems.append(
                        f"item '{item.id}': Horizon v2 requires {field}")
        resolved_groups: list[list[dict]] = []
        for s in item.verified:
            if s.source in known_sources:
                problems.append(
                    f"item '{item.id}': cites the client back to themselves "
                    f"('{s.text[:60]}') — the footprint informs judgment, it "
                    f"is never told to the client")
                continue
            if s.source not in allowed:
                problems.append(
                    f"item '{item.id}': signal cites a source outside the fact "
                    f"bank — '{s.source[:80]}'")
                continue
            key = (s.source, re.sub(r"\s+", " ", s.text.strip()))
            if v2:
                if not s.evidence_id:
                    problems.append(
                        f"item '{item.id}': Horizon v2 signal requires evidence_id")
                    continue
                rows = by_id.get(s.evidence_id) or []
                if not rows:
                    problems.append(
                        f"item '{item.id}': evidence_id '{s.evidence_id}' is not "
                        "in the fact bank")
                    continue
                if len(rows) > 1:
                    problems.append(
                        f"item '{item.id}': evidence_id '{s.evidence_id}' is "
                        "ambiguous")
                    continue
                fact = rows[0]
                fact_key = (str(fact.get("source") or ""),
                            re.sub(r"\s+", " ",
                                   str(fact.get("text") or "").strip()))
                if fact_key != key:
                    problems.append(
                        f"item '{item.id}': signal does not exactly match "
                        f"fact-bank row '{s.evidence_id}'")
                    continue
                resolved_groups.append(rows)
            else:
                matches = by_claim.get(key) or []
                if not matches:
                    problems.append(
                        f"item '{item.id}': signal text does not exactly match the "
                        f"fact bank row at '{s.source[:80]}'")
                    continue
                resolved_groups.append(matches)
        if (len(resolved_groups) == len(item.verified)
                and not any(any(f.get("kind") not in _COVERAGE_ONLY_KINDS
                                for f in group)
                            for group in resolved_groups)):
            problems.append(
                f"item '{item.id}': forecast-screen coverage is the only "
                "evidence; coverage or absence cannot support a forming-"
                "opportunity thesis by itself")
        prose_fields = ["title", "pattern", "projection", "watching"]
        if v2:
            prose_fields += ["falsifier", "watch_trigger", "monitoring_cadence"]
        for field in prose_fields:
            text = getattr(item, field)
            if text is None:
                continue
            hit = next((r for r in known_refs if r and r in text), None)
            if hit:
                problems.append(
                    f"item '{item.id}': {field} restates the client's own "
                    f"award {hit} — they know their own business; say only "
                    f"what they don't know")
        for field in prose_fields:
            text = getattr(item, field)
            if not text:
                continue
            m = _GUARDED_CLAIM.search(text)
            if m:
                problems.append(
                    f"item '{item.id}': freewritten count claim "
                    f"'{m.group(0)}' in {field} — machine-reconciled nouns "
                    f"need rephrasing (say 'buys'/'award records'/'signals')")
        if item.confidence not in ("strong", "moderate", "early"):
            problems.append(f"item '{item.id}': confidence "
                            f"'{item.confidence}' not in strong|moderate|early")
        # house style: no em dashes in composed copy (the render normalizes as
        # a last resort, but the composer should converge on clean punctuation)
        for field in prose_fields:
            text = getattr(item, field)
            if text and "—" in text:
                problems.append(f"item '{item.id}': em dash in {field}; house "
                                "style bans it (use comma, colon, or middot)")
    return problems


def compose_horizon(client_name: str, results: dict, engine=None, *,
                    bank: Optional[list[dict]] = None,
                    retrieved_at: Optional[str] = None) -> tuple:
    """One compose + one violation-fed retry. Returns (HorizonSet, bank,
    problems) — a non-empty problems list means the caller must not ship."""
    bank = (build_fact_bank(results, retrieved_at=retrieved_at)
            if bank is None else bank)
    if not bank:
        return (HorizonSet(
                    client_name=client_name,
                    items=[],
                    method_note=("Scoped Horizon screen completed without "
                                 "citable inputs.")),
                bank, [])
    from agents.decisions.engine import DecisionEngine
    engine = engine or DecisionEngine()
    context = {"client_name": client_name, "fact_bank": bank,
               "schema_version": HORIZON_SCHEMA_VERSION}
    hset = engine.deliberate(layer="horizon", system_prompt=_SYSTEM,
                             context=context, schema=HorizonSet)
    hset = _materialize_v2_render_fields(hset)
    problems = validate_horizon(
        hset, bank, schema_version=HORIZON_SCHEMA_VERSION,
        expected_client_name=client_name)
    if problems:
        context["instruction"] = ("VALIDATION FAILED on first pass — fix "
                                  "exactly these and change nothing else: "
                                  + "; ".join(problems))
        hset = engine.deliberate(layer="horizon", system_prompt=_SYSTEM,
                                 context=context, schema=HorizonSet)
        hset = _materialize_v2_render_fields(hset)
        problems = validate_horizon(
            hset, bank, schema_version=HORIZON_SCHEMA_VERSION,
            expected_client_name=client_name)
    return hset, bank, problems


def refine_horizon(payload: dict, instruction: str, engine=None, *,
                   current_binding: Optional[dict] = None) -> dict:
    """One refine round of the dialogue gate. Mutates and returns the payload
    (still a DRAFT — approval is a separate, human act). A round that fails
    validation twice keeps the prior set and records the failure loudly."""
    binding_problems = horizon_binding_problems(payload, current_binding)
    if binding_problems:
        raise HorizonValidationError(binding_problems)
    try:
        schema_version = _payload_schema_version(payload)
    except ValueError as exc:
        raise HorizonValidationError([str(exc)]) from exc
    from agents.decisions.engine import DecisionEngine
    engine = engine or DecisionEngine()
    bank = payload["fact_bank"]
    context = {"client_name": payload["client"],
               "current_set": payload["set"],
               "fact_bank": bank,
               "schema_version": schema_version,
               "instruction": instruction}
    hset = engine.deliberate(layer="horizon-refine", system_prompt=_REFINE_SYSTEM,
                             context=context, schema=HorizonSet)
    if schema_version == HORIZON_SCHEMA_VERSION:
        hset = _materialize_v2_render_fields(hset)
    problems = validate_horizon(
        hset, bank, schema_version=schema_version,
        expected_client_name=payload["client"])
    if problems:
        context["instruction"] = (instruction + " — YOUR PREVIOUS ATTEMPT "
                                  "FAILED VALIDATION: " + "; ".join(problems)
                                  + ". Fix these without inventing sources.")
        hset = engine.deliberate(layer="horizon-refine",
                                 system_prompt=_REFINE_SYSTEM,
                                 context=context, schema=HorizonSet)
        if schema_version == HORIZON_SCHEMA_VERSION:
            hset = _materialize_v2_render_fields(hset)
        problems = validate_horizon(
            hset, bank, schema_version=schema_version,
            expected_client_name=payload["client"])
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    if problems:
        payload["rounds"].append({"instruction": instruction, "at": now,
                                  "applied": False,
                                  "note": "REJECTED — " + "; ".join(problems)})
    else:
        payload["set"] = _serialized_set(hset, schema_version)
        payload["rounds"].append({"instruction": instruction, "at": now,
                                  "applied": True,
                                  "note": hset.refine_note or ""})
        # an edit AFTER approval reopens the gate: only what a human last
        # approved may render, so any applied change demands a fresh approval
        payload["status"] = "draft"
        payload.pop("approved_at", None)
        payload.pop("approved_by", None)
    return payload


# ── persistence (data/review — gitignored, like every operational artifact) ──

def _slug(name: str) -> str:
    from tools.slug import client_slug
    return client_slug(name)


def horizon_path(client_name: str) -> Path:
    return _REVIEW_DIR / f"{_slug(client_name)}.horizon.json"


def load_horizon(client_name: str) -> Optional[dict]:
    p = horizon_path(client_name)
    if not p.exists():
        return None
    try:
        payload = json.loads(p.read_text(encoding="utf-8"))
        return payload if isinstance(payload, dict) else None
    except (OSError, json.JSONDecodeError):
        return None


def save_horizon(payload: dict) -> Path:
    p = horizon_path(payload["client"])
    from tools.artifacts import atomic_write_json
    return atomic_write_json(p, payload)


class HorizonValidationError(ValueError):
    """A persisted Horizon payload failed deterministic approval checks."""

    def __init__(self, problems: list[str]):
        self.problems = problems
        super().__init__("; ".join(problems))


def _validate_payload(payload: dict) -> tuple[Optional[HorizonSet], list[str]]:
    """Validate a persisted payload without changing its artifact schema."""
    try:
        schema_version = _payload_schema_version(payload)
    except (AttributeError, ValueError) as exc:
        return None, [str(exc)]
    client_name = payload.get("client")
    if not isinstance(client_name, str) or not client_name.strip():
        return None, ["horizon artifact client is missing or invalid"]
    status = payload.get("status")
    approved_at = payload.get("approved_at")
    approved_by = payload.get("approved_by")
    if status not in {"draft", "approved"}:
        return None, ["horizon review status is invalid"]
    if status == "approved":
        try:
            parsed_approval = datetime.fromisoformat(
                str(approved_at).replace("Z", "+00:00"))
            aware = (parsed_approval.tzinfo is not None
                     and parsed_approval.utcoffset() is not None)
        except (TypeError, ValueError):
            aware = False
        if not aware or not isinstance(approved_by, str) or not approved_by.strip():
            return None, [
                "approved Horizon requires a timezone-aware approval timestamp "
                "and reviewer identity"]
    elif approved_at is not None or approved_by is not None:
        return None, ["draft Horizon cannot carry approval metadata"]
    try:
        hset = HorizonSet.model_validate(payload.get("set") or {})
    except (AttributeError, TypeError, ValidationError) as exc:
        return None, [f"horizon set schema is invalid: {exc}"]
    bank = payload.get("fact_bank")
    if not isinstance(bank, list):
        return None, ["horizon fact_bank is missing or invalid"]
    malformed = [str(i) for i, fact in enumerate(bank)
                 if (not isinstance(fact, dict)
                     or not isinstance(fact.get("text"), str)
                     or not isinstance(fact.get("source"), str)
                     or not isinstance(fact.get("kind"), str))]
    if malformed:
        return None, ["horizon fact_bank has malformed rows at indexes: "
                      + ", ".join(malformed[:10])]
    try:
        return hset, validate_horizon(
            hset, bank, schema_version=schema_version,
            expected_client_name=client_name)
    except Exception as exc:  # noqa: BLE001 — invalid artifact, never a wall
        return None, [f"horizon validation could not read the artifact: {exc}"]


def horizon_binding_problems(payload: dict,
                             current_binding: Optional[dict] = None) -> list[str]:
    """Return binding failures against the current operator-owned run.

    Legacy artifacts intentionally have no binding. They remain readable but
    are never eligible for approval or rendering until recomposed.
    """
    stored = payload.get("binding") if isinstance(payload, dict) else None
    if current_binding is None and isinstance(stored, dict):
        try:
            current_binding = current_horizon_binding(payload.get("client") or "")
        except Exception as exc:  # noqa: BLE001 - gate must fail closed
            return [f"current Horizon binding unavailable: {exc}"]
    return binding_drift(stored, current_binding or {}, subject="horizon",
                         remedy="recompose it under the current scope and "
                                "capability profile")


def new_draft(client_name: str, hset: HorizonSet, bank: list[dict],
              binding: Optional[dict] = None) -> dict:
    if hset.client_name != client_name:
        raise HorizonValidationError([
            "horizon set client_name does not match artifact client "
            f"('{hset.client_name}' != '{client_name}')"])
    hset = _materialize_v2_render_fields(hset)
    payload = {"client": client_name, "status": "draft",
               "schema_version": HORIZON_SCHEMA_VERSION,
               "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
               "fact_bank": bank,
               "set": _serialized_set(hset, HORIZON_SCHEMA_VERSION),
               "rounds": []}
    if binding is not None:
        payload["binding"] = binding
    return payload


def approve_horizon(payload: dict, *, current_binding: Optional[dict] = None) -> dict:
    """The human act. Only an approved payload ever reaches a renderer."""
    _, problems = _validate_payload(payload)
    problems += horizon_binding_problems(payload, current_binding)
    if problems:
        raise HorizonValidationError(problems)
    payload["status"] = "approved"
    payload["approved_at"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
    payload["approved_by"] = "operator"
    return payload


def approved_set(client_name: str, *,
                 current_binding: Optional[dict] = None) -> Optional[dict]:
    """The render-side loader: the horizon set dict IF approved, else None.
    A draft NEVER renders — the dialogue gate is the whole point."""
    hset, _, _ = horizon_for_report(client_name,
                                    current_binding=current_binding)
    return hset


def horizon_for_report(client_name: str, *, current_binding: Optional[dict] = None
                       ) -> tuple[Optional[dict], str, list[str]]:
    """Load the optional report layer without ever blocking the base report.

    Returns ``(set, state, problems)``. Only a deterministically valid,
    explicitly approved set is returned. Missing, draft, unreadable, and
    invalid artifacts degrade to ``None`` and a diagnostic state.
    """
    p = horizon_path(client_name)
    payload = load_horizon(client_name)
    if payload is None:
        if p.exists():
            return None, "invalid", ["horizon artifact is unreadable"]
        return None, "missing", []
    if payload.get("status") != "approved":
        return None, "draft", []
    _, problems = _validate_payload(payload)
    problems += horizon_binding_problems(payload, current_binding)
    if problems:
        return None, "invalid", problems
    return payload.get("set"), "approved", []
