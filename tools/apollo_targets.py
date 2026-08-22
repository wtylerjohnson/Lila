"""The Apollo supply step: resolve targeting specs into stored contacts.

SWEEP CLASS, NEVER PRESS CLASS (contract amendment v1.4). Nothing in this
module is importable from the render path and nothing here runs during a
press. The press reads the durable targets store and calls no one; this
module is what writes that store, on its own schedule, under its own
switch. That is the L1 store shape applied to people: the expensive lookup
happens once, outside the deliverable, and the deliverable reads a file.

FOUR GUARDS, ALL LOUD.
  1. LILA_ENABLE_APOLLO defaults OFF. A step that quietly did nothing when
     the switch was off would look exactly like a step that ran and found
     nobody, which is the distinction the whole band is built on.
  2. A missing key raises. It never degrades to an empty result.
  3. An API error raises with the status and the body head. A partial
     result is written only with its own truncation disclosure.
  4. Calls are CAPPED and the cap is disclosed in the receipt, in the one
     dialect every capped lane in this codebase speaks (tools.api.base
     cap_disclosed): kept slice, true total, truncated only when something
     was actually cut.

SPEND IS PRINTED, NOT ESTIMATED. The receipt states the calls made, the cap
they ran against, the identities returned, and how many arrived with their
email locked, because a locked identity costs a credit to reveal and the
operator should see that number before deciding to reveal any.

NO KEY IS EVER PLACED BY THIS CODE. It reads APOLLO_API_KEY from the
environment and refuses when it is absent. Writing a key into a file or a
default is out of bounds.
"""

from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.request
from datetime import datetime, timezone
from typing import Any, Optional

from tools.toggles import is_enabled

# Apollo's people-search endpoint. One endpoint, one seam, so a test can
# poison the network and prove the press never touches it.
API_URL = "https://api.apollo.io/api/v1/mixed_people/search"
SOURCE_NAME = "apollo.mixed_people_search"

# Enrichment. The search endpoint returns no contact details at all
# (measured 2026-08-06); work email arrives here, synchronously, at roughly
# one credit per record. Apollo caps a bulk match at 10 people per request.
BULK_MATCH_URL = "https://api.apollo.io/api/v1/people/bulk_match"
ENRICH_SOURCE_NAME = "apollo.people_bulk_match"
BULK_MATCH_MAX = 10

# UNVERIFIED AT WRITE TIME (2026-08-06): no API key existed in this session,
# so the REST balance endpoint has never been exercised from this code. It
# is written to fail LOUD and return None rather than invent a balance,
# because a fabricated balance would defeat every guardrail built on it.
PROFILE_URL = "https://api.apollo.io/api/v1/users/api_profile"

# TASK 5 HARD GATE. Direct-dial reveal is off in code, not by intention.
# Flipping it is an explicit operator act AND still has to clear the
# retrieval proof, because the two guards answer different questions:
# this one asks whether we want to spend, the proof asks whether we could
# ever collect what we paid for.
PHONE_REVEAL_ENV = "LILA_APOLLO_ALLOW_PHONE_REVEAL"
WEBHOOK_RESULT_PATH = "apollo.webhook_result"

# The default ceiling on calls per run. One call per spec page; a run that
# would exceed this stops, keeps what it has, and says so.
DEFAULT_CALL_CAP = 25
DEFAULT_PER_PAGE = 10
DEFAULT_TIMEOUT = 30

# Apollo returns this literal in `email` when the address is behind a
# credit. It is NOT an email and must never be stored as one.
_LOCKED_EMAIL = "email_not_unlocked"


class ApolloError(RuntimeError):
    """The supply step cannot run or the API refused. Always loud."""


def apollo_enabled() -> bool:
    """LILA_ENABLE_APOLLO, default OFF (contract amendment v1.4)."""
    return is_enabled("apollo", default=False)


def api_key() -> Optional[str]:
    key = str(os.environ.get("APOLLO_API_KEY") or "").strip()
    return key or None


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _http_post(url: str, payload: dict, key: str,
               timeout: int = DEFAULT_TIMEOUT) -> dict:
    """THE one network seam. Everything else in this module is pure."""
    body = json.dumps(payload).encode("utf-8")
    request = urllib.request.Request(url, data=body, method="POST")
    request.add_header("Content-Type", "application/json")
    request.add_header("Cache-Control", "no-cache")
    request.add_header("accept", "application/json")
    request.add_header("x-api-key", key)
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            raw = response.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as exc:
        head = exc.read().decode("utf-8", "replace")[:300] if exc.fp else ""
        raise ApolloError(
            f"Apollo returned HTTP {exc.code} for {url}: {head}") from exc
    except (urllib.error.URLError, OSError) as exc:
        raise ApolloError(f"Apollo unreachable at {url}: {exc}") from exc
    try:
        parsed = json.loads(raw)
    except ValueError as exc:
        raise ApolloError(
            f"Apollo returned unparseable JSON: {raw[:200]}") from exc
    if not isinstance(parsed, dict):
        raise ApolloError(f"Apollo returned a {type(parsed).__name__}, "
                          "not an object")
    return parsed


def search_payload(spec: dict, *, page: int = 1,
                   per_page: int = DEFAULT_PER_PAGE) -> dict:
    """One people search: the spec's target organisation plus its title set.

    Organisation and titles both come from the spec, which came from the
    persona reference file and the pack. Nothing is invented here.
    """
    payload: dict = {
        "q_organization_name": spec["target_org"]["name"],
        "person_titles": list(spec.get("person_titles") or []),
        "page": int(page),
        "per_page": int(per_page),
    }
    seniorities = list(spec.get("person_seniorities") or [])
    if seniorities:
        payload["person_seniorities"] = seniorities
    # STAGE 0, the only zero-credit exclusion point. Apollo matches this
    # against where the PERSON is, not their employer's HQ, so an unbounded
    # search returns employees of a US agency living anywhere on earth and
    # charges to enrich them. Operator-owned; see targeting_personas.json.
    locations = list(spec.get("person_locations") or [])
    if locations:
        payload["person_locations"] = locations
    return payload


def _identity(person: dict, spec: dict, *, retrieved_at: str) -> Optional[dict]:
    """One API person as a store row, or None when it carries no name.

    The provenance block is written HERE and only here, so a row can never
    reach the store unlabeled: the class is apollo, the date is this run's,
    and the source names the exact endpoint that produced it.
    """
    name = " ".join(str(person.get("name") or "").split())
    if not name:
        first = str(person.get("first_name") or "").strip()
        last = str(person.get("last_name") or "").strip()
        name = " ".join(x for x in (first, last) if x)
    if not name:
        return None
    from agents.golden_press.targeting_rules import tier_for_title

    email = " ".join(str(person.get("email") or "").split())
    locked = _LOCKED_EMAIL in email.casefold()
    organisation = person.get("organization") or {}
    title = " ".join(str(person.get("title") or "").split())
    row = {
        "contact_id": str(person.get("id") or ""),
        "spec_id": spec["spec_id"],
        # the rung the RESOLVED title answers, not the first rung sought
        "tier": tier_for_title(spec, title),
        "name": name,
        "title": title,
        "organization": " ".join(
            str(organisation.get("name")
                or spec["target_org"]["name"] or "").split()),
        "phone": " ".join(str(
            person.get("phone_number")
            or (person.get("organization") or {}).get("phone") or "").split()),
        "linkedin_url": " ".join(
            str(person.get("linkedin_url") or "").split()),
        "join_record_ids": list(spec.get("join_record_ids") or []),
        "provenance": {"class": "apollo", "retrieved_at": retrieved_at,
                       "source": SOURCE_NAME},
        "email_locked": bool(locked),
    }
    # SCREEN AT CONSTRUCTION (operator ruling 2026-08-06). A search-only row
    # is thin -- no geography, no employment history -- so it will usually
    # come back accepted_with_flags on S7, which is the honest answer: an
    # unverified identity carries a caution rather than a clean bill. enrich()
    # re-screens the same person against the richer match response and
    # overwrites this, so the screen always reflects the best data held.
    from agents.golden_press.person_screen import screen_person

    row["screen"] = screen_person(person, spec)
    # A locked address is a placeholder, not an address. Storing it would
    # put a fake email in front of an operator and in the CSV export.
    if email and not locked:
        row["email"] = email
    return row


def resolve(specs: list, *, key: Optional[str] = None,
            call_cap: int = DEFAULT_CALL_CAP,
            per_page: int = DEFAULT_PER_PAGE,
            poster=None, now: Optional[datetime] = None,
            enrich_emails: bool = False, matcher=None,
            balance_getter=None,
            reveal_phone_number: bool = False) -> dict:
    """Resolve every spec into store rows. Loud on every failure path.

    Returns the exact payload the store holds: ``{"receipt": {...},
    "contacts": [...]}``. ``poster`` is the injection seam for tests; the
    default is the real HTTP call.
    """
    if not apollo_enabled():
        raise ApolloError(
            "LILA_ENABLE_APOLLO is off (it defaults off). The targeting "
            "supply step spends Apollo credits, so turning it on is an "
            "explicit operator act; nothing was called and nothing was "
            "written.")
    key = key or api_key()
    if not key:
        raise ApolloError(
            "APOLLO_API_KEY is not set in the environment. The step refuses "
            "rather than writing an empty result that would read as a "
            "tested zero. Add the key yourself; no code path places one.")
    poster = poster or (lambda payload: _http_post(API_URL, payload, key))
    started = now or _utc_now()
    retrieved_at = started.date().isoformat()

    # ONE CALL PER DISTINCT SEARCH, NOT PER SPEC. Ten forecast adjacency
    # rows routing through one reseller are ten specs and ONE question, and
    # measured on the live packs that is the common case (apexanalytix: 10
    # T3 rows, all DEV TECHNOLOGY GROUP). Paying ten credits for one answer
    # would burn the cap on repeats; the result is attributed back to every
    # spec that asked, and the dedupe is stated in the receipt.
    groups: dict = {}
    for spec in specs:
        payload = search_payload(spec, per_page=per_page)
        key_tuple = (str(payload["q_organization_name"]).casefold(),
                     tuple(payload["person_titles"]))
        groups.setdefault(key_tuple, {"payload": payload, "specs": []})
        groups[key_tuple]["specs"].append(spec)

    contacts: list = []
    calls = 0
    returned = 0
    locked = 0
    searched = 0
    truncated = False
    for group in groups.values():
        if calls >= call_cap:
            truncated = True
            break
        result = poster(group["payload"])
        calls += 1
        searched += len(group["specs"])
        people = result.get("people")
        if people is None:
            people = result.get("contacts") or []
        if not isinstance(people, list):
            raise ApolloError(
                f"Apollo returned a {type(people).__name__} where a people "
                f"list was expected for "
                f"{group['payload']['q_organization_name']!r}")
        returned += len(people)
        anchor = group["specs"][0]
        served = [s["spec_id"] for s in group["specs"]]
        joins: list = []
        for spec in group["specs"]:
            for rid in spec.get("join_record_ids") or []:
                if rid not in joins:
                    joins.append(rid)
        for person in people:
            if not isinstance(person, dict):
                continue
            row = _identity(person, anchor, retrieved_at=retrieved_at)
            if row is None:
                continue
            if row.pop("email_locked", False):
                locked += 1
            # ONE ROW PER PERSON PER SEARCH. The same route legitimately
            # answers several specs, so the row names all of them and
            # carries their joined records; it does not repeat itself once
            # per spec on the page.
            row["spec_ids"] = served
            row["join_record_ids"] = joins
            contacts.append(row)

    # ---- enrichment: the confirmed synchronous work-email path ---------- #
    # ENRICHMENT IS OPT-IN, and so is every network call it implies. A
    # caller that only wants the free search (and every search-only test)
    # must not acquire a balance read and a billable match by default; the
    # runner turns this on explicitly.
    from tools.apollo_credits import SpendRecord

    spend = SpendRecord()
    spend.calls_made = calls
    enrichment = {"rows": contacts, "estimate": None, "calls": 0,
                  "matched": 0, "screened_out": []}
    if enrich_emails and contacts:
        spend.balance_before = read_balance(key, getter=balance_getter)
        enrichment = enrich(
            contacts, key=key, matcher=matcher,
            balance_before=spend.balance_before,
            reveal_phone_number=reveal_phone_number, spend=spend,
            specs=specs)
        contacts = enrichment["rows"]
        spend.estimate = enrichment["estimate"]
        spend.balance_after = read_balance(key, getter=balance_getter)
        print(spend.render(), flush=True)

    receipt = {
        "attempted": True,
        "source": SOURCE_NAME,
        "enrichment_source": (ENRICH_SOURCE_NAME if enrich_emails else None),
        "endpoint": API_URL,
        "retrieved_at": retrieved_at,
        "spend": spend.receipt(),
        "emails_resolved": sum(1 for r in contacts if r.get("email")),
        "enriched_records": enrichment["matched"],
        "enrich_calls": enrichment["calls"],
        "phone_reveal_requested": bool(reveal_phone_number),
        "screened_out": enrichment.get("screened_out") or [],
        "started_at": started.isoformat(),
        "specs_read": len(specs),
        "specs_searched": searched,
        "distinct_searches": len(groups),
        "result_count": returned,
        "contacts_written": len(contacts),
        "emails_locked": locked,
        "calls_made": calls,
        "call_cap": int(call_cap),
        "per_page": int(per_page),
        "truncated": truncated,
    }
    if truncated:
        receipt["truncated_note"] = (
            f"searched the first {searched} of {len(specs)} spec(s) in "
            f"order; the {call_cap}-call cap stopped the run")
    receipt["spend_note"] = (
        f"{calls} search call(s) against a {call_cap}-call cap · "
        f"{len(specs)} spec(s) collapsed to {len(groups)} distinct search(es) "
        f"· {returned} identity result(s) · {locked} arrived with the email "
        f"locked behind a credit and none was stored as an address")
    return {"receipt": receipt, "contacts": contacts}


# --------------------------------------------------------------------------- #
# balance, enrichment, and the hard phone gate
# --------------------------------------------------------------------------- #
def phone_reveal_allowed() -> bool:
    """TASK 5: direct-dial reveal is hard-gated OFF in code."""
    return os.environ.get(PHONE_REVEAL_ENV, "").strip().lower() in (
        "1", "true", "on", "yes")


def read_balance(key: str, *, getter=None) -> Optional[int]:
    """Current credit balance, or None when it cannot be read.

    ZERO COST. Returns None loudly rather than a guess: every guardrail
    downstream is built on this number, so inventing one would be worse
    than admitting it is unknown.
    """
    getter = getter or (lambda: _http_post(PROFILE_URL, {}, key))
    try:
        payload = getter()
    except Exception as exc:  # noqa: BLE001 - diagnostic, never fatal
        print(f"[apollo] balance unreadable ({type(exc).__name__}: {exc}); "
              f"the run continues with an unknown balance and the receipt "
              f"will say so", file=sys.stderr, flush=True)
        return None
    value = (payload or {}).get("num_credits_remaining")
    if not isinstance(value, int):
        print("[apollo] balance endpoint returned no integer "
              "num_credits_remaining; recording unknown rather than "
              "guessing", file=sys.stderr, flush=True)
        return None
    return value


def _webhook_probe(key: str, getter=None):
    """The ZERO-COST probe for the async result path.

    It asks the result endpoint for an id that cannot exist. A reachable,
    authorised endpoint answers (a not-found is an answer). An endpoint that
    refuses authentication raises, which is exactly the 2026-08-06 failure,
    and the proof then refuses the billable call.
    """
    getter = getter or (lambda: _http_post(
        "https://api.apollo.io/api/v1/webhook_results/probe",
        {"request_id": "0"}, key))
    return getter()


def enrich(rows: list, *, key: str, matcher=None, proof=None,
           balance_before: Optional[int] = None,
           reveal_phone_number: bool = False,
           spend: Optional[Any] = None,
           specs: Optional[list] = None) -> dict:
    """Fill work email (and classified phones) for already-searched rows.

    EMAIL ONLY BY DEFAULT. ``reveal_phone_number`` must clear TWO gates: the
    hard operator switch and the retrieval proof. Both refuse loudly and
    neither can be satisfied by intention.
    """
    from tools.apollo_credits import (
        CreditRefusal, RetrievalProof, enforce_cap, estimate, render_estimate)

    if not rows:
        return {"rows": [], "estimate": None, "calls": 0, "matched": 0}
    if reveal_phone_number:
        if not phone_reveal_allowed():
            raise CreditRefusal(
                f"direct-dial reveal is hard-gated off. Set "
                f"{PHONE_REVEAL_ENV} deliberately to lift the gate; it costs "
                f"roughly 8 credits per person and its result path was dead "
                f"on 2026-08-06, which cost 16 credits for nothing.")
        proof = proof or RetrievalProof()
        proof.require(WEBHOOK_RESULT_PATH,
                      spend_note="a reveal here would cost roughly "
                                 f"{8 * len(rows)} direct-dial credit(s)")

    spec_by_id = {str(s.get("spec_id")): s for s in (specs or [])}
    ids = [r["contact_id"] for r in rows if r.get("contact_id")]
    batches = [ids[i:i + BULK_MATCH_MAX]
               for i in range(0, len(ids), BULK_MATCH_MAX)]
    est = estimate(len(ids), calls=len(batches), credit_class="match",
                   balance_before=balance_before)
    print(render_estimate(est), flush=True)
    enforce_cap(est)

    matcher = matcher or (lambda payload: _http_post(
        BULK_MATCH_URL, payload, key))
    by_id: dict = {}
    calls = 0
    for batch in batches:
        payload = {"details": [{"id": person_id} for person_id in batch],
                   "reveal_phone_number": bool(reveal_phone_number)}
        result = matcher(payload)
        calls += 1
        matches = (result or {}).get("matches")
        if not isinstance(matches, list):
            raise ApolloError(
                f"bulk match returned a {type(matches).__name__} where a "
                f"matches list was expected")
        for person in matches:
            if isinstance(person, dict) and person.get("id"):
                by_id[str(person["id"])] = person
        if spend is not None:
            spend.calls_made += calls

    from agents.golden_press.phone_policy import from_enrichment
    from agents.golden_press.person_screen import screen_person, REJECT

    merged = []
    screened_out = []
    for row in rows:
        person = by_id.get(str(row.get("contact_id")))
        row = dict(row)
        if person:
            # THE SCREEN, on a response already bought for the work email.
            # It gates the 8-credit reveal and it is the only thing standing
            # between a plausible false positive and the client artifact.
            row["screen"] = screen_person(person, spec_by_id.get(
                str(row.get("spec_id"))) or {})
            if row["screen"]["verdict"] == REJECT:
                screened_out.append({
                    "name": row.get("name"), "contact_id": row.get("contact_id"),
                    "spec_id": row.get("spec_id"),
                    "why": next((f["why"] for f in row["screen"]["findings"]
                                 if f["verdict"] == REJECT), "screened out"),
                })
                continue
            email = " ".join(str(person.get("email") or "").split())
            if email and "email_not_unlocked" not in email.casefold():
                row["email"] = email
                row["email_status"] = str(person.get("email_status") or "")
            row["phones"] = from_enrichment(person)
            # Apollo's own attribution, carried for the tier measurement.
            # It is RECORDED, never yet used to decide the rendered tier.
            row["apollo_seniority"] = person.get("seniority")
            row["apollo_departments"] = list(person.get("departments") or [])
            row["apollo_subdepartments"] = list(
                person.get("subdepartments") or [])
            row["apollo_functions"] = list(person.get("functions") or [])
            row["enriched"] = True
        else:
            row["enriched"] = False
        merged.append(row)
    return {"rows": merged, "estimate": est, "calls": calls,
            "matched": len(by_id), "screened_out": screened_out}


def dry_run(specs: list, *, per_page: int = DEFAULT_PER_PAGE) -> dict:
    """What the step WOULD send, with no key, no switch, and no call.

    A dry run reveals no identity, so it is available whether or not the
    switch is on: the specs it prints are already in the client report.
    The planned-call count is the DEDUPED one, because that is what the run
    would actually spend; overstating it would make the operator budget for
    calls the step is not going to make.
    """
    seen: dict = {}
    for spec in specs:
        payload = search_payload(spec, per_page=per_page)
        key = (str(payload["q_organization_name"]).casefold(),
               tuple(payload["person_titles"]))
        seen.setdefault(key, payload)
    return {
        "attempted": False,
        "source": SOURCE_NAME,
        "endpoint": API_URL,
        "specs_read": len(specs),
        "planned_calls": len(seen),
        "searches": list(seen.values()),
    }
