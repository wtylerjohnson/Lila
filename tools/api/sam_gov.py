"""SAM.gov adapter — the Discovery Trigger (live).

Calls SAM.gov's Get Opportunities API v2 and maps each notice to a RawOpportunity,
preserving the full payload and the public uiLink for traceability.

Requires a free api.data.gov / SAM.gov key in env SAM_GOV_API_KEY.
API: https://open.gsa.gov/api/get-opportunities-public-api/

Parsing is split into map_notice() so it can be verified against a real-shape
fixture without a key or network.
"""

from __future__ import annotations

import hashlib
import json
import os
import sys
import time
from datetime import date, datetime
from pathlib import Path
from typing import Any, Optional

import httpx

from agents.schemas import OpportunityContact, RawOpportunity
import tools.api.sam_quota as sam_quota  # direct submodule import: avoids the package-init cycle
from tools.api._http import get_json
from tools.api.base import DataSource, SourceKind, SourceQuery, register_source

SEARCH_URL = "https://api.sam.gov/opportunities/v2/search"
# SAM v2 routinely takes 30-60s on broad queries; the old 30s default turned
# slow-but-successful calls into "random" timeouts.
SAM_TIMEOUT = 75.0
# api.sam.gov sits behind an edge WAF that has been observed serving EMPTY
# responses to non-browser user agents (verified 2026-07-03: keyless calls that
# should 403 instantly returned nothing to a programmatic fetcher). Send
# browser-style headers so our calls don't look like a bot to the edge.
SAM_HEADERS = {
    "User-Agent": ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                   "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36"),
    "Accept": "application/json",
}
_DEFAULT_CACHE = Path(__file__).resolve().parents[2] / "data" / "cache" / "sam"
FRESH_TTL_S = int(os.environ.get("LILA_SAM_TTL_MIN", "45")) * 60


def _cache_dir() -> Path:
    return Path(os.environ.get("LILA_SAM_CACHE_DIR", str(_DEFAULT_CACHE)))


def _cache_key(call: dict) -> str:
    keyless = {k: v for k, v in call.items() if k != "api_key"}
    blob = json.dumps(keyless, sort_keys=True, default=str)
    return hashlib.sha1(blob.encode()).hexdigest()


def _cache_read(key: str) -> tuple[Optional[dict], float]:
    """Return (payload, age_seconds) — payload None on miss."""
    p = _cache_dir() / f"{key}.json"
    try:
        wrapped = json.loads(p.read_text())
        return wrapped["payload"], time.time() - float(wrapped["fetched_at"])
    except (OSError, ValueError, KeyError):
        return None, 0.0


def _cache_write(key: str, payload: dict) -> None:
    try:
        d = _cache_dir()
        d.mkdir(parents=True, exist_ok=True)
        (d / f"{key}.json").write_text(
            json.dumps({"fetched_at": time.time(), "payload": payload}))
    except OSError:
        pass  # cache is an optimization, never a failure


def _is_rate_limited(exc: Exception) -> bool:
    resp = getattr(exc, "response", None)
    if resp is not None and getattr(resp, "status_code", None) == 429:
        return True
    return isinstance(exc, httpx.HTTPStatusError) and "429" in str(exc)


def _parse_date(value: Any) -> Optional[date]:
    """Legacy date projection of a whole-value parsed source timestamp."""
    from tools.relevance.temporal import display_date
    return display_date(value)


def _parse_amount(award: Any) -> Optional[float]:
    if isinstance(award, dict) and award.get("amount") not in (None, ""):
        try:
            return float(award["amount"])
        except (TypeError, ValueError):
            return None
    return None


# SAM v2 typeOfSetAside codes -> the labels profiles/eligibility lists use.
# Unknown codes fall back to the description, then the raw code (never dropped).
_SET_ASIDE_LABELS = {
    "SBA": "Small Business",
    "SBP": "Partial Small Business",
    "8A": "8(a)",
    "8AN": "8(a) Sole Source",
    "HZC": "HUBZone",
    "HZS": "HUBZone Sole Source",
    "SDVOSBC": "SDVOSB",
    "SDVOSBS": "SDVOSB Sole Source",
    "WOSB": "WOSB",
    "WOSBSS": "WOSB Sole Source",
    "EDWOSB": "EDWOSB",
    "EDWOSBSS": "EDWOSB Sole Source",
    "LAS": "Local Area",
    "IEE": "Indian Economic Enterprise",
    "ISBEE": "Indian Small Business Economic Enterprise",
    "BICiv": "Buy Indian",
    "VSA": "VOSB",
    "VSS": "VOSB Sole Source",
}


def normalize_set_aside(code: Any, description: Any = None) -> Optional[str]:
    """Map SAM's set-aside CODE to the label eligibility lists use.

    SAM returns codes ('SDVOSBC') while CapabilityProfile stores labels ('SDVOSB');
    comparing them raw silently drops eligible deals — the exact false negative the
    prefilter design rule forbids.
    """
    if code and isinstance(code, str) and code.strip():
        c = code.strip()
        return _SET_ASIDE_LABELS.get(c) or (
            description.strip() if isinstance(description, str) and description.strip() else c
        )
    if isinstance(description, str) and description.strip():
        return description.strip()
    return None


def _parse_contacts(poc: Any) -> list[OpportunityContact]:
    """SAM's pointOfContact is a list of dicts (fullName/title/email/phone/type)."""
    if not isinstance(poc, list):
        return []
    out: list[OpportunityContact] = []
    for c in poc:
        if not isinstance(c, dict):
            continue
        contact = OpportunityContact(
            name=(c.get("fullName") or "").strip() or None,
            title=(c.get("title") or "").strip() or None,
            email=(c.get("email") or "").strip() or None,
            phone=(c.get("phone") or "").strip() or None,
            contact_type=(c.get("type") or "").strip() or None,
        )
        if any((contact.name, contact.email, contact.phone)):
            out.append(contact)
    return out


def map_notice(notice: dict) -> RawOpportunity:
    """Map one SAM.gov opportunitiesData entry to RawOpportunity."""
    from tools.relevance.temporal import source_time
    return RawOpportunity(
        source="sam.gov",
        source_id=notice.get("noticeId") or notice.get("solicitationNumber") or "",
        title=(notice.get("title") or "").strip(),
        agency=notice.get("fullParentPathName") or None,
        naics_code=notice.get("naicsCode") or None,
        psc_code=notice.get("classificationCode") or None,
        set_aside=normalize_set_aside(
            notice.get("typeOfSetAside"), notice.get("typeOfSetAsideDescription")
        ),
        posted_date=_parse_date(notice.get("postedDate")),
        response_deadline=_parse_date(notice.get("responseDeadLine")),
        estimated_value=_parse_amount(notice.get("award")),
        api_url=notice.get("uiLink") or None,
        contacts=_parse_contacts(notice.get("pointOfContact")),
        temporal_evidence={kind: source_time(notice.get(field), field='raw_payload.' + field,
                              source_id=notice.get('noticeId') or notice.get('solicitationNumber') or '')
                           for kind, field in (('deadline', 'responseDeadLine'), ('posted', 'postedDate'))},
        raw_payload=notice,
    )


@register_source
class SamGovSource(DataSource):
    name = "sam.gov"
    kind = SourceKind.DISCOVERY
    MAX_PASSES = 4  # cap SAM calls per search to conserve the key's rate limit

    def __init__(self, api_key: str | None = None) -> None:
        pool = [api_key or os.environ.get("SAM_GOV_API_KEY"),
                os.environ.get("SAM_GOV_API_KEY_2")]
        self._api_keys = [key for key in pool if key]
        self._key_index = 0
        self.last_status: dict[str, Any] = {}

    @property
    def _api_key(self) -> str | None:
        if not self._api_keys:
            return None
        return self._api_keys[self._key_index]

    def _advance_key(self) -> bool:
        """Rotate to the next configured key; False when the pool is spent."""
        if self._key_index + 1 >= len(self._api_keys):
            return False
        self._key_index += 1
        print(f"[sam.gov] key {self._key_index} throttled — rotating to the "
              f"next configured key ({self._key_index + 1}/"
              f"{len(self._api_keys)}).", file=sys.stderr)
        return True

    def healthcheck(self) -> tuple[bool, str]:
        # Deliberately no live call: rate limits are tight (10/day keyless-role).
        if not self._api_key:
            return False, "SAM_GOV_API_KEY missing"
        return True, f"key set · {sam_quota.summary()}"

    def search(self, query: SourceQuery) -> list[RawOpportunity]:
        """Query SAM.gov and return mapped RawOpportunity records.

        postedFrom/postedTo are REQUIRED by SAM and span at most one year, in
        MM/dd/yyyy format. Caller supplies them via SourceQuery.
        """
        # ZERO-SAM INSTRUMENTATION (repress tasking, 2026-08-03): a config
        # flag is not proof. With the tripwire armed (LILA_SAM_TRIPWIRE=1,
        # set on the press environment), ANY invocation of the metered SAM
        # adapter RAISES, so a press that would have spent quota dies loudly
        # instead. Store/extract reads never pass through here.
        import os as _os
        if _os.environ.get("LILA_SAM_TRIPWIRE", "").strip() == "1":
            raise RuntimeError(
                "LILA_SAM_TRIPWIRE: metered SAM adapter invoked while the "
                "tripwire is armed; the zero-SAM guarantee would have been "
                "violated")
        if not self._api_key:
            raise RuntimeError(
                "SAM_GOV_API_KEY not set. Get a free key at https://sam.gov/ "
                "(or api.data.gov) and export SAM_GOV_API_KEY."
            )
        if not (query.posted_from and query.posted_to):
            raise ValueError("SAM.gov requires posted_from and posted_to (max 1-year span).")
        # SAM rejects spans of a year or more with a 400; keep it strictly under.
        if (query.posted_to - query.posted_from).days > 364:
            raise ValueError(
                "SAM.gov date span must be < 1 year "
                f"(got {(query.posted_to - query.posted_from).days} days)."
            )

        params: dict[str, Any] = {
            "api_key": self._api_key,
            "postedFrom": query.posted_from.strftime("%m/%d/%Y"),
            "postedTo": query.posted_to.strftime("%m/%d/%Y"),
            "limit": min(query.limit, 1000),
            "offset": 0,
        }
        # Forward-looking gate: only opportunities still open for response.
        if query.deadline_from:
            params["rdlfrom"] = query.deadline_from.strftime("%m/%d/%Y")
        if query.deadline_to:
            params["rdlto"] = query.deadline_to.strftime("%m/%d/%Y")
        # SAM's `title` is a single substring match and `ncode` a single NAICS,
        # so every requested NAICS and keyword is a distinct attempt. Preserve
        # the complete requested/executed/omitted manifest: pagination can be
        # complete for the calls we made while the overall screen is still
        # incomplete because the quota cap omitted approved lanes.
        # Each pass is one SAM call; cap the fan-out (MAX_PASSES) and space calls to
        # avoid burst throttling. PREFER NAICS (ncode) passes: SAM's `title` param is a
        # narrow literal substring on the notice title only (no full-text), so for niche
        # vendors it misses most relevant notices. NAICS matches structurally for breadth;
        # relevance ranking happens downstream (Assess scoring + LLM fit).
        requested_specs: list[dict] = []
        for code in dict.fromkeys(
                str(code).strip() for code in query.naics_codes
                if str(code).strip()):
            requested_specs.append({
                "id": f"naics:{code}", "kind": "naics", "value": code,
                "params": {"ncode": code},
            })
        for keyword in dict.fromkeys(
                str(keyword).strip() for keyword in query.keywords
                if str(keyword).strip()):
            requested_specs.append({
                "id": f"keyword:{keyword}", "kind": "keyword",
                "value": keyword, "params": {"title": keyword},
            })
        if not requested_specs:
            requested_specs = [{
                "id": "base", "kind": "base", "value": "base",
                "params": {},
            }]
        executed_specs = requested_specs[:self.MAX_PASSES]
        omitted_specs = requested_specs[self.MAX_PASSES:]
        passes = [spec["params"] for spec in executed_specs]

        results: list[RawOpportunity] = []
        seen: set[str] = set()
        errors: list[str] = []
        n_live = n_fresh_cache = n_stale_cache = 0
        throttled = False
        # FILTER-FIRST (L18): focus agencies filter what this metered path
        # returns downstream (client-side match on fullParentPathName — the
        # extract path is primary; this fallback stays param-conservative).
        from tools.agencies import find as _find_agency, matches_record as _agency_match
        focus = [a for a in (_find_agency(n) for n in (query.agencies or [])) if a]

        census: dict[str, dict] = {}
        for i, (spec, extra) in enumerate(zip(executed_specs, passes)):
            call = dict(params)
            call.update(extra)
            # Set-aside is a ranking/eligibility dimension, not a discovery
            # boundary. Narrowing here would hide unrestricted and alternate
            # set-aside notices from the default full assessment.
            label = spec["value"]
            if label in census:
                label = spec["id"]

            # L17: paginate each pass to totalRecords. The daily budget is
            # guarded per page (warn at 80%, stop at 100%); a pass that cannot
            # reach totalRecords is INCOMPLETE — recorded, never silent.
            page_limit = call["limit"]
            offset = 0
            pass_retrieved = 0
            pass_total: Optional[int] = None
            pass_complete = True
            while True:
                call["offset"] = offset
                ckey = _cache_key(call)
                # Fresh cache first: a re-run within the TTL costs ZERO quota.
                cached, age = _cache_read(ckey)
                if cached is not None and age < FRESH_TTL_S:
                    payload = cached
                    n_fresh_cache += 1
                elif throttled or not sam_quota.guard("search"):
                    # Budget or rate limit hit — stale cache beats nothing.
                    payload = cached
                    if payload is not None:
                        n_stale_cache += 1
                    else:
                        pass_complete = False
                        errors.append(f"{label}@{offset}: skipped "
                                      f"(budget/rate limit, no cache)")
                        break
                else:
                    if (i or offset) and n_live:
                        time.sleep(0.8)  # space live calls; avoids burst throttling
                    try:
                        while True:
                            sam_quota.note_call("search")
                            call["api_key"] = self._api_key
                            try:
                                payload = get_json(SEARCH_URL, params=call,
                                                   timeout=SAM_TIMEOUT,
                                                   headers=SAM_HEADERS)
                                break
                            except Exception as rotate_exc:  # noqa: BLE001
                                if _is_rate_limited(rotate_exc) \
                                        and self._advance_key():
                                    continue
                                raise
                        _cache_write(ckey, payload)
                        n_live += 1
                    except Exception as exc:  # noqa: BLE001 — one bad page shouldn't zero the pass
                        if _is_rate_limited(exc):
                            throttled = True
                            print(f"[sam.gov] RATE LIMITED after {sam_quota.calls_today()} "
                                  f"calls today — falling back to cache. Permanent fix: "
                                  f"the daily pool is spent. The extract path is "
                                  f"the primary and costs nothing.",
                                  file=sys.stderr)
                        payload = cached
                        if payload is not None:
                            n_stale_cache += 1
                        else:
                            pass_complete = False
                            errors.append(f"{label}@{offset}: {exc}")
                            break

                page = payload.get("opportunitiesData", []) or []
                if pass_total is None:
                    try:
                        pass_total = int(payload.get("totalRecords"))
                    except (TypeError, ValueError):
                        pass_total = len(page)
                for notice in page:
                    opp = map_notice(notice)
                    # Belt and suspenders on the forward-looking gate: a notice
                    # with a deadline already past is dead inventory. Keep
                    # None-deadline notices (sources sought / RFIs) — those are
                    # exactly the forward signals.
                    if (query.deadline_from and opp.response_deadline
                            and opp.response_deadline < query.deadline_from):
                        continue
                    if focus and not any(_agency_match(opp.agency or "", a)
                                         for a in focus):
                        continue  # L18: metered path returns the focus slice
                    if opp.source_id and opp.source_id not in seen:
                        seen.add(opp.source_id)
                        results.append(opp)
                pass_retrieved += len(page)
                offset += page_limit
                if not page or pass_retrieved >= (pass_total or 0):
                    break

            if pass_total is not None and pass_retrieved < pass_total:
                pass_complete = False
            census[label] = {"retrieved": pass_retrieved, "total_records": pass_total,
                             "complete": pass_complete}

        attempt_manifest = {
            "requested": [{key: spec[key] for key in ("id", "kind", "value")}
                          for spec in requested_specs],
            "executed": [{key: spec[key] for key in ("id", "kind", "value")}
                         for spec in executed_specs],
            "omitted": [{key: spec[key] for key in ("id", "kind", "value")}
                        for spec in omitted_specs],
        }
        pagination_complete = (
            all(c["complete"] for c in census.values()) if census else False)
        if omitted_specs:
            errors.append(
                f"{len(omitted_specs)} requested SAM pass(es) omitted by the "
                f"{self.MAX_PASSES}-pass safety cap")
        self.last_status = {
            "passes": len(passes), "live": n_live, "fresh_cache": n_fresh_cache,
            "stale_cache": n_stale_cache, "failed": len(errors),
            "throttled": throttled, "calls_today": sam_quota.calls_today(),
            "census": census,
            "attempt_manifest": attempt_manifest,
            "complete": pagination_complete and not omitted_specs,
        }
        if errors:
            print(f"[sam.gov] {len(errors)}/{len(passes)} passes returned nothing usable: "
                  f"{'; '.join(errors)}", file=sys.stderr)
        # Only surface an error if we got nothing AND every pass failed (e.g. all 429).
        if not results and errors and len(errors) == len(passes):
            raise RuntimeError(f"all {len(passes)} SAM passes failed; last: {errors[-1]}")
        return results
