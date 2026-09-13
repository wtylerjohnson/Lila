"""Deterministic adapters and immutable persistence for one Assess run.

This module is additive to the established report path. It promotes only the
parts of the current sweep that satisfy the stricter Assess contracts and
records every downgrade or rejection in the persisted artifact. A failure to
materialize this sidecar must never prevent the existing assessment from being
built.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import tempfile
import unicodedata
import re
from contextlib import contextmanager
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Optional
from urllib.parse import parse_qs, unquote, urlparse

from agents.assess.source_clock import acquisition_clock, clock_instant, acquired_at

from agents.assess.contracts import (
    AssessRun,
    AssessScope,
    CoverageStatus,
    EvidenceKind,
    EvidenceRef,
    EvidenceStrength,
    EvidenceTier,
    EvidenceUse,
    GateStatus,
    LiveClassification,
    LiveRecommendation,
    LiveSolicitation,
    LiveSolicitationLedger,
    NoticeAttachment,
    IntelligenceStatus,
    OpportunityThesis,
    OpportunityThesisLedger,
    PartnerDirection,
    PartnerOpportunity,
    PartnerOpportunityLedger,
    ProjectedWindow,
    ScopeAgency,
    ScopeMode,
    SourceCoverage,
    SourceLane,
)

_ROOT = Path(__file__).resolve().parents[2]
_DEFAULT_STATE = _ROOT / "data" / "state" / "assess_runs"
_SCHEMA_VERSION = 2
_SUPPORTED_SCHEMAS = (1, 2)
_UNSET = object()
_LOG = logging.getLogger(__name__)


class AssessLedgerError(ValueError):
    """The current evidence cannot be represented by the strict ledger."""


def _slug(value: str) -> str:
    from tools.slug import client_slug
    return client_slug(value)


def _text(value: Any) -> str:
    return " ".join(unicodedata.normalize("NFKC", str(value or "")).split())


def _jsonable(value: Any) -> Any:
    if hasattr(value, "model_dump"):
        return value.model_dump(mode="json")
    return value


def _digest(value: Any) -> str:
    canonical = json.dumps(
        _jsonable(value), sort_keys=True, separators=(",", ":"),
        ensure_ascii=False, default=str,
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _parse_datetime(value: Any, *, fallback: Optional[datetime] = None,
                    require_timezone: bool = False) -> Optional[datetime]:
    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, str) and value.strip():
        try:
            parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
        except ValueError:
            return fallback
    else:
        return fallback
    if parsed.tzinfo is None:
        if require_timezone:
            return fallback
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _parse_date(value: Any) -> Optional[date]:
    if isinstance(value, date) and not isinstance(value, datetime):
        return value
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, str) and value.strip():
        try:
            return date.fromisoformat(value.strip()[:10])
        except ValueError:
            return None
    return None


def scope_from_sweep(searches: dict) -> AssessScope:
    """Translate the gate-recorded sweep scope without inferring a new one."""
    raw = searches.get("search_scope")
    if not isinstance(raw, dict):
        raise AssessLedgerError(
            "Assess sweep has no explicit, valid search scope")
    agencies = raw.get("agencies")
    if raw.get("all") is True:
        if agencies or raw.get("mode") == "focus":
            raise AssessLedgerError(
                "Assess sweep scope ambiguously mixes all-federal and agency focus")
        return AssessScope(mode=ScopeMode.ALL)
    if raw.get("mode") != "focus" or not isinstance(agencies, list) or not agencies:
        raise AssessLedgerError(
            "Assess sweep must explicitly declare all=true or a nonempty focus scope")
    rows = []
    for agency in agencies:
        if isinstance(agency, dict):
            name = _text(agency.get("name") or agency.get("abbr"))
            abbr = _text(agency.get("abbr")) or None
        else:
            name, abbr = _text(agency), None
        if not name:
            raise AssessLedgerError("Assess scope contains an empty agency")
        rows.append(ScopeAgency(name=name, abbr=abbr))
    return AssessScope(mode=ScopeMode.AGENCY, agencies=rows)


def _scope_designator(scope: AssessScope) -> str:
    if scope.mode == ScopeMode.ALL:
        return "all"
    parts = [_slug(a.abbr or a.name) for a in scope.agencies]
    return "agency_" + "_".join(parts)


def _agency_is_in_scope(value: Any, scope: AssessScope) -> bool:
    if scope.mode == ScopeMode.ALL:
        return True
    text_value = _text(value)
    if not text_value:
        return False
    try:
        from tools.agencies import find, matches_record
        for selected in scope.agencies:
            target = find(selected.abbr or selected.name)
            if target and matches_record(text_value, target):
                return True
            if target:
                abbr = _text(target.get("abbr"))
                if abbr and re.search(
                        rf"\b{re.escape(abbr)}\b", text_value, flags=re.I):
                    return True
    except Exception:  # noqa: BLE001 - fallback below remains fail-closed
        pass
    value_tokens = set(re.findall(r"[a-z0-9]+", text_value.lower()))
    return any(
        bool(selected_tokens and selected_tokens <= value_tokens)
        for selected in scope.agencies
        for selected_tokens in ({
            token for token in re.findall(
                r"[a-z0-9]+", _text(selected.name).lower())
            if token not in {"department", "agency", "the", "of", "us", "u", "s"}
        },)
    )


def _sam_url(value: Any) -> Optional[str]:
    raw = _text(value)
    if not raw:
        return None
    try:
        parsed = urlparse(raw)
    except ValueError:
        return None
    host = (parsed.hostname or "").lower()
    if parsed.scheme == "https" and (host == "sam.gov" or host.endswith(".sam.gov")):
        return raw
    return None


def _sam_url_matches_notice(value: Any, notice_id: str) -> bool:
    allowed = _sam_url(value)
    if not allowed or not notice_id:
        return False
    parsed = urlparse(allowed)
    target = _text(notice_id).lower()
    segments = [unquote(part).strip().lower()
                for part in parsed.path.split("/") if part.strip()]
    query_ids = [
        _text(value).lower()
        for key, values in parse_qs(parsed.query).items()
        if key.lower() in {"noticeid", "notice_id", "id"}
        for value in values
    ]
    return target in segments or target in query_ids


def _https_host(value: Any) -> str:
    try:
        parsed = urlparse(_text(value))
    except ValueError:
        return ""
    return (parsed.hostname or "").lower() if parsed.scheme == "https" else ""


def _official_government_source(value: Any) -> bool:
    host = _https_host(value)
    return bool(host and (host.endswith(".gov") or host.endswith(".mil")))


def _official_usaspending_source(value: Any) -> bool:
    host = _https_host(value)
    return host == "usaspending.gov" or host.endswith(".usaspending.gov")


def _notice_url(row: dict, notice_id: str) -> tuple[str, bool]:
    raw = row.get("raw_payload") if isinstance(row.get("raw_payload"), dict) else {}
    for value in (row.get("api_url"), raw.get("url"), raw.get("uiLink")):
        allowed = _sam_url(value)
        if allowed:
            return allowed, True
    # The canonical SAM notice route keeps an otherwise valid census record
    # traceable, but synthesized routing is never sufficient for BID_NOW.
    return f"https://sam.gov/opp/{notice_id}/view", False


def _raw_value(row: dict, *names: str) -> Any:
    raw = row.get("raw_payload") if isinstance(row.get("raw_payload"), dict) else {}
    for name in names:
        value = raw.get(name)
        if value not in (None, ""):
            return value
    return None


def _notice_type(row: dict) -> str:
    return _text(
        row.get("notice_type") or row.get("type")
        or _raw_value(row, "type", "baseType")
    )


def _solicitation_number(row: dict) -> Optional[str]:
    value = _raw_value(row, "solicitation", "solicitationNumber")
    return _text(value) or None


def _issuing_office(row: dict) -> Optional[str]:
    # Only an explicit issuing-office value is trustworthy enough to
    # namespace solicitation families. Department/component alone is not.
    value = _raw_value(
        row, "office", "officeName", "organizationId", "organization_id")
    explicit = _text(value)
    if explicit:
        return explicit
    hierarchy = _text(_raw_value(row, "fullParentPathName"))
    if not hierarchy:
        return None
    # SAM live payloads commonly encode department.component.office as a
    # dotted hierarchy. The final segment is usable only when it is not itself
    # a known department/component; otherwise merging by solicitation number
    # would collapse unrelated issuing offices.
    parts = [part.strip() for part in hierarchy.split(".") if part.strip()]
    if len(parts) < 2:
        return None
    candidate = _text(parts[-1])
    try:
        from tools.agencies import find
        if find(candidate):
            return None
    except Exception:  # noqa: BLE001 - unknown hierarchy fails closed below
        return None
    return candidate or None


def _component(row: dict) -> Optional[str]:
    value = _raw_value(row, "subtier", "subTier", "component")
    explicit = _text(value)
    hierarchy = _text(
        row.get("agency") or _raw_value(row, "fullParentPathName", "agency"))
    try:
        from tools.agencies import AGENCIES, find
        if explicit:
            resolved = find(explicit)
            if resolved and resolved.get("parent"):
                return _text(resolved.get("name"))
        haystack = f"{explicit} {hierarchy}".lower()
        candidates = []
        for agency in AGENCIES:
            if not agency.get("parent"):
                continue
            abbr = _text(agency.get("abbr"))
            aliases = [agency.get("name") or "", *(agency.get("aliases") or [])]
            if (any(_text(alias).lower() in haystack for alias in aliases if alias)
                    or (abbr and re.search(
                        rf"\b{re.escape(abbr)}\b", haystack, flags=re.I))):
                candidates.append(agency)
        if candidates:
            candidates.sort(
                key=lambda agency: max(
                    [len(_text(agency.get("name")))]
                    + [len(_text(alias)) for alias in agency.get("aliases") or []]),
                reverse=True,
            )
            return _text(candidates[0].get("name"))
    except Exception:  # noqa: BLE001 - unknown hierarchy stays explicit/fail-closed
        pass
    dotted = [part.strip() for part in hierarchy.split(".") if part.strip()]
    if len(dotted) >= 2:
        return _text(dotted[1]) or None
    slashed = [part.strip() for part in hierarchy.split("/") if part.strip()]
    if len(slashed) >= 2:
        return _text(slashed[1]) or None
    return explicit or None


def _active_status(row: dict) -> Optional[bool]:
    raw = _raw_value(row, "active", "isActive", "status")
    if isinstance(raw, bool):
        return raw
    value = _text(raw).lower()
    if value in {"yes", "true", "y", "active", "posted"}:
        return True
    if value in {"no", "false", "n", "inactive", "closed", "cancelled",
                 "canceled", "archived"}:
        return False
    return None


def _family_basis(row: dict, notice_id: str) -> dict:
    solicitation = _solicitation_number(row)
    office = _issuing_office(row)
    if solicitation and office:
        return {
            "source": "sam.gov", "kind": "solicitation",
            "office": _text(office).upper(),
            "solicitation_number": _text(solicitation).upper(),
        }
    return {
        "source": "sam.gov", "kind": "notice",
        "notice_id": _text(notice_id).upper(),
    }


def _family_id(basis: dict) -> str:
    return "live:sam:v1:" + _digest(basis)


def _posting_times(row: dict) -> tuple[Optional[datetime], Optional[datetime]]:
    posted = _parse_datetime(
        row.get("posted_date") or _raw_value(row, "posted", "postedDate"))
    modified = _parse_datetime(
        _raw_value(row, "modifiedDate", "modified", "lastUpdatedDate"))
    return posted, modified


def _posting_order(row: dict) -> tuple[str, str, str]:
    posted, modified = _posting_times(row)
    effective = max((stamp for stamp in (posted, modified) if stamp), default=None)
    source_id = row.get("source_id") or ""
    return (
        effective.isoformat() if effective else "",
        posted.isoformat() if posted else "",
        _text(source_id),
    )


def _dossier_depth(results: dict) -> dict[str, dict]:
    dossiers = results.get("dossiers")
    dossiers = dossiers if isinstance(dossiers, dict) else {}
    out = {}
    for key in ("depth_records", "records"):
        rows = dossiers.get(key)
        if not isinstance(rows, list):
            continue
        for record in rows:
            if not isinstance(record, dict) or not record.get("id"):
                continue
            depth = record.get("source_depth")
            if isinstance(depth, dict):
                out[str(record["id"])] = depth
    return out


def _depth_clock_claims(results: dict, posting_id: str):
    dossiers = results.get("dossiers") or {}
    return [row["source_depth"].get("retrieved_at")
            for key in ("depth_records", "records")
            for row in dossiers.get(key) or []
            if isinstance(row, dict) and str(row.get("id") or "") == posting_id
            and _depth_matches_posting(row.get("source_depth"), posting_id)]


def _attachment_state(depth: Optional[dict]) -> tuple[bool, Optional[str]]:
    if not depth:
        return False, "Authoritative notice description and attachments were not retained"
    if (not depth.get("resources_checked")
            or depth.get("resources_schema") != "recognized_v1"):
        errors = "; ".join(_text(e) for e in depth.get("errors") or [] if e)
        return False, ("SAM attachment inventory was not verified"
                       + (f": {errors[:240]}" if errors else ""))
    attachments = depth.get("attachments")
    if not isinstance(attachments, list):
        return False, "SAM attachment inventory shape is missing or ambiguous"
    if depth.get("attachment_inventory_count") != len(attachments):
        return False, "SAM attachment inventory count does not reconcile"
    if depth.get("attachment_inventory_hash") != _digest(attachments):
        return False, "SAM attachment inventory hash does not reconcile"
    if not attachments:
        return True, None
    names = [_text(a.get("name")) for a in attachments if isinstance(a, dict)]
    label = ", ".join(n for n in names if n)[:240]
    return False, (f"{len(attachments)} listed attachment(s) have not been "
                   "downloaded and reviewed" + (f": {label}" if label else ""))


def _depth_matches_posting(depth: Optional[dict], posting_id: str) -> bool:
    if not isinstance(depth, dict):
        return False
    if _text(depth.get("notice_id")) != posting_id:
        return False
    if not _sam_url_matches_notice(depth.get("source_url"), posting_id):
        return False
    if depth.get("description_checked") is not True:
        return False
    return not any(
        "description" in _text(error).lower()
        and ("failed" in _text(error).lower()
             or "no description" in _text(error).lower())
        for error in depth.get("errors") or [])


_NEGATIVE_REQUIREMENT = re.compile(
    r"\b(not|no|without|exclude(?:d|s)?|unrelated|optional)\b|"
    r"out\s+of\s+scope|does\s+not|do\s+not|isn't|aren't|prohibited",
    re.I,
)


def _positive_requirement_excerpt(description: str, term: str) -> Optional[str]:
    """Exact clause with an affirmative buyer/performer obligation.

    Term presence alone is not requirement evidence. This deliberately
    rejects ambiguous prose and negative references such as "not required"
    or "out of scope" even when the approved capability phrase appears. It
    also rejects contextual statements such as "the existing network supports
    X"; a buyer intent, delivery actor, or explicit scope statement must bind
    the approved capability to the procurement.
    """
    from tools.capability import term_regex
    term_rx = term_regex(term)
    escaped_term = term_rx.pattern
    # The capability must sit in the direct object phrase. Coordinating or
    # contextual words before it terminate the match so a nearby unrelated
    # duty cannot borrow a background technology mention.
    direct_object = (
        r"(?:(?!\b(?:and|but|while|whereas|using|via|with|except|although)\b|"
        r"[.;]).){0,120}")
    performer = re.compile(
        r"\b(?:contractor|vendor|offeror|awardee|successful\s+offeror)\b"
        r"(?:(?![.;]).){0,100}\b(?:shall|must|is\s+required\s+to|will)\s+"
        r"(?:provide|deliver|perform|support|include|implement|operate|maintain)\b"
        + direct_object + escaped_term,
        re.I,
    )
    buyer = re.compile(
        r"\b(?:government|agency|department|buyer|program|office)\b"
        r"(?:(?![.;]).){0,100}\b(?:seeks?|requires?|is\s+procuring|"
        r"will\s+procure|intends?\s+to\s+(?:acquire|procure)|"
        r"is\s+soliciting)\b" + direct_object + escaped_term,
        re.I,
    )
    explicit_scope = re.compile(
        r"(?:\b(?:requirement|scope|solution|system|service|platform)\b"
        r"(?:(?![.;]).){0,80}\b(?:includes?|requires?|"
        r"must\s+(?:provide|support|include))\b" + direct_object
        + escaped_term + r"|" + escaped_term
        + r".{0,100}\b(?:is|are)\s+(?:required|mandatory)\b)",
        re.I,
    )
    for clause in re.split(r"(?<=[.!?])\s+|[;\r\n]+", description):
        if not term_rx.search(clause):
            continue
        if _NEGATIVE_REQUIREMENT.search(clause):
            continue
        if (performer.search(clause) or buyer.search(clause)
                or explicit_scope.search(clause)):
            return clause.strip()[:500]
    return None


def _known_classification(notice_type: str) -> Optional[LiveClassification]:
    low = notice_type.lower()
    if any(token in low for token in ("award notice", "award")):
        return LiveClassification.AWARDED_OR_CLOSED
    if any(token in low for token in (
            "sources sought", "source sought", "request for information", "rfi")):
        return LiveClassification.MARKET_RESEARCH
    if "presolicitation" in low or "pre-solicitation" in low:
        return LiveClassification.PRESOLICITATION
    if any(token in low for token in (
            "special notice", "notice of intent", "justification")):
        return LiveClassification.SPECIAL_NOTICE
    if "amendment" in low:
        return LiveClassification.AMENDMENT
    if "solicitation" in low:
        return None  # strict BID_NOW screen decides this notice class
    return LiveClassification.UNSCREENED


def _sam_coverage(results: dict) -> SourceCoverage:
    rows = results.get("sam.gov")
    census = results.get("sam_census")
    if not isinstance(rows, list):
        detail = ((rows or {}).get("error") if isinstance(rows, dict)
                  else "SAM result is missing or malformed")
        return SourceCoverage(
            source="sam.gov", lane=SourceLane.LIVE,
            status=CoverageStatus.FAILED, required=True,
            note=_text(detail) or "SAM collection failed",
        )
    if not isinstance(census, dict):
        return SourceCoverage(
            source="sam.gov", lane=SourceLane.LIVE,
            status=CoverageStatus.PARTIAL, required=True,
            records_screened=len(rows), total_available=None,
            note="SAM census is absent; collection completeness is unproved",
        )
    def _count(value: Any) -> Optional[int]:
        # bool is an int subclass, but it is never a defensible census count.
        return (value if isinstance(value, int) and not isinstance(value, bool)
                and value >= 0 else None)

    source = _text(census.get("source"))
    matched = _count(census.get("matched"))
    active = _count(census.get("active_screened"))
    retrieved = _count(census.get("retrieved"))
    problems: list[str] = []
    if census.get("complete") is not True:
        problems.append("collector did not assert a complete pass")
    if source not in {"sam_extract", "sam.gov live API"}:
        problems.append("collector source is absent or unrecognized")
    if matched is None:
        problems.append("matched count is absent or invalid")
    elif matched != len(rows):
        problems.append(
            f"matched count {matched} does not reconcile to {len(rows)} rows")
    if active is None:
        problems.append("active-screened count is absent or invalid")
    elif matched is not None and matched > active:
        problems.append("matched count exceeds the screened population")

    if source == "sam_extract":
        screened = active if active is not None else len(rows)
        total = active
    elif source == "sam.gov live API":
        screened = retrieved if retrieved is not None else len(rows)
        total = active
        if retrieved is None:
            problems.append("live-API retrieved count is absent or invalid")
        elif active is not None and retrieved != active:
            problems.append(
                f"live-API retrieval {retrieved} does not reconcile to "
                f"{active} available records")
        if matched is not None and retrieved is not None and matched > retrieved:
            problems.append("matched rows exceed live-API records retrieved")
        manifest = census.get("attempt_manifest")
        passes = census.get("passes")
        if not isinstance(manifest, dict):
            problems.append("live-API attempt manifest is absent or malformed")
        else:
            requested = manifest.get("requested")
            executed = manifest.get("executed")
            omitted = manifest.get("omitted")
            if not all(isinstance(value, list)
                       for value in (requested, executed, omitted)):
                problems.append("live-API attempt manifest lists are malformed")
            else:
                requested_ids = [row.get("id") for row in requested
                                 if isinstance(row, dict)]
                executed_ids = [row.get("id") for row in executed
                                if isinstance(row, dict)]
                omitted_ids = [row.get("id") for row in omitted
                               if isinstance(row, dict)]
                if (len(requested_ids) != len(requested)
                        or len(executed_ids) != len(executed)
                        or len(omitted_ids) != len(omitted)
                        or any(not _text(value) for value in (
                            requested_ids + executed_ids + omitted_ids))):
                    problems.append("live-API attempt manifest has invalid ids")
                elif (len(set(requested_ids)) != len(requested_ids)
                      or sorted(requested_ids)
                      != sorted(executed_ids + omitted_ids)):
                    problems.append("live-API attempt manifest does not reconcile")
                if omitted_ids:
                    problems.append(
                        f"{len(omitted_ids)} requested live-API pass(es) were omitted")
                if (isinstance(passes, dict)
                        and len(executed_ids) != len(passes)):
                    problems.append(
                        "live-API executed manifest does not match pass census")
        if not isinstance(passes, dict):
            problems.append("live-API pass census is absent or malformed")
        else:
            pass_retrieved = []
            pass_totals = []
            for label, row in passes.items():
                if not isinstance(row, dict):
                    problems.append(
                        f"live-API pass {_text(label) or '?'} is malformed")
                    continue
                got = _count(row.get("retrieved"))
                available = _count(row.get("total_records"))
                if (row.get("complete") is not True or got is None
                        or available is None or got != available):
                    problems.append(
                        f"live-API pass {_text(label) or '?'} is incomplete")
                    continue
                pass_retrieved.append(got)
                pass_totals.append(available)
            if (retrieved is not None and sum(pass_retrieved) != retrieved):
                problems.append(
                    "live-API retrieved count does not reconcile to pass census")
            if active is not None and sum(pass_totals) != active:
                problems.append(
                    "live-API available count does not reconcile to pass census")
    else:
        screened = len(rows)
        total = None

    complete = not problems
    note = (f"{len(rows)} matched notice record(s); source "
            f"{source or 'not stated'}")
    if problems:
        note += "; required SAM census is incomplete: " + "; ".join(problems)
    return SourceCoverage(
        source="sam.gov", lane=SourceLane.LIVE,
        status=(CoverageStatus.COMPLETE if complete else CoverageStatus.PARTIAL),
        required=True, records_screened=screened,
        total_available=total, note=note,
    )


def _bound_requirement_reviews(
    payload: Optional[dict],
    *,
    client_name: str,
    binding: dict,
) -> tuple[dict[str, dict], list[str]]:
    """Return exact current human review decisions, failing closed on drift."""
    if payload is None:
        return {}, []
    if not isinstance(payload, dict):
        return {}, ["Live requirement review artifact is malformed"]
    if payload.get("schema_version") != 1:
        return {}, ["Live requirement review schema is unsupported"]
    if payload.get("client") != client_name:
        return {}, ["Live requirement reviews belong to a different client"]
    stored_binding = payload.get("binding")
    required_binding = {
        "version", "scope_designator", "sweep_artifact",
        "sweep_sha256", "profile_sha256",
    }
    if not isinstance(stored_binding, dict):
        return {}, ["Live requirement reviews are unbound"]
    changed = [key for key in sorted(required_binding)
               if stored_binding.get(key) != binding.get(key)]
    if changed:
        return {}, [
            "Live requirement review binding differs from this Assess run: "
            + ", ".join(changed)]
    rows = payload.get("reviews")
    if not isinstance(rows, list):
        return {}, ["Live requirement review collection is malformed"]
    out: dict[str, dict] = {}
    conflicts: set[str] = set()
    diagnostics: list[str] = []
    for row in rows:
        if not isinstance(row, dict):
            diagnostics.append("Live requirement review rejected a non-object row")
            continue
        notice_id = _text(row.get("notice_id"))
        if not notice_id:
            diagnostics.append("Live requirement review is missing a notice id")
            continue
        if notice_id in out or notice_id in conflicts:
            out.pop(notice_id, None)
            conflicts.add(notice_id)
            diagnostics.append(
                f"Live requirement review {notice_id} is duplicated and ignored")
            continue
        if row.get("decision") not in {"approved", "rejected"}:
            diagnostics.append(
                f"Live requirement review {notice_id} has an invalid decision")
            continue
        out[notice_id] = row
    return out, diagnostics


def adapt_live_ledger(
    searches: dict,
    profile: Any,
    *,
    run_id: str,
    scope: AssessScope,
    profile_version: str,
    as_of: datetime,
    requirement_reviews_payload: Optional[dict] = None,
    binding: Optional[dict] = None,
) -> tuple[LiveSolicitationLedger, dict[str, str], dict[str, dict], list[str]]:
    """Build the authoritative SAM census without promoting metadata as fact."""
    results = searches.get("results") or {}
    rows = results.get("sam.gov")
    rows = rows if isinstance(rows, list) else []
    triage = results.get("triage")
    triage = triage if isinstance(triage, dict) else {}
    depths = _dossier_depth(results)
    sam_complete = _sam_coverage(results).status == CoverageStatus.COMPLETE
    collection_integrity = True
    requirement_reviews, diagnostics = _bound_requirement_reviews(
        requirement_reviews_payload,
        client_name=searches.get("client") or profile.client_name,
        binding=binding or {},
    )

    groups: dict[str, list[dict]] = {}
    posting_index: dict[str, str] = {}
    conflicted_postings: set[str] = set()
    for row in rows:
        if not isinstance(row, dict):
            diagnostics.append("SAM ledger rejected a non-object row")
            collection_integrity = False
            continue
        notice_id = _text(row.get("source_id") or _raw_value(
            row, "notice_id", "noticeId"))
        title = _text(row.get("title"))
        agency = _text(row.get("agency") or _raw_value(
            row, "fullParentPathName", "agency"))
        raw_notice_id = _text(_raw_value(row, "notice_id", "noticeId"))
        if not notice_id or not title or not agency:
            diagnostics.append(
                "SAM ledger rejected a row missing notice id, title, or agency")
            collection_integrity = False
            continue
        if not _agency_is_in_scope(agency, scope):
            diagnostics.append(
                f"SAM ledger rejected {notice_id}: buyer is outside the "
                "operator-selected agency scope")
            collection_integrity = False
            continue
        if row.get("source") != "sam.gov" or raw_notice_id != notice_id:
            diagnostics.append(
                f"SAM ledger rejected {notice_id}: explicit source/raw identity "
                "does not match the canonical posting")
            collection_integrity = False
            continue
        basis = _family_basis(row, notice_id)
        record_id = _family_id(basis)
        if notice_id in conflicted_postings:
            collection_integrity = False
            continue
        prior_family = posting_index.get(notice_id)
        if prior_family and prior_family != record_id:
            diagnostics.append(
                f"SAM posting {notice_id} resolved to conflicting families")
            collection_integrity = False
            conflicted_postings.add(notice_id)
            posting_index.pop(notice_id, None)
            groups[prior_family] = [
                prior for prior in groups.get(prior_family, [])
                if _text(prior.get("source_id")) != notice_id
            ]
            if not groups[prior_family]:
                groups.pop(prior_family, None)
            continue
        groups.setdefault(record_id, []).append(row)
        posting_index[notice_id] = record_id

    sam_complete = sam_complete and collection_integrity
    records: list[LiveSolicitation] = []
    accepted_current_postings: dict[str, dict] = {}
    for record_id, members in sorted(groups.items()):
        members = sorted(members, key=_posting_order)
        current = members[-1]
        current_stamp = _posting_order(current)[:2]
        tied_current = [member for member in members
                        if _posting_order(member)[:2] == current_stamp]
        current_ambiguous = (
            len(tied_current) > 1
            and len({_digest(member) for member in tied_current}) > 1
        )
        notice_id = _text(current.get("source_id") or _raw_value(
            current, "notice_id", "noticeId"))
        title = _text(current.get("title"))
        agency = _text(current.get("agency") or _raw_value(
            current, "fullParentPathName", "agency"))
        response_deadline = _parse_date(
            current.get("response_deadline")
            or _raw_value(current, "deadline", "responseDeadLine"))
        verdict = triage.get(notice_id)
        verdict = verdict if isinstance(verdict, dict) else {}
        verdict_name = _text(verdict.get("verdict")).lower()
        verdict_reason = _text(verdict.get("reason"))
        notice_type = _notice_type(current)
        active = _active_status(current)
        accepted_current_postings[notice_id] = current

        evidence = []
        for posting in members:
            posting_id = _text(posting.get("source_id") or _raw_value(
                posting, "notice_id", "noticeId"))
            url, _ = _notice_url(posting, posting_id)
            depth = depths.get(posting_id)
            trusted_depth = depth if _depth_matches_posting(depth, posting_id) else None
            description = _text((trusted_depth or {}).get("description"))
            excerpt = description[:800] if description else _text(posting.get("title"))
            observation_hash = _digest({"posting": posting, "depth": depth})
            clock = acquisition_clock(
                (trusted_depth or {}).get("retrieved_at"),
                basis="sam_notice_depth", component="notice_description",
                field="source_depth.retrieved_at", binding=observation_hash,
                competing=_depth_clock_claims(results, posting_id) if trusted_depth else ())
            if not trusted_depth:
                # Record unsupported claims, never promote them to source authority.
                claims = [(prefix + key, data[key]) for prefix, data in (("row.", posting), ("raw_payload.", posting.get("raw_payload") or {}), ("source_depth.", depth or {}))
                          if isinstance(data, dict) for key in ("retrieved_at", "fetched_at", "observed_at")
                          if data.get(key) is not None]
                clock = acquisition_clock(claims[0][1] if claims else None,
                    competing=[v for _, v in claims[1:]], basis="none", component="notice_payload",
                    field=" | ".join(key for key, _ in claims) or "unrecorded", binding=observation_hash)
            retrieved = clock_instant(clock)
            supports = [EvidenceUse.TIMING, EvidenceUse.BUYER]
            if trusted_depth and description:
                supports.insert(0, EvidenceUse.REQUIREMENT)
            evidence.append(EvidenceRef(
                evidence_id=f"ev:sam:v1:{posting_id}:{observation_hash}",
                tier=EvidenceTier.NOTICE,
                kind=EvidenceKind.NOTICE,
                source_name="SAM.gov notice",
                source_url=url,
                retrieved_at=retrieved,
                source_acquisition=clock,
                observed_date=_parse_date(
                    posting.get("posted_date")
                    or _raw_value(posting, "posted", "postedDate")),
                record_hash=observation_hash,
                excerpt=excerpt,
                primary_source=True,
                supports=tuple(supports),
            ))

        current_depth = depths.get(notice_id)
        if current_depth is None:
            # A dossier may have been built on an earlier posting in this
            # solicitation family. It remains citable but cannot silently
            # claim to be the effective current package.
            prior_depth = next(
                (depths.get(_text(m.get("source_id")))
                 for m in reversed(members[:-1]) if depths.get(_text(m.get("source_id")))),
                None,
            )
            if prior_depth:
                diagnostics.append(
                    f"{record_id}: retained depth belongs to an earlier posting")
        trusted_current_depth = (current_depth if _depth_matches_posting(
            current_depth, notice_id) else None)
        description = _text((trusted_current_depth or {}).get("description"))
        depth_time = acquired_at(evidence[-1]) if trusted_current_depth else None
        verification_time = max(
            (stamp for stamp in (as_of, depth_time) if stamp), default=as_of)
        posted_time, modified_time = _posting_times(current)
        current_effective_time = max(
            (stamp for stamp in (posted_time, modified_time) if stamp),
            default=None)
        source_depth_id = _text((current_depth or {}).get("notice_id"))
        _raw_url, raw_url_ok = _notice_url(current, notice_id)
        raw_url_id_ok = raw_url_ok and _sam_url_matches_notice(
            current.get("api_url") or _raw_value(current, "url", "uiLink"),
            notice_id)
        source_depth_url_ok = _sam_url_matches_notice(
            (current_depth or {}).get("source_url"), notice_id)
        attachments_checked, attachment_gap = _attachment_state(
            trusted_current_depth)
        raw_attachments = ((trusted_current_depth or {}).get("attachments")
                           if isinstance((trusted_current_depth or {}).get("attachments"), list)
                           else [])
        attachment_inventory_count = (
            (trusted_current_depth or {}).get("attachment_inventory_count")
            if isinstance((trusted_current_depth or {}).get(
                "attachment_inventory_count"), int)
            and not isinstance((trusted_current_depth or {}).get(
                "attachment_inventory_count"), bool)
            else None)
        attachment_inventory_hash = (
            (trusted_current_depth or {}).get("attachment_inventory_hash")
            if isinstance((trusted_current_depth or {}).get(
                "attachment_inventory_hash"), str)
            else None)
        attachment_inventory_valid = bool(
            trusted_current_depth
            and trusted_current_depth.get("resources_checked") is True
            and trusted_current_depth.get("resources_schema") == "recognized_v1"
            and isinstance(trusted_current_depth.get("attachments"), list)
            and attachment_inventory_count == len(raw_attachments)
            and attachment_inventory_hash == _digest(raw_attachments)
        )
        attachments: list[NoticeAttachment] = []
        try:
            for attachment in raw_attachments:
                if not isinstance(attachment, dict) or not _text(attachment.get("name")):
                    raise ValueError("attachment row is malformed")
                attachments.append(NoticeAttachment(
                    name=_text(attachment.get("name")),
                    media_type=_text(
                        attachment.get("type") or attachment.get("mimeType")) or None,
                    resource_id=_text(
                        attachment.get("resource_id")
                        or attachment.get("resourceId")
                        or attachment.get("attachmentId")) or None,
                    source_url=_sam_url(
                        attachment.get("source_url")
                        or attachment.get("downloadUrl")
                        or attachment.get("uri")),
                ))
        except (TypeError, ValueError) as exc:
            attachments = []
            attachment_inventory_valid = False
            attachments_checked = False
            attachment_gap = f"SAM attachment inventory cannot be reviewed: {exc}"

        from tools.capability import match_capability
        match = match_capability(profile, description) if description else None
        from tools.capability import term_regex
        matched_core = tuple(
            term for term in profile.capability_terms.core
            if match and match.tier == "core" and term_regex(term).search(description)
        )
        positive_spans = {
            term: _positive_requirement_excerpt(description, term)
            for term in matched_core
        }
        core_terms = tuple(term for term in matched_core
                           if positive_spans.get(term))
        candidate_requirement_excerpt = (
            positive_spans[core_terms[0]] if core_terms else None)

        requirement_review = requirement_reviews.get(notice_id)
        requirement_review_evidence_id = None
        requirement_reviewed_by = None
        requirement_reviewed_at = None
        attachment_reviewed_by = None
        attachment_reviewed_at = None
        approved_requirement_excerpt = None
        approved_core_terms: tuple[str, ...] = ()
        requirement_review_gap = None
        if requirement_review:
            if requirement_review.get("decision") == "rejected":
                requirement_review_gap = (
                    "Operator rejected the proposed requirement span; a new "
                    "opportunity-specific review is required")
            else:
                proposed_evidence_id = _text(
                    requirement_review.get("evidence_id"))
                proposed_excerpt = _text(requirement_review.get("excerpt"))
                proposed_reviewer = _text(
                    requirement_review.get("reviewed_by"))
                proposed_reviewed_at = _parse_datetime(
                    requirement_review.get("reviewed_at"),
                    require_timezone=True)
                raw_terms = requirement_review.get("capability_terms")
                raw_terms = raw_terms if isinstance(raw_terms, list) else []
                term_lookup = {term.casefold(): term for term in matched_core}
                proposed_terms = tuple(dict.fromkeys(
                    term_lookup[_text(term).casefold()]
                    for term in raw_terms
                    if _text(term).casefold() in term_lookup
                    and term_regex(term_lookup[_text(term).casefold()]).search(
                        proposed_excerpt)
                ))
                review_problems = []
                if proposed_evidence_id != evidence[-1].evidence_id:
                    review_problems.append("evidence id no longer matches")
                if not proposed_excerpt or proposed_excerpt not in description:
                    review_problems.append("excerpt is not exact current source text")
                if not proposed_terms:
                    review_problems.append(
                        "no approved core capability term is bound to the excerpt")
                if not proposed_reviewer:
                    review_problems.append("reviewer is missing")
                if proposed_reviewed_at is None:
                    review_problems.append(
                        "review timestamp is missing or timezone-naive")
                elif acquired_at(evidence[-1]) is None:
                    review_problems.append("source acquisition chronology is unknown")
                elif raw_attachments and any(acquired_at(e) is None for e in evidence):
                    review_problems.append("attachment evidence acquisition chronology is incomplete")
                elif proposed_reviewed_at < evidence[-1].retrieved_at:
                    review_problems.append("review predates its evidence")
                if raw_attachments:
                    if not attachment_inventory_valid:
                        review_problems.append(
                            "attachment inventory is structurally invalid")
                    if requirement_review.get("attachments_reviewed") is not True:
                        review_problems.append(
                            "listed attachments have not been human-reviewed")
                    if (requirement_review.get("attachment_inventory_hash")
                            != attachment_inventory_hash):
                        review_problems.append(
                            "reviewed attachment inventory hash no longer matches")
                    if (requirement_review.get("attachment_inventory_count")
                            != attachment_inventory_count):
                        review_problems.append(
                            "reviewed attachment inventory count no longer matches")
                if review_problems:
                    requirement_review_gap = (
                        "Human requirement review is invalid: "
                        + "; ".join(review_problems))
                    diagnostics.append(
                        f"{record_id}: {requirement_review_gap}")
                else:
                    requirement_review_evidence_id = proposed_evidence_id
                    requirement_reviewed_by = proposed_reviewer
                    requirement_reviewed_at = proposed_reviewed_at
                    approved_requirement_excerpt = proposed_excerpt
                    approved_core_terms = proposed_terms
                    if raw_attachments and attachment_inventory_valid:
                        attachments_checked = True
                        attachment_gap = None
                        attachment_reviewed_by = proposed_reviewer
                        attachment_reviewed_at = proposed_reviewed_at
                    verification_time = max(
                        verification_time, proposed_reviewed_at)

        trace_terms = approved_core_terms or core_terms
        fit_trace = tuple(
            f"{evidence[-1].evidence_id}: "
            + ("human-approved requirement" if approved_core_terms
               else "candidate requirement")
            + f" contains approved core term '{term}'"
            for term in trace_terms
        )

        known = _known_classification(notice_type)
        classification = known or LiveClassification.UNSCREENED
        recommendation = LiveRecommendation.RESEARCH
        exclusion_reason = None
        gaps = []

        if active is False or (response_deadline
                               and response_deadline < verification_time.date()) \
                or known == LiveClassification.AWARDED_OR_CLOSED:
            classification = LiveClassification.AWARDED_OR_CLOSED
            recommendation = LiveRecommendation.NO_BID
        elif verdict_name == "discard":
            classification = LiveClassification.UNSCREENED
            recommendation = LiveRecommendation.RESEARCH
            attachment_gap = (
                "Metadata-only triage recommended discard: "
                + (verdict_reason or "fit not established")
                + "; authoritative requirement review is still required")
        elif current_ambiguous:
            classification = LiveClassification.UNSCREENED
            recommendation = LiveRecommendation.RESEARCH
            attachment_gap = (
                "Conflicting SAM postings share the latest available timestamp; "
                "the effective requirement is ambiguous")
            diagnostics.append(f"{record_id}: current amendment is ambiguous")
        elif known in (
                LiveClassification.MARKET_RESEARCH,
                LiveClassification.PRESOLICITATION,
                LiveClassification.SPECIAL_NOTICE):
            recommendation = (LiveRecommendation.MONITOR
                              if verdict_name in {"pursue", "monitor"}
                              else LiveRecommendation.RESEARCH)
        elif known == LiveClassification.AMENDMENT:
            recommendation = LiveRecommendation.RESEARCH
        elif "solicitation" in notice_type.lower():
            if current.get("source") != "sam.gov":
                gaps.append("Canonical row does not explicitly identify SAM.gov provenance")
            if active is not True:
                gaps.append("Notice does not carry an explicit active status")
            if not response_deadline or response_deadline <= verification_time.date():
                gaps.append("A future deadline with time remaining is not verified")
            if not sam_complete:
                gaps.append("Required SAM source census is incomplete")
            raw_notice_id = _text(_raw_value(current, "notice_id", "noticeId"))
            if raw_notice_id != notice_id:
                gaps.append("Raw SAM notice identity does not match the ledger posting")
            if not raw_url_id_ok or not source_depth_url_ok:
                gaps.append(
                    "Retained depth and source URL are not bound to this SAM notice id")
            if not depth_time:
                gaps.append("Retained authoritative depth lacks a timezone-aware retrieval time")
            if depth_time and current_effective_time \
                    and depth_time < current_effective_time:
                gaps.append("Retained depth predates the current SAM posting or modification")
            if source_depth_id != notice_id:
                gaps.append("Retained depth does not match the current SAM notice id")
            if (current_depth or {}).get("description_checked") is not True:
                gaps.append("Authoritative description fetch is not marked complete")
            if any("description" in _text(error).lower()
                   and ("failed" in _text(error).lower()
                        or "no description" in _text(error).lower())
                   for error in (current_depth or {}).get("errors") or []):
                gaps.append("Authoritative description fetch carries an open error")
            if not description:
                gaps.append("Full authoritative requirement text was not retained")
            if not approved_core_terms:
                if requirement_review_gap:
                    gaps.append(requirement_review_gap)
                elif core_terms:
                    gaps.append(
                        "Candidate requirement span awaits explicit human approval")
                else:
                    gaps.append(
                        "No approved core capability term appears in explicit positive "
                        "requirement language")
            if not core_terms and not approved_core_terms:
                candidate_requirement_excerpt = None
            if attachment_gap:
                gaps.append(attachment_gap)
            if not gaps:
                classification = LiveClassification.BID_NOW
                recommendation = LiveRecommendation.PURSUE
            else:
                classification = LiveClassification.UNSCREENED
                recommendation = LiveRecommendation.RESEARCH
                attachment_gap = "; ".join(gaps)
        elif classification == LiveClassification.UNSCREENED:
            gaps.append(f"Notice type '{notice_type or 'unknown'}' is not classified")
            attachment_gap = "; ".join(gaps)

        requirement_excerpt = (
            approved_requirement_excerpt or candidate_requirement_excerpt)
        if requirement_excerpt:
            # Requirement evidence must retain the exact cited source span.
            # A flat prefix silently drops valid requirements that occur later
            # in a long SAM description, so center the bounded excerpt on the
            # selected affirmative sentence before validating the contract.
            requirement_start = description.find(requirement_excerpt)
            if requirement_start >= 0:
                excerpt_start = max(0, requirement_start - 150)
                excerpt_end = max(
                    excerpt_start + 800,
                    requirement_start + len(requirement_excerpt),
                )
                excerpt = description[excerpt_start:excerpt_end]
                current_evidence = evidence[-1].model_dump(mode="python")
                current_evidence["excerpt"] = excerpt
                evidence[-1] = EvidenceRef.model_validate(current_evidence)
            if classification == LiveClassification.BID_NOW:
                attachment_gap = None
        elif classification == LiveClassification.UNSCREENED and not attachment_gap:
            attachment_gap = "Authoritative requirement package has not been fully screened"

        records.append(LiveSolicitation(
            record_id=record_id,
            notice_id=notice_id,
            solicitation_number=_solicitation_number(current),
            title=title,
            agency=agency,
            component=_component(current),
            office=_issuing_office(current),
            classification=classification,
            response_deadline=response_deadline,
            authoritative_evidence=evidence,
            requirement_excerpt=requirement_excerpt,
            requirement_review_evidence_id=requirement_review_evidence_id,
            requirement_reviewed_by=requirement_reviewed_by,
            requirement_reviewed_at=requirement_reviewed_at,
            attachments=attachments,
            attachment_inventory_count=attachment_inventory_count,
            attachment_inventory_hash=attachment_inventory_hash,
            attachment_inventory_valid=attachment_inventory_valid,
            attachments_checked=attachments_checked,
            attachment_reviewed_by=attachment_reviewed_by,
            attachment_reviewed_at=attachment_reviewed_at,
            attachment_gap=attachment_gap,
            fit_trace=fit_trace,
            recommendation=recommendation,
            exclusion_reason=exclusion_reason,
            verified_at=verification_time,
        ))

    ledger = LiveSolicitationLedger(
        run_id=run_id, client_name=searches.get("client") or profile.client_name,
        profile_version=profile_version, scope=scope, as_of=as_of,
        records=records,
    )
    return ledger, posting_index, accepted_current_postings, diagnostics


_HORIZON_EVIDENCE = {
    "sam_monitor": (
        EvidenceTier.NOTICE, EvidenceKind.NOTICE, True,
        # The Horizon fact is notice metadata only. A model-authored triage
        # reason is never primary evidence, and title/type/deadline do not prove
        # a requirement. A future retained-description fact kind may add
        # REQUIREMENT only after exact-span validation.
        (EvidenceUse.TIMING, EvidenceUse.BUYER)),
    "expiring_award": (
        EvidenceTier.MARKET, EvidenceKind.AWARD, True,
        (EvidenceUse.TIMING, EvidenceUse.INCUMBENT, EvidenceUse.BUYER)),
    "forecast_screen": (
        EvidenceTier.PROGRAM, EvidenceKind.AGENCY_FORECAST, False,
        (EvidenceUse.BUYER,)),
    "forecast_delta": (
        EvidenceTier.PROGRAM, EvidenceKind.AGENCY_FORECAST, False,
        (EvidenceUse.TIMING, EvidenceUse.REQUIREMENT)),
    "forecast_line": (
        EvidenceTier.PROGRAM, EvidenceKind.AGENCY_FORECAST, True,
        (EvidenceUse.TIMING, EvidenceUse.REQUIREMENT, EvidenceUse.BUYER)),
    "dod_award_wire": (
        EvidenceTier.MARKET, EvidenceKind.AGENCY_ANNOUNCEMENT, True,
        (EvidenceUse.BUYER, EvidenceUse.INCUMBENT)),
    "budget_pressure": (
        EvidenceTier.MARKET, EvidenceKind.BUDGET, True,
        (EvidenceUse.FUNDING, EvidenceUse.BUYER)),
    "budget_line": (
        EvidenceTier.PROGRAM, EvidenceKind.BUDGET, True,
        (EvidenceUse.FUNDING, EvidenceUse.BUYER)),
    "oversight": (
        EvidenceTier.PROGRAM, EvidenceKind.WATCHDOG, True,
        (EvidenceUse.REQUIREMENT, EvidenceUse.BUYER)),
    "legislation": (
        EvidenceTier.PROGRAM, EvidenceKind.LEGISLATION, True,
        (EvidenceUse.FUNDING, EvidenceUse.REQUIREMENT)),
    "regulatory": (
        EvidenceTier.PROGRAM, EvidenceKind.REGULATION, True,
        (EvidenceUse.REQUIREMENT, EvidenceUse.TIMING)),
    "cisa_kev": (
        EvidenceTier.PROGRAM, EvidenceKind.AGENCY_ANNOUNCEMENT, True,
        (EvidenceUse.TIMING,)),
    "agency_announcement": (
        EvidenceTier.PROGRAM, EvidenceKind.AGENCY_ANNOUNCEMENT, True,
        (EvidenceUse.REQUIREMENT, EvidenceUse.TIMING, EvidenceUse.BUYER)),
}

_HORIZON_CONTEXT_ONLY = frozenset({"forecast_screen"})


def _horizon_fact_scope_problem(
    fact: dict,
    scope: AssessScope,
    *,
    fact_kind: str,
) -> Optional[str]:
    """Focused runs accept only buyer-bound affirmative Horizon evidence.

    Government-wide forecast-screen rows may remain visible as coverage
    context because the Horizon validator prevents them from being the sole
    support for a thesis. Every affirmative fact must name an agency matching
    the operator-selected scope. Missing/legacy scope metadata fails closed
    only for focused strict projection; all-federal behavior is unchanged.
    """
    if scope.mode == ScopeMode.ALL:
        return None
    fact_scope = fact.get("scope")
    if not isinstance(fact_scope, dict):
        return "lacks structured buyer-scope metadata"
    scope_kind = _text(fact_scope.get("kind"))
    if scope_kind == "government_wide":
        if fact_kind in _HORIZON_CONTEXT_ONLY:
            return None
        return "is government-wide rather than buyer-bound to the focused scope"
    if scope_kind != "agency":
        return "has unsupported buyer-scope metadata"
    agencies = fact_scope.get("agencies")
    if (not isinstance(agencies, list) or not agencies
            or any(not _text(agency) for agency in agencies)):
        return "has malformed agency-scope metadata"
    component = _text(fact_scope.get("component"))
    candidates = [
        " / ".join(part for part in (_text(agency), component) if part)
        for agency in agencies
    ]
    if not any(_agency_is_in_scope(candidate, scope)
               for candidate in candidates):
        return "belongs to an agency outside the operator-selected scope"
    return None


def adapt_horizon_ledger(
    payload: Optional[dict],
    binding: dict,
    *,
    run_id: str,
    client_name: str,
    scope: AssessScope,
    profile_version: str,
    as_of: datetime,
) -> tuple[OpportunityThesisLedger, list[str]]:
    """Project only an exact, bound Horizon v2 artifact into strict theses."""
    empty = OpportunityThesisLedger(
        run_id=run_id, client_name=client_name,
        profile_version=profile_version, scope=scope, as_of=as_of,
    )
    if not payload:
        return empty, []
    if not isinstance(payload, dict):
        return empty, ["Horizon artifact is malformed"]
    if payload.get("client") != client_name:
        return empty, ["Horizon artifact belongs to a different client"]
    if payload.get("status") not in {"draft", "approved"}:
        return empty, ["Horizon artifact has an invalid review status"]
    if payload.get("status") == "approved":
        approved_at = _parse_datetime(
            payload.get("approved_at"), require_timezone=True)
        if approved_at is None or not _text(payload.get("approved_by")):
            return empty, [
                "Approved Horizon lacks a timezone-aware approval timestamp "
                "or reviewer identity"]
    elif payload.get("approved_at") is not None \
            or payload.get("approved_by") is not None:
        return empty, ["Draft Horizon carries invalid approval metadata"]
    stored_binding = payload.get("binding")
    required_binding = {
        "version", "scope_designator", "sweep_artifact",
        "sweep_sha256", "profile_sha256",
    }
    if not isinstance(stored_binding, dict):
        return empty, ["Horizon artifact is legacy or unbound"]
    changed = [key for key in sorted(required_binding)
               if stored_binding.get(key) != binding.get(key)]
    if changed:
        return empty, [
            "Horizon binding differs from this Assess run: " + ", ".join(changed)]

    raw_set = payload.get("set")
    if not isinstance(raw_set, dict):
        return empty, ["Horizon set is missing or malformed"]
    raw_items = raw_set.get("items") or []
    if not isinstance(raw_items, list):
        return empty, ["Horizon item collection is malformed"]
    if payload.get("schema_version", 1) != 2:
        return empty, [
            "Horizon v1 cannot be losslessly projected; recompose v2"]

    from agents.reports.horizon import HorizonSet, validate_horizon

    try:
        hset = HorizonSet.model_validate(payload.get("set") or {})
    except Exception as exc:  # noqa: BLE001 - invalid optional layer degrades
        return empty, [f"Horizon v2 schema is invalid: {exc}"]
    bank = payload.get("fact_bank")
    if not isinstance(bank, list):
        return empty, ["Horizon fact bank is missing"]
    try:
        problems = validate_horizon(
            hset, bank, schema_version=2,
            expected_client_name=client_name)
    except Exception as exc:  # noqa: BLE001 - optional layer fails closed
        return empty, [f"Horizon v2 validation failed safely: {exc}"]
    if problems:
        return empty, ["Horizon v2 failed strict validation: " + "; ".join(problems)]
    if not raw_items:
        return empty, []

    by_id = {fact.get("id"): fact for fact in bank if isinstance(fact, dict)}
    items = []
    status = (IntelligenceStatus.APPROVED
              if payload.get("status") == "approved"
              else IntelligenceStatus.PROPOSED)
    for item in hset.items:
        if not _agency_is_in_scope(item.where, scope):
            return empty, [
                f"Horizon item {item.id} is outside the operator-selected "
                "agency scope"]
        evidence = []
        for signal in item.verified:
            fact = by_id.get(signal.evidence_id)
            kind = _text((fact or {}).get("kind"))
            if kind == "client_past_performance":
                return empty, [
                    f"Horizon item {item.id} selected client-known evidence"]
            mapping = _HORIZON_EVIDENCE.get(kind)
            if mapping is None:
                return empty, [
                    f"Horizon item {item.id} uses unsupported fact kind '{kind}'"]
            scope_problem = _horizon_fact_scope_problem(
                fact, scope, fact_kind=kind)
            if scope_problem:
                return empty, [
                    f"Horizon evidence {signal.evidence_id} {scope_problem}"]
            official = (_sam_url(signal.source) is not None
                        if kind == "sam_monitor"
                        else _official_government_source(signal.source))
            if not official:
                return empty, [
                    f"Horizon evidence {signal.evidence_id} is not on an "
                    "allowlisted official government source"]
            retrieved = _parse_datetime(
                fact.get("retrieved_at"), require_timezone=True)
            if retrieved is None:
                return empty, [
                    f"Horizon evidence {signal.evidence_id} lacks a "
                    "timezone-aware retrieval time"]
            tier, evidence_kind, primary, supports = mapping
            try:
                evidence.append(EvidenceRef(
                    evidence_id=("ev:horizon:v2:"
                                 + _digest({"id": signal.evidence_id,
                                            "source": signal.source})),
                    tier=tier, kind=evidence_kind,
                    source_name=f"Horizon {kind.replace('_', ' ')}",
                    source_url=signal.source,
                    retrieved_at=retrieved,
                    source_acquisition=acquisition_clock(fact.get("retrieved_at"),
                        basis="horizon_fact_bank", component="horizon_fact",
                        field="fact.retrieved_at", binding=_digest(fact)),
                    excerpt=signal.text,
                    record_hash=_digest(fact),
                    primary_source=primary,
                    supports=supports,
                ))
            except Exception as exc:  # noqa: BLE001 - evidence fails closed
                return empty, [
                    f"Horizon evidence {signal.evidence_id} is not ledger-safe: {exc}"]
        try:
            items.append(OpportunityThesis(
                thesis_id=f"thesis:horizon:v2:{_digest(item.id)}",
                title=item.title,
                predicted_event=item.projection,
                agency=item.where,
                lifecycle_stage=item.lifecycle_stage,
                projected_window=ProjectedWindow(label=item.window),
                evidence=evidence,
                inference_chain=item.pattern,
                falsifier=item.falsifier,
                watch_trigger=item.watch_trigger,
                monitoring_cadence=item.monitoring_cadence,
                evidence_strength=EvidenceStrength(item.confidence),
                status=status,
            ))
        except Exception as exc:  # noqa: BLE001 - whole thesis set fails closed
            return empty, [f"Horizon item {item.id} is not ledger-safe: {exc}"]

    return OpportunityThesisLedger(
        run_id=run_id, client_name=client_name,
        profile_version=profile_version, scope=scope, as_of=as_of,
        items=items,
    ), []


def _value(obj: Any, name: str, default: Any = None) -> Any:
    if isinstance(obj, dict):
        return obj.get(name, default)
    return getattr(obj, name, default)


def _agency_matches(row_agency: Any, opportunity_agency: Any,
                    row_subagency: Any = None,
                    opportunity_component: Any = None) -> bool:
    from tools.api.usaspending import normalize_agency_name

    opportunity_parts = [part.strip() for part in _text(opportunity_agency).split("/")
                         if part.strip()]
    component = _text(opportunity_component)
    if not component and len(opportunity_parts) > 1:
        component = opportunity_parts[-1]
    if component:
        candidate_buyer = " ".join((
            _text(row_agency), _text(row_subagency))).strip()
        try:
            from tools.agencies import find, matches_record
            target = find(component)
            if target:
                abbr = _text(target.get("abbr"))
                return (matches_record(candidate_buyer, target)
                        or bool(abbr and re.search(
                            rf"\b{re.escape(abbr)}\b", candidate_buyer,
                            flags=re.I)))
        except Exception:  # noqa: BLE001 - fallback remains fail-closed
            pass
        component_tokens = set(re.findall(r"[a-z0-9]+", component.lower()))
        buyer_tokens = set(re.findall(r"[a-z0-9]+", candidate_buyer.lower()))
        return bool(component_tokens and component_tokens <= buyer_tokens)
    left = normalize_agency_name(_text(row_agency).split("/")[0])
    right = normalize_agency_name(_text(opportunity_agency).split("/")[0])
    return bool(left and right and left.strip().lower() == right.strip().lower())


def partner_opportunity_core_terms(profile, notice: dict) -> frozenset[str]:
    """Client-core terms present in one authoritative selected notice.

    This is the shared Assess/C1 half of the opportunity-specific subaward
    predicate.  It deliberately ignores retrieval tags and NAICS labels.
    """
    from tools.capability import match_capability, term_regex

    raw = notice.get("raw_payload") \
        if isinstance(notice.get("raw_payload"), dict) else {}
    opportunity_blob = " ".join((
        _text(notice.get("title")),
        _text(raw.get("description_snippet")),
    ))
    opportunity_match = match_capability(profile, opportunity_blob)
    if opportunity_match.tier != "core":
        return frozenset()
    return frozenset(
        term.casefold() for term in profile.capability_terms.core
        if term and term_regex(term).search(opportunity_blob)
    )


def qualify_partner_subaward_edge(
    edge: dict,
    *,
    profile,
    opportunity_terms: frozenset[str],
    opportunity_agency: Any,
    opportunity_component: Any = None,
) -> tuple[frozenset[str], str]:
    """Strict record predicate shared by Assess and the public composer.

    Success means the edge belongs to the same target agency/component, its
    description independently clears the client CORE bar with a term shared
    by this exact selected notice, and it carries a native subaward identity
    plus an official USAspending source.  The caller may impose additional
    publication requirements such as a canonical prime-award GID.
    """
    from tools.capability import match_capability, term_regex

    if not opportunity_terms:
        return frozenset(), "selected notice has no client-core term"
    if not _agency_matches(
            edge.get("awarding_agency"), opportunity_agency,
            edge.get("awarding_sub_agency"), opportunity_component):
        return frozenset(), "subaward buyer does not match selected play"
    description = _text(edge.get("description"))
    if not description:
        return frozenset(), "subaward description is absent"
    edge_match = match_capability(profile, description)
    if edge_match.tier != "core":
        return frozenset(), "subaward description has no client-core match"
    edge_core_terms = {
        term.casefold() for term in profile.capability_terms.core
        if term and term_regex(term).search(description)
    }
    common = frozenset(opportunity_terms & edge_core_terms)
    if not common:
        return frozenset(), "subaward and selected play share no core term"
    native_id = _text(edge.get("subaward_id"))
    if not native_id:
        return frozenset(), "subaward native identity is absent"
    if not _official_usaspending_source(edge.get("source")):
        return frozenset(), "subaward source is not official USAspending"
    return common, "qualified"


def _partner_coverage(
    *,
    plays: list[Any],
    results: dict,
) -> tuple[SourceCoverage, ...]:
    if not plays:
        return ()
    subs = results.get("subawards")
    edge_count = 0
    failed_lanes = 0
    if isinstance(subs, dict):
        edges = subs.get("edges")
        if isinstance(edges, dict):
            for rows in edges.values():
                if not isinstance(rows, list):
                    failed_lanes += 1
                elif rows and all(isinstance(row, dict) and row.get("error")
                                  for row in rows):
                    failed_lanes += 1
                else:
                    edge_count += len([row for row in rows
                                       if isinstance(row, dict)
                                       and not row.get("error")])
    if not isinstance(subs, dict):
        sub_status = CoverageStatus.PARTIAL
        sub_note = "subaward attempt is absent or malformed"
    elif subs.get("error"):
        sub_status = CoverageStatus.FAILED
        sub_note = _text(subs.get("error"))
    elif failed_lanes:
        sub_status = CoverageStatus.PARTIAL
        sub_note = f"{failed_lanes} subaward lane(s) failed; collector is capped"
    else:
        sub_status = CoverageStatus.PARTIAL
        sub_note = "subaward rows screened, but the collector is capped without a total"
    rows = [SourceCoverage(
        source="partner_subaward_capability",
        lane=SourceLane.PARTNER,
        status=sub_status,
        required=True,
        records_screened=edge_count,
        total_available=None,
        note=sub_note,
    )]
    blocker_kinds = {
        _text(_value(blocker, "kind")).upper()
        for play in plays for blocker in (_value(play, "blockers", []) or [])
        if _value(blocker, "fired", False)
    }
    unavailable = {
        "VEHICLE": (
            "partner_vehicle_holders",
            "no official vehicle-holder registry adapter is registered"),
        "ELIGIBILITY": (
            "partner_certifications",
            "no candidate-specific certification registry is registered"),
        "INCUMBENCY": (
            "partner_program_incumbency",
            "no exact opportunity/program incumbent source is registered"),
    }
    for kind, (source, note) in unavailable.items():
        if kind in blocker_kinds:
            rows.append(SourceCoverage(
                source=source, lane=SourceLane.PARTNER,
                status=CoverageStatus.NOT_REGISTERED,
                required=True, note=note,
            ))
    if "SCALE" in blocker_kinds:
        rows.append(SourceCoverage(
            source="partner_capacity_awards", lane=SourceLane.PARTNER,
            status=CoverageStatus.PARTIAL, required=True,
            note="lane awards are capped and cannot prove candidate capacity exhaustively",
        ))
    return tuple(rows)


def adapt_partner_ledger(
    document: Any,
    searches: dict,
    profile: Any,
    live: LiveSolicitationLedger,
    posting_index: dict[str, str],
    accepted_notices: dict[str, dict],
    *,
    run_id: str,
    client_name: str,
    scope: AssessScope,
    profile_version: str,
    as_of: datetime,
) -> tuple[PartnerOpportunityLedger, list[SourceCoverage], list[str]]:
    """Promote only opportunity-specific, capability-matched teaming paths."""
    empty = PartnerOpportunityLedger(
        run_id=run_id, client_name=client_name,
        profile_version=profile_version, scope=scope, as_of=as_of,
    )
    partnering = _value(document, "partnering") if document is not None else None
    plays = list(_value(partnering, "plays", []) or [])
    if not plays:
        return empty, [], []

    results = searches.get("results") or {}
    notices = accepted_notices
    live_by_id = {record.record_id: record for record in live.records}
    subs = results.get("subawards")
    edges_by_naics = ((subs or {}).get("edges")
                      if isinstance(subs, dict) else {})
    edges_by_naics = edges_by_naics if isinstance(edges_by_naics, dict) else {}
    coverage = list(_partner_coverage(plays=plays, results=results))
    diagnostics: list[str] = []
    proposed: dict[tuple[str, str, PartnerDirection], dict] = {}

    from tools.entity_lineage import normalize_company, resolve_entity_result

    entity_cache: dict[str, str] = {}

    def _pure_door(name: str) -> str:
        raw_name = _text(name)
        if raw_name in entity_cache:
            return entity_cache[raw_name]
        canonical, reason = resolve_entity_result(raw_name)
        if canonical is None and reason == "ambiguous":
            diagnostics.append(
                f"Partner entity '{raw_name}' is ambiguous in the lineage crosswalk")
        key = canonical or normalize_company(raw_name)
        entity_cache[raw_name] = key
        return key

    client_door = _pure_door(client_name)
    supported_blockers = {"ADJACENCY"}
    for play in plays:
        source_id = _text(_value(play, "source_id"))
        linked_id = posting_index.get(source_id)
        live_record = live_by_id.get(linked_id or "")
        notice = notices.get(source_id)
        if (not linked_id or live_record is None or notice is None
                or live_record.notice_id != source_id):
            diagnostics.append(
                f"Partner play {source_id or '?'} has no exact live-record linkage")
            continue
        fired = [blocker for blocker in (_value(play, "blockers", []) or [])
                 if _value(blocker, "fired", False)]
        blocker_kinds = {
            _text(_value(blocker, "kind")).upper() for blocker in fired}
        unsupported = sorted(blocker_kinds - supported_blockers)
        if unsupported:
            diagnostics.append(
                f"Partner play {source_id} requires unregistered blocker evidence: "
                + ", ".join(unsupported))
            continue

        opportunity_terms = partner_opportunity_core_terms(profile, notice)
        if not opportunity_terms:
            diagnostics.append(
                f"Partner play {source_id} has no core capability term in "
                "authoritative opportunity text")
            continue
        raw = notice.get("raw_payload") \
            if isinstance(notice.get("raw_payload"), dict) else {}
        naics = _text(notice.get("naics_code") or raw.get("naics"))
        raw_edges = edges_by_naics.get(naics) or []
        raw_edges = [edge for edge in raw_edges
                     if isinstance(edge, dict) and not edge.get("error")]

        for candidate in _value(play, "candidates", []) or []:
            partner_name = _text(_value(candidate, "name"))
            partner_door = _pure_door(partner_name)
            if not partner_name or not partner_door or partner_door == client_door:
                continue
            candidate_directions = set(_value(candidate, "directions", []) or [])
            for raw_direction, direction, role_field in (
                ("SUB_TO_PRIME", PartnerDirection.SUB_TO_PRIME, "prime"),
                ("PRIME_WITH_SUBS", PartnerDirection.PRIME_TO_SUB, "sub"),
            ):
                if raw_direction not in candidate_directions:
                    continue
                matched = []
                shared_terms: set[str] = set()
                for edge in raw_edges:
                    if _pure_door(_text(edge.get(role_field))) != partner_door:
                        continue
                    common, _reason = qualify_partner_subaward_edge(
                        edge, profile=profile,
                        opportunity_terms=opportunity_terms,
                        opportunity_agency=live_record.agency,
                        opportunity_component=live_record.component,
                    )
                    if not common:
                        continue
                    description = _text(edge.get("description"))
                    native_id = _text(edge.get("subaward_id"))
                    source = _text(edge.get("source"))
                    excerpt = (
                        f"Subaward {native_id}: {_text(edge.get('prime'))} to "
                        f"{_text(edge.get('sub'))}. {description}")
                    edge_identity = {
                        "subaward_id": native_id,
                        "prime_award_id": _text(edge.get("prime_award_id")),
                        "prime": _text(edge.get("prime")),
                        "sub": _text(edge.get("sub")),
                    }
                    try:
                        evidence = EvidenceRef(
                            evidence_id=("ev:usaspending:subaward:v1:"
                                         + _digest(edge_identity)),
                            tier=EvidenceTier.MARKET,
                            kind=EvidenceKind.SUBAWARD,
                            source_name="USAspending subaward record",
                            source_url=source,
                            retrieved_at=None,
                            source_acquisition=acquisition_clock(None, basis="none",
                                component="subaward_record", field="unrecorded", binding=_digest(edge)),
                            observed_date=_parse_date(edge.get("date")),
                            excerpt=excerpt,
                            record_hash=_digest(edge),
                            primary_source=True,
                            supports=(EvidenceUse.CAPABILITY, EvidenceUse.BUYER),
                        )
                    except Exception:
                        continue
                    matched.append(evidence)
                    shared_terms.update(common)
                if not matched:
                    continue
                key = (linked_id, partner_door, direction)
                slot = proposed.setdefault(key, {
                    "name": partner_name,
                    "evidence": {},
                    "terms": set(),
                    "blockers": blocker_kinds,
                    "dual_role": bool(_value(candidate, "dual_role")),
                })
                for evidence in matched:
                    slot["evidence"][evidence.evidence_id] = evidence
                slot["terms"].update(shared_terms)

    items = []
    for (linked_id, partner_door, direction), slot in sorted(
            proposed.items(), key=lambda pair: (
                pair[0][0], pair[0][2].value, pair[0][1])):
        partner_name = slot["name"]
        terms = ", ".join(sorted(slot["terms"]))
        blockers = ", ".join(sorted(slot["blockers"])) or "partner access"
        if direction == PartnerDirection.SUB_TO_PRIME:
            role = (f"Subcontracting hypothesis: {partner_name} leads the "
                    f"buyer relationship and the client supplies {terms}.")
        else:
            role = (f"Prime-to-sub hypothesis: the client leads and "
                    f"{partner_name} supplies evidenced {terms} delivery.")
        risks = ["Candidate workshare need is not yet verified"]
        if slot["dual_role"]:
            risks.append("Candidate also appears in the competitive landscape")
        partner_id = "partner:v1:" + _digest({
            "linked_assess_id": linked_id,
            "partner_door": partner_door,
            "direction": direction.value,
        })
        items.append(PartnerOpportunity(
            partner_id=partner_id,
            partner_name=partner_name,
            linked_assess_ids=(linked_id,),
            direction=direction,
            role_hypothesis=role,
            client_needs_partner=(
                f"The opportunity-specific {blockers} blocker makes a "
                "partner path worth validating."),
            partner_needs_client=(
                f"Official subaward text overlaps the client's approved {terms} "
                "capability; an unmet partner need is not yet verified."),
            evidence=tuple(sorted(
                slot["evidence"].values(), key=lambda row: row.evidence_id)),
            risks=tuple(risks),
            next_validation_step=(
                f"Confirm {partner_name}'s current buyer access, capability gap, "
                "conflicts, and proposed workshare before outreach."),
            status=IntelligenceStatus.RESEARCH_NEEDED,
        ))

    return PartnerOpportunityLedger(
        run_id=run_id, client_name=client_name,
        profile_version=profile_version, scope=scope, as_of=as_of,
        items=items,
    ), coverage, diagnostics


def build_source_coverage(
    searches: dict,
    *,
    horizon_payload: Optional[dict] = None,
    horizon_diagnostics: tuple[str, ...] = (),
    partner_coverage: tuple[SourceCoverage, ...] = (),
    live_diagnostics: tuple[str, ...] = (),
) -> tuple[SourceCoverage, ...]:
    """Conservative, run-local coverage; unknown attempts never become zero."""
    results = searches.get("results") or {}
    sam = _sam_coverage(results)
    if any(message.startswith("SAM ledger rejected")
           or message.startswith("SAM posting")
           for message in live_diagnostics):
        sam_data = sam.model_dump(mode="python")
        sam_data["status"] = CoverageStatus.PARTIAL
        sam_data["note"] = (sam.note + "; malformed or ambiguous SAM rows were "
                            "rejected by the ledger adapter")
        sam = SourceCoverage.model_validate(sam_data)
    coverage = [sam]

    # These collectors do not yet persist a run-bound attempt manifest or an
    # uncapped total. Even successful payloads are therefore PARTIAL, never
    # presented as exhaustive coverage.
    lanes = (
        ("expiring_awards", SourceLane.HORIZON),
        ("forecast_signals", SourceLane.HORIZON),
        ("client_awards", SourceLane.HORIZON),
        ("budget_pressure", SourceLane.HORIZON),
        ("congress", SourceLane.HORIZON),
        ("watchdogs", SourceLane.HORIZON),
        ("federal_register", SourceLane.HORIZON),
        ("regulations_gov", SourceLane.HORIZON),
        ("cisa_kev", SourceLane.HORIZON),
        ("dod_contracts", SourceLane.HORIZON),
        ("contract_awards", SourceLane.HORIZON),
        ("subawards", SourceLane.PARTNER),
        ("incumbent_buyer_map", SourceLane.PARTNER),
    )
    required_plan_sources = {
        "expiring_awards", "forecast_signals", "budget_pressure",
        "congress", "watchdogs", "federal_register", "dod_contracts",
        "regulations_gov", "cisa_kev", "contract_awards", "subawards",
        "incumbent_buyer_map",
    }
    for source, lane in lanes:
        required = source in required_plan_sources
        value = results.get(source, _UNSET)
        if value is _UNSET:
            coverage.append(SourceCoverage(
                source=source, lane=lane, status=CoverageStatus.PARTIAL,
                required=required,
                note="attempt unknown: source is absent from this sweep artifact",
            ))
            continue
        if not isinstance(value, (dict, list)):
            coverage.append(SourceCoverage(
                source=source, lane=lane, status=CoverageStatus.FAILED,
                required=required,
                note="source payload is malformed",
            ))
            continue
        if isinstance(value, dict) and value.get("error"):
            coverage.append(SourceCoverage(
                source=source, lane=lane, status=CoverageStatus.FAILED,
                required=required,
                note=_text(value.get("error")),
            ))
            continue
        if isinstance(value, dict) and value.get("disabled") is True:
            coverage.append(SourceCoverage(
                source=source, lane=lane, status=CoverageStatus.FAILED,
                required=required,
                note=_text(value.get("note") or "registered source was disabled"),
            ))
            continue
        if isinstance(value, list):
            count = len(value)
        else:
            count = sum(len(v) for v in value.values() if isinstance(v, list))
        coverage.append(SourceCoverage(
            source=source, lane=lane, status=CoverageStatus.PARTIAL,
            required=required,
            records_screened=count, total_available=None,
            note="collector returned data but has no run-bound attempt/total manifest",
        ))

    raw_horizon_set = ((horizon_payload or {}).get("set")
                       if isinstance(horizon_payload, dict) else None)
    raw_horizon_items = (raw_horizon_set.get("items")
                         if isinstance(raw_horizon_set, dict) else None)
    horizon_items = len(raw_horizon_items) if isinstance(raw_horizon_items, list) else 0
    if horizon_diagnostics:
        horizon_status = CoverageStatus.FAILED
        horizon_note = "; ".join(horizon_diagnostics)
    elif not horizon_payload:
        horizon_status = CoverageStatus.PARTIAL
        horizon_note = "Horizon thesis review has not been composed"
    elif horizon_payload.get("status") != "approved":
        horizon_status = CoverageStatus.PARTIAL
        horizon_note = "Horizon thesis set is awaiting explicit human approval"
    else:
        horizon_status = CoverageStatus.COMPLETE
        horizon_note = "Approved Horizon thesis gate passed strict validation"
    coverage.append(SourceCoverage(
        source="horizon_thesis_gate", lane=SourceLane.HORIZON,
        status=horizon_status, required=True,
        records_screened=horizon_items,
        note=horizon_note,
    ))
    coverage.extend(partner_coverage)

    return tuple(sorted(coverage, key=lambda row: (row.lane.value, row.source)))


def _run_projection(
    *,
    as_of: datetime,
    scope: AssessScope,
    profile_version: str,
    live: LiveSolicitationLedger,
    horizon: OpportunityThesisLedger,
    partners: PartnerOpportunityLedger,
    coverage: tuple[SourceCoverage, ...],
    approval_status: GateStatus,
    approved_by: Optional[str],
    approved_at: Optional[datetime],
    partial_release_approved: bool,
    requires_human_review: bool,
    projection_inputs: dict[str, str],
    diagnostics: tuple[str, ...],
    posting_index: dict[str, str],
) -> dict:
    """Canonical, persistable basis for the immutable Assess run id."""
    return {
        "as_of": as_of.astimezone(timezone.utc).isoformat(),
        "scope": scope.model_dump(mode="json"),
        "profile_version": profile_version,
        "live": live.model_dump(mode="json", exclude={"run_id"}),
        "horizon": horizon.model_dump(mode="json", exclude={"run_id"}),
        "partners": partners.model_dump(mode="json", exclude={"run_id"}),
        "coverage": [row.model_dump(mode="json") for row in coverage],
        "approval_status": approval_status.value,
        "approved_by": approved_by,
        "approved_at": (approved_at.astimezone(timezone.utc).isoformat()
                        if approved_at else None),
        "partial_release_approved": partial_release_approved,
        "requires_human_review": requires_human_review,
        "projection_inputs": dict(sorted(projection_inputs.items())),
        "diagnostics": list(sorted(set(diagnostics))),
        "posting_index": dict(sorted(posting_index.items())),
    }


def _contains_acquisition(value):
    if isinstance(value, dict):
        return "source_acquisition" in value or any(_contains_acquisition(v) for v in value.values())
    return isinstance(value, (list, tuple)) and any(_contains_acquisition(v) for v in value)


def _legacy_projection(value):
    if isinstance(value, dict):
        return {k: _legacy_projection(v) for k, v in value.items() if k != "source_acquisition"}
    if isinstance(value, (list, tuple)):
        return [_legacy_projection(v) for v in value]
    return value


def _run_identity(client_name: str, binding: dict, projection: Any, *, schema_version=_SCHEMA_VERSION) -> str:
    basis = {
        "schema_version": schema_version,
        "client_name": _text(client_name),
        "binding": binding,
        "projection": projection,
    }
    return f"assess:v{schema_version}:" + _digest(basis)


def build_assess_run(
    client_name: str,
    searches: dict,
    profile: Any,
    binding: dict,
    *,
    horizon_payload: Optional[dict] = None,
    approval_payload: Optional[dict] = None,
    approval_status: str = "missing",
    document: Any = None,
    requirement_reviews_payload: Optional[dict] = None,
    reviewed_cases=None,
    projection_inputs: Optional[dict[str, str]] = None,
    extra_diagnostics: tuple[str, ...] = (),
    as_of: Optional[datetime] = None,
) -> tuple[AssessRun, tuple[str, ...], dict[str, str]]:
    """Build a strict run from already loaded artifacts, with no I/O."""
    if not isinstance(searches, dict) or not isinstance(searches.get("results"), dict):
        raise AssessLedgerError("Assess sweep is missing its results object")
    if not getattr(profile, "is_populated", lambda: False)():
        raise AssessLedgerError("Assess capability profile is not populated")
    if searches.get("client") and searches.get("client") != client_name:
        raise AssessLedgerError("Assess sweep belongs to a different client")
    if getattr(profile, "client_name", client_name) != client_name:
        raise AssessLedgerError("Assess capability profile belongs to a different client")
    scope = scope_from_sweep(searches)
    if binding.get("scope_designator") != _scope_designator(scope):
        raise AssessLedgerError("Assess binding and sweep scope do not match")
    profile_version = _text(binding.get("profile_sha256"))
    if not profile_version:
        raise AssessLedgerError("Assess binding has no capability profile hash")
    base_as_of = (_parse_datetime(as_of, require_timezone=True) if as_of
                  else _parse_datetime(
                      searches.get("generated_at"), require_timezone=True))
    if base_as_of is None:
        raise AssessLedgerError("Assess sweep has no deterministic generated_at")
    projection_inputs = dict(sorted((projection_inputs or {}).items()))

    gate = GateStatus.PENDING
    approved_by = None
    approved_at = None
    partial_release_requested = False
    approved_blocker_rows: Any = None
    approved_blocker_sha256: Any = None
    approval_diagnostics: list[str] = []
    if approval_status == "approved" and approval_payload:
        stored_approval_binding = approval_payload.get("binding") \
            if isinstance(approval_payload, dict) else None
        approval_binding_keys = {
            "version", "scope_designator", "sweep_artifact",
            "sweep_sha256", "profile_sha256",
        }
        binding_matches = (
            isinstance(stored_approval_binding, dict)
            and all(stored_approval_binding.get(key) == binding.get(key)
                    for key in approval_binding_keys)
            and approval_payload.get("client") == client_name
        )
        parsed = _parse_datetime(
            approval_payload.get("approved_at"), require_timezone=True)
        if parsed and binding_matches:
            gate = GateStatus.APPROVED
            approved_by = _text(approval_payload.get("approved_by")) or "operator"
            approved_at = parsed
            if approval_payload.get("partial_release_approved") is True:
                partial_release_requested = True
                approved_blocker_rows = approval_payload.get(
                    "partial_release_blockers")
                approved_blocker_sha256 = approval_payload.get(
                    "partial_release_blockers_sha256")
        elif not binding_matches:
            approval_diagnostics.append(
                "Assess approval client or binding does not match this run")
        else:
            approval_diagnostics.append(
                "Assess approval timestamp is missing, invalid, or timezone-naive")

    provisional_run_id = _run_identity(
        client_name, binding, {"phase": "projection"})
    def _adapt_evidence(effective_as_of: datetime):
        adapted_live, adapted_index, accepted, live_gaps = adapt_live_ledger(
            searches, profile, run_id=provisional_run_id, scope=scope,
            profile_version=profile_version, as_of=effective_as_of,
            requirement_reviews_payload=requirement_reviews_payload,
            binding=binding,
        )
        adapted_horizon, horizon_gaps = adapt_horizon_ledger(
            horizon_payload, binding, run_id=provisional_run_id,
            client_name=client_name, scope=scope,
            profile_version=profile_version, as_of=effective_as_of)
        if reviewed_cases is not None:
            from agents.assess.reviewed_cases import supplement_live
            adapted_live = supplement_live(adapted_live, reviewed_cases,
                                           _scope_designator(scope))
        return (adapted_live, adapted_index, accepted, list(live_gaps),
                adapted_horizon, list(horizon_gaps))

    (live, posting_index, accepted_notices, live_gap_list,
     horizon, horizon_diagnostics) = _adapt_evidence(base_as_of)
    valid_observation_times = [base_as_of]
    if approved_at:
        valid_observation_times.append(approved_at)
    for record in live.records:
        valid_observation_times.append(record.verified_at)
        valid_observation_times.extend(
            evidence.retrieved_at for evidence in record.authoritative_evidence
            if evidence.retrieved_at is not None)
    for thesis in horizon.items:
        valid_observation_times.extend(
            evidence.retrieved_at
            for evidence in (*thesis.evidence, *thesis.counterevidence)
            if evidence.retrieved_at is not None)
    if (not horizon_diagnostics and isinstance(horizon_payload, dict)
            and horizon_payload.get("status") == "approved"):
        horizon_approved_at = _parse_datetime(
            horizon_payload.get("approved_at"), require_timezone=True)
        if horizon_approved_at:
            valid_observation_times.append(horizon_approved_at)
    as_of = max(valid_observation_times)
    if as_of != base_as_of:
        (live, posting_index, accepted_notices, live_gap_list,
         horizon, horizon_diagnostics) = _adapt_evidence(as_of)

    diagnostics = [*live_gap_list, *horizon_diagnostics,
                   *approval_diagnostics]
    live_diagnostics = tuple(live_gap_list)

    # Partner projection runs only after the validated evidence clocks establish
    # the run cutoff; rejected optional artifacts cannot move live deadlines.
    partners, partner_coverage, partner_diagnostics = adapt_partner_ledger(
        document, searches, profile, live, posting_index, accepted_notices,
        run_id=provisional_run_id, client_name=client_name, scope=scope,
        profile_version=profile_version, as_of=as_of)
    diagnostics.extend(partner_diagnostics)

    diagnostics.extend(extra_diagnostics)
    coverage = build_source_coverage(
        searches, horizon_payload=horizon_payload,
        horizon_diagnostics=tuple(horizon_diagnostics),
        partner_coverage=tuple(partner_coverage),
        live_diagnostics=live_diagnostics)
    partial_release_approved = False
    if partial_release_requested:
        # A partial authorization is not a loose override.  It is permission
        # for one exact, operator-visible set of required source gaps.  Any
        # change in those rows requires a fresh explicit decision.
        from agents.assess.approval import required_blocker_manifest

        current_blockers = required_blocker_manifest(coverage)
        stored_manifest_valid = (
            isinstance(approved_blocker_rows, list)
            and isinstance(approved_blocker_sha256, str)
            and _digest(approved_blocker_rows) == approved_blocker_sha256
        )
        if (stored_manifest_valid
                and approved_blocker_rows == current_blockers["rows"]
                and approved_blocker_sha256 == current_blockers["sha256"]):
            partial_release_approved = True
        else:
            diagnostics.append(
                "Partial-release blocker manifest changed; explicit approval "
                "does not carry forward")
    diagnostics_out = tuple(sorted(set(diagnostics)))
    projection = _run_projection(
        as_of=as_of, scope=scope, profile_version=profile_version,
        live=live, horizon=horizon, partners=partners, coverage=coverage,
        approval_status=gate, approved_by=approved_by,
        approved_at=approved_at,
        partial_release_approved=partial_release_approved,
        requires_human_review=True, projection_inputs=projection_inputs,
        diagnostics=diagnostics_out,
        posting_index=posting_index,
    )
    run_id = _run_identity(client_name, binding, projection)

    def _rebind(ledger, model):
        data = ledger.model_dump(mode="python")
        data["run_id"] = run_id
        return model.model_validate(data)

    live = _rebind(live, LiveSolicitationLedger)
    horizon = _rebind(horizon, OpportunityThesisLedger)
    partners = _rebind(partners, PartnerOpportunityLedger)

    run = AssessRun(
        run_id=run_id, client_name=client_name,
        profile_version=profile_version, scope=scope, as_of=as_of,
        coverage=coverage,
        live=live, horizon=horizon, partners=partners,
        approval_status=gate, approved_by=approved_by,
        approved_at=approved_at,
        partial_release_approved=partial_release_approved,
    )
    return run, diagnostics_out, posting_index


def _state_dir(value: Optional[os.PathLike | str] = None) -> Path:
    if value is not None:
        return Path(value)
    return Path(os.environ.get("LILA_ASSESS_RUN_DIR", str(_DEFAULT_STATE)))


def assess_client_storage_key(client_name: str) -> str:
    """Collision-resistant directory name; readable slug plus exact-name hash."""
    normalized = _text(client_name).casefold()
    return f"{_slug(client_name)}--{_digest(normalized)[:12]}"


def current_assess_pointer_path(
    client_name: str,
    scope_designator: str = "all",
    *,
    state_dir: Optional[os.PathLike | str] = None,
) -> Path:
    """Return the one mutable pointer for an exact client and scope.

    This is a path resolver only. Callers must still use
    :func:`load_current_assess_run` to validate the pointer, immutable
    artifact checksum, run identity, and nested contracts before consuming
    any projected intelligence.
    """
    if not re.fullmatch(r"all|agency_[a-z0-9_]+", scope_designator or ""):
        raise AssessLedgerError("invalid Assess scope designator")
    root = _state_dir(state_dir) / assess_client_storage_key(client_name)
    return root / f"{scope_designator}.current.json"


def assess_projection_input_manifest(
    client_name: str,
    *,
    review_dir: Optional[os.PathLike | str] = None,
) -> dict[str, str]:
    """Hashes of mutable, non-sweep inputs that shape the strict projection.

    The primary approval binding already covers scope, sweep, and capability
    profile. These smaller review/reference artifacts can change Horizon or
    partner output without changing that binding, so current-run checks must
    bind them separately.
    """
    from agents.review import REVIEW_DIR

    root = Path(review_dir or REVIEW_DIR)
    slug = _slug(client_name)

    def _file_hash(path: Path) -> str:
        try:
            return hashlib.sha256(path.read_bytes()).hexdigest()
        except FileNotFoundError:
            return "missing"
        except OSError as exc:
            return f"unreadable:{type(exc).__name__}"

    try:
        from tools.entity_lineage import crosswalk_path
        entity_crosswalk = Path(crosswalk_path())
    except Exception:  # noqa: BLE001 - an unavailable input stays explicit
        entity_crosswalk = Path("/__unavailable_entity_crosswalk__")

    return {
        "version": "1",
        "horizon_sha256": _file_hash(root / f"{slug}.horizon.json"),
        "live_requirements_sha256": _file_hash(
            root / f"{slug}.live_requirements.json"),
        "reviewed_cases_sha256": _file_hash(
            root / f"{slug}.reviewed_cases.json"),
        "qualify_sha256": _file_hash(root / f"{slug}.qualify.json"),
        "assess_approval_sha256": _file_hash(
            root / f"{slug}.assess_approval.json"),
        "partnering_profile_sha256": _file_hash(
            root / f"{slug}.partnering.json"),
        "vehicles_reference_sha256": _file_hash(
            _ROOT / "data" / "reference" / "vehicles.json"),
        "entity_crosswalk_sha256": _file_hash(entity_crosswalk),
    }


@contextmanager
def _scope_pointer_lock(pointer: Path):
    """Exclusive per-scope lock for the pointer compare-and-swap. Every LILA
    writer (refresh, activation, rollback) acquires it around read+compare+
    write, so an internal concurrent refresh can never recreate a pointer a
    sibling writer just removed. Best-effort on platforms without fcntl."""
    pointer.parent.mkdir(parents=True, exist_ok=True)
    lock_path = pointer.with_suffix(pointer.suffix + ".lock")
    fd = os.open(str(lock_path), os.O_CREAT | os.O_RDWR, 0o644)
    try:
        try:
            import fcntl
            fcntl.flock(fd, fcntl.LOCK_EX)
        except (ImportError, OSError):
            pass  # lock advisory-only where unavailable; CAS still compares
        yield
    finally:
        os.close(fd)


def _atomic_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    tmp = Path(tmp_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, indent=2, ensure_ascii=False, default=str)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(tmp, (path.stat().st_mode & 0o777) if path.exists() else 0o644)
        os.replace(tmp, path)
    finally:
        if tmp.exists():
            tmp.unlink()


def persist_assess_run(
    run: AssessRun,
    binding: dict,
    diagnostics: tuple[str, ...] = (),
    posting_index: Optional[dict[str, str]] = None,
    projection_inputs: Optional[dict[str, str]] = None,
    *,
    state_dir: Optional[os.PathLike | str] = None,
    expected_pointer: Any = _UNSET,
) -> Path:
    """Persist an immutable run plus an atomic scope-specific current pointer."""
    run = AssessRun.model_validate(run.model_dump(mode="python"))
    root = _state_dir(state_dir) / assess_client_storage_key(run.client_name)
    canonical_diagnostics = tuple(sorted(set(diagnostics)))
    canonical_posting_index = dict(sorted((posting_index or {}).items()))
    canonical_projection_inputs = dict(sorted((projection_inputs or {}).items()))
    identity_projection = _run_projection(
        as_of=run.as_of, scope=run.scope,
        profile_version=run.profile_version,
        live=run.live, horizon=run.horizon, partners=run.partners,
        coverage=run.coverage, approval_status=run.approval_status,
        approved_by=run.approved_by, approved_at=run.approved_at,
        partial_release_approved=run.partial_release_approved,
        requires_human_review=run.requires_human_review,
        projection_inputs=canonical_projection_inputs,
        diagnostics=canonical_diagnostics,
        posting_index=canonical_posting_index,
    )
    if _run_identity(run.client_name, binding, identity_projection) != run.run_id:
        raise AssessLedgerError(
            "Assess run id does not match its persisted evidence projection")
    artifact = {
        "schema_version": _SCHEMA_VERSION,
        "binding": binding,
        "projection_inputs": canonical_projection_inputs,
        "identity": {
            "algorithm": "sha256-canonical-json-v1",
            "projection_sha256": _digest(identity_projection),
        },
        "run": run.model_dump(mode="json"),
        "diagnostics": list(canonical_diagnostics),
        "posting_index": canonical_posting_index,
    }
    digest = run.run_id.rsplit(":", 1)[-1]
    path = root / f"{digest}.json"
    if path.exists():
        try:
            existing = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise AssessLedgerError(f"existing Assess run is unreadable: {exc}") from exc
        if existing != artifact:
            raise AssessLedgerError("immutable Assess run id has conflicting content")
    else:
        _atomic_json(path, artifact)
    pointer = root / f"{_scope_designator(run.scope)}.current.json"
    pointer_payload = {
        "schema_version": _SCHEMA_VERSION,
        "run_id": run.run_id,
        "artifact": path.name,
        "artifact_sha256": _digest(artifact),
    }
    # Serialize the whole compare-and-swap under a per-scope exclusive lock
    # (2026-07-12, Cycle 5 review P1: the check-to-replace window let a
    # concurrent LILA refresh/activation/rollback recreate a just-removed
    # pointer). current_bytes is read INSIDE the lock, immediately before the
    # write, so refresh, activation, and the sanctioned rollback command all
    # serialize on the same guard. Every writer takes this lock.
    with _scope_pointer_lock(pointer):
        try:
            current_bytes = pointer.read_bytes()
        except OSError:
            current_bytes = None
        if expected_pointer is not _UNSET:
            # A creation-free refresh carries the exact pointer bytes its
            # existence check observed. If the operator removed or replaced the
            # pointer while the refresh materialized, nothing is written; the
            # operator's pointer state stands. Activation is the only
            # expectation-free path.
            expected_bytes = (expected_pointer.get("bytes")
                              if isinstance(expected_pointer, dict) else None)
            if current_bytes is None or current_bytes != expected_bytes:
                raise AssessLedgerError(
                    "scope pointer changed during refresh (operator removal or "
                    "replacement); nothing was written and the operator's "
                    "pointer state stands")
        existing_pointer = None
        if current_bytes is not None:
            try:
                existing_pointer = json.loads(current_bytes.decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError):
                existing_pointer = None
        if existing_pointer != pointer_payload:
            _atomic_json(pointer, pointer_payload)
    return path


def load_current_assess_run(
    client_name: str,
    scope_designator: str = "all",
    *,
    state_dir: Optional[os.PathLike | str] = None,
) -> Optional[dict]:
    if not re.fullmatch(r"all|agency_[a-z0-9_]+", scope_designator or ""):
        return None
    pointer_path = current_assess_pointer_path(
        client_name, scope_designator, state_dir=state_dir)
    root = pointer_path.parent
    try:
        pointer = json.loads(pointer_path.read_text(encoding="utf-8"))
        artifact_name = pointer["artifact"]
        if Path(artifact_name).name != artifact_name:
            return None
        path = root / artifact_name
        payload = json.loads(path.read_text(encoding="utf-8"))
        version = payload.get("schema_version")
        if (type(version) is not int or version not in _SUPPORTED_SCHEMAS
                or pointer.get("schema_version") != version):
            return None
        if _digest(payload) != pointer.get("artifact_sha256"):
            return None
        if version == 1 and _contains_acquisition(payload.get("run")):
            return None  # a v2 receipt cannot be smuggled under a legacy identity
        run = AssessRun.model_validate(payload.get("run") or {})
        if not run.run_id.startswith(f"assess:v{version}:"):
            return None
        identity = payload.get("identity")
        if (not isinstance(identity, dict)
                or identity.get("algorithm") != "sha256-canonical-json-v1"):
            return None
        expected_projection = _run_projection(
            as_of=run.as_of, scope=run.scope,
            profile_version=run.profile_version,
            live=run.live, horizon=run.horizon, partners=run.partners,
            coverage=run.coverage, approval_status=run.approval_status,
            approved_by=run.approved_by, approved_at=run.approved_at,
            partial_release_approved=run.partial_release_approved,
            requires_human_review=run.requires_human_review,
            projection_inputs=dict(payload.get("projection_inputs") or {}),
            diagnostics=tuple(payload.get("diagnostics") or ()),
            posting_index=payload.get("posting_index") or {},
        )
        if version == 1:
            expected_projection = _legacy_projection(expected_projection)
        if (_text(run.client_name) != _text(client_name)
                or _scope_designator(run.scope) != scope_designator
                or run.run_id != pointer.get("run_id")
                or (payload.get("binding") or {}).get("scope_designator")
                != scope_designator
                or identity.get("projection_sha256")
                != _digest(expected_projection)
                or _run_identity(
                    run.client_name, payload.get("binding") or {},
                    expected_projection, schema_version=version) != run.run_id):
            return None
        return payload
    except (OSError, KeyError, TypeError, json.JSONDecodeError, ValueError):
        return None


def current_run_identity(
    client_name: str,
    designator: str,
    *,
    review_dir: Optional[str] = None,
) -> Optional[dict]:
    """Return the stable identity of an exact CURRENT Assess pointer.

    ``None`` has two deliberately different meanings at this non-throwing
    read seam: an absent pointer means the client has not been cut over, while
    a present pointer that cannot clear the established immutable-run,
    evidence-binding, or projection-input checks fails closed.  Invalid cases
    are logged so consumers can preserve their frozen return shapes without
    losing the operator-visible reason.
    """
    try:
        pointer_path = current_assess_pointer_path(client_name, designator)
        try:
            with pointer_path.open("rb") as handle:
                pointer_before = handle.read()
                stat_before = os.fstat(handle.fileno())
        except FileNotFoundError:
            return None

        payload = load_current_assess_run(client_name, designator)
        if payload is None:
            _LOG.warning(
                "current Assess identity unavailable for %r/%r: pointer or "
                "immutable run validation failed",
                client_name,
                designator,
            )
            return None

        # Snapshot the pointer again immediately after the established loader
        # has validated its artifact. Reading and statting the same open inode
        # keeps both snapshots internally coherent.
        with pointer_path.open("rb") as handle:
            pointer_after = handle.read()
            stat_after = os.fstat(handle.fileno())

        run = AssessRun.model_validate(payload.get("run") or {})

        # Exact bytes plus inode and mtime prevent an atomic replacement from
        # mixing one validated run with another pointer's identity metadata.
        pointer = json.loads(pointer_after)
        artifact_name = pointer.get("artifact") \
            if isinstance(pointer, dict) else None
        expected_artifact = f"{run.run_id.rsplit(':', 1)[-1]}.json"
        if (not isinstance(pointer, dict)
                or pointer.get("schema_version") not in _SUPPORTED_SCHEMAS
                or not isinstance(artifact_name, str)
                or Path(artifact_name).name != artifact_name
                or artifact_name != expected_artifact
                or pointer.get("run_id") != run.run_id
                or pointer.get("artifact_sha256") != _digest(payload)):
            _LOG.warning(
                "current Assess identity unavailable for %r/%r: current "
                "pointer schema or artifact fields are invalid",
                client_name,
                designator,
            )
            return None

        snapshot_before = (
            stat_before.st_dev,
            stat_before.st_ino,
            stat_before.st_mtime_ns,
            stat_before.st_size,
        )
        snapshot_after = (
            stat_after.st_dev,
            stat_after.st_ino,
            stat_after.st_mtime_ns,
            stat_after.st_size,
        )
        if pointer_before != pointer_after or snapshot_before != snapshot_after:
            _LOG.warning(
                "current Assess identity unavailable for %r/%r: pointer "
                "changed during validation",
                client_name,
                designator,
            )
            return None

        # Reuse the one evidence-binding owner rather than reproducing its
        # gate/sweep/profile resolution here. A structurally valid, coherent
        # pointer is not CURRENT once those mutable inputs drift.
        from agents.assess.approval import (
            current_assess_binding,
            required_blocker_manifest,
        )
        from agents.assess.binding import binding_drift

        current_binding = current_assess_binding(
            client_name, review_dir=review_dir)
        drift = binding_drift(
            payload.get("binding"),
            current_binding,
            subject="strict Assess run",
            remedy="refresh the strict run",
        )
        if current_binding.get("scope_designator") != designator:
            drift.append(
                "strict Assess run search scope changed; refresh the strict run")
        if drift:
            _LOG.warning(
                "current Assess identity unavailable for %r/%r: %s",
                client_name,
                designator,
                "; ".join(dict.fromkeys(drift)),
            )
            return None

        expected_inputs = assess_projection_input_manifest(
            client_name, review_dir=review_dir)
        if payload.get("projection_inputs") != expected_inputs:
            _LOG.warning(
                "current Assess identity unavailable for %r/%r: review or "
                "reference inputs changed; refresh the strict run",
                client_name,
                designator,
            )
            return None

        blockers = required_blocker_manifest(run.coverage)
        seconds, nanoseconds = divmod(
            stat_after.st_mtime_ns, 1_000_000_000)
        persisted_at = datetime.fromtimestamp(
            seconds, tz=timezone.utc).replace(
                microsecond=nanoseconds // 1_000)
        return {
            "run_id": run.run_id,
            "pointer_digest": _digest(pointer),
            "blocker_manifest_sha256": blockers["sha256"],
            "persisted_at": persisted_at.isoformat(),
        }
    except Exception as exc:  # noqa: BLE001 - this public read seam is fail-None
        _LOG.warning(
            "current Assess identity unavailable for %r/%r: %s",
            client_name,
            designator,
            exc,
        )
        return None


def materialize_current_assess_run(
    client_name: str,
    *,
    sweep_path: Optional[os.PathLike | str] = None,
    state_dir: Optional[os.PathLike | str] = None,
    review_dir: Optional[str] = None,
    expected_pointer: Any = _UNSET,
) -> tuple[AssessRun, Path, tuple[str, ...]]:
    """Load the gate-designated artifacts, build, and atomically persist."""
    from agents.assess.approval import (
        _assess_approval_for_materialization, current_assess_binding,
    )
    from agents.reports.document import build_document
    from agents.review import REVIEW_DIR, sweep_artifact_path
    from tools.capability import require_profile

    selected = Path(sweep_path or sweep_artifact_path(client_name))
    searches = json.loads(selected.read_text(encoding="utf-8"))
    profile = require_profile(client_name)
    binding = current_assess_binding(
        client_name, review_dir=review_dir, sweep_path=str(selected),
        sweep=searches, profile=profile,
    )
    q_root = Path(review_dir or REVIEW_DIR)
    projection_inputs = assess_projection_input_manifest(
        client_name, review_dir=q_root)
    approval, gate_status, approval_problems = \
        _assess_approval_for_materialization(
            client_name, current_binding=binding, review_dir=review_dir)
    horizon_problem = None
    horizon_path = q_root / f"{_slug(client_name)}.horizon.json"
    try:
        horizon = (json.loads(horizon_path.read_text(encoding="utf-8"))
                   if horizon_path.exists() else None)
    except (OSError, json.JSONDecodeError) as exc:
        horizon = None
        horizon_problem = (
            f"Horizon artifact unavailable: {type(exc).__name__}: "
            f"{str(exc)[:240]}")
    requirement_reviews_problem = None
    requirement_reviews_path = (
        q_root / f"{_slug(client_name)}.live_requirements.json")
    try:
        requirement_reviews = (
            json.loads(requirement_reviews_path.read_text(encoding="utf-8"))
            if requirement_reviews_path.exists() else None)
    except (OSError, json.JSONDecodeError) as exc:
        requirement_reviews = None
        requirement_reviews_problem = (
            f"Live requirement review artifact unavailable: "
            f"{type(exc).__name__}: {str(exc)[:240]}")
    as_of = _parse_datetime(
        searches.get("generated_at"), require_timezone=True)
    if as_of is None:
        raise AssessLedgerError(
            "Assess sweep generated_at is missing or timezone-naive")
    document = None
    document_problem = None
    try:
        q_path = q_root / f"{_slug(client_name)}.qualify.json"
        qualify = (json.loads(q_path.read_text(encoding="utf-8"))
                   if q_path.exists() else None)
        document = build_document(
            client_name, searches=searches, qualify=qualify,
            as_of=as_of.date(),
            # The ledger adapter may be refreshing a prior current pointer.
            # Its partner projection must not recursively consume that older
            # run while constructing the replacement evidence envelope.
            _live_report=False,
            # The exact profile object already bound above must also shape
            # partner/document projection; a mid-run disk edit cannot split
            # the immutable run across two profile versions.
            _cap_profile=profile)
    except Exception as exc:  # noqa: BLE001 - partner projection is additive
        document_problem = (
            f"Partner projection document unavailable: {type(exc).__name__}: "
            f"{str(exc)[:240]}")
    from agents.assess.reviewed_cases import load_cases
    reviewed_cases = load_cases(client_name, q_root)
    run, diagnostics, posting_index = build_assess_run(
        client_name, searches, profile, binding,
        horizon_payload=horizon, approval_payload=approval,
        approval_status=gate_status, document=document,
        requirement_reviews_payload=requirement_reviews,
        reviewed_cases=reviewed_cases,
        projection_inputs=projection_inputs, as_of=as_of,
        extra_diagnostics=(tuple(approval_problems)
                           + ((document_problem,) if document_problem else ())
                           + ((horizon_problem,) if horizon_problem else ())
                           + ((requirement_reviews_problem,)
                              if requirement_reviews_problem else ())),
    )
    path = persist_assess_run(
        run, binding, diagnostics, posting_index, projection_inputs,
        state_dir=state_dir, expected_pointer=expected_pointer)
    return run, path, diagnostics
