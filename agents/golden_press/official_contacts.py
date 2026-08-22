"""The free official-contact lane: POCs the government published itself.

WHY THIS LEADS (operator ruling 2026-08-06). A SAM point of contact is
published BY the government ON a specific notice. That is a different and
stronger evidence class than an inferred directory match: the join law is
satisfied by construction rather than by reasoning, and it costs nothing.

Measured on the local notice store, 2026-08-06:
    69,106 notices carry a published POC email
    36,125 carry a published POC PHONE, which is the field Apollo charges
           eight credits to reveal
    23,666 carry a second published POC
     7,447 carry a POC title, and when present it is overwhelmingly
           procurement (Contract Specialist 3,922, Contracting Officer
           2,045), which is why R6's conservatism is empirically right and
           not merely cautious

ZERO NETWORK. This module reads the local store and nothing else. It is
sweep-class like the Apollo lane, but it spends nothing, so it runs first
and Apollo only fills what it leaves uncovered.

THE JOIN IS TIERED AND STATED (operator constraint: "do not attach every
agency POC to every opportunity at that agency"). Three binding strengths,
weakest of which still requires a capability match:

  direct_notice  the POC published on a notice the spec's own evidence
                 cites. The strongest possible join: this person is on
                 this record.
  same_office    same agency and buying office as a cited record, AND the
                 notice matches the client's approved capability
                 vocabulary. Office granularity is real: DoD alone has 716
                 distinct offices in the store.
  same_subtier   same agency and subtier, AND a capability match.

Anything looser does not bind and does not render. The strength rides every
contact so a reader can see how tight the join was.

CONSERVATIVE PERSONA (R6). A published contracting POC proves PROCUREMENT
authority and nothing else. It is promoted to another function only when
the source explicitly establishes that role in `poc_title`. Inference does
not promote; only publication does.
"""

from __future__ import annotations

import re
from typing import Any, Optional

OFFICIAL_CONTACTS_VERSION = "official_contacts.v1.2026-08-06"

SOURCE_NAME = "sam.notice_poc"
PROVENANCE_CLASS = "apollo"  # store vocabulary; see `source` for the truth

DIRECT_NOTICE = "direct_notice"
SAME_OFFICE = "same_office"
SAME_SUBTIER = "same_subtier"
BIND_STRENGTHS = (DIRECT_NOTICE, SAME_OFFICE, SAME_SUBTIER)

PRIMARY = "primary"
SECONDARY = "secondary"

# What a published title has to say before a POC is credited with anything
# beyond procurement authority (R6). Positive evidence only.
_PROMOTION_TITLES = {
    "mission/program": ("program manager", "program director", "project manager",
                        "product owner", "requirements owner", "technical lead",
                        "engineer", "architect", "cor", "contracting officer's "
                        "representative", "contracting officers representative"),
    "security operations": ("security", "cyber", "isso", "issm",
                            "authorizing official", "ato"),
}
_PROCUREMENT = "procurement/contracting"

_EMAIL = re.compile(r"^[^@\s]+@[^@\s]+\.[A-Za-z]{2,}$")

# THE PUBLISHED NAME FIELD IS DIRTY, measured 2026-08-06. Real values from
# the store include "KYLE BARNER445-737-8182", "Alla YakoverDSN614-693-0",
# "Telephone: 4457373493" and "Questions regarding this solicitation".
# Rendering those in a client artifact would put a phone number where a
# person's name belongs and a sentence where a contact belongs.
_PHONE_IN_NAME = re.compile(
    r"(?:DSN|COMM|TEL|PH|PHONE|CELL)?[:\s]*"
    r"(\(?\d{3}\)?[-.\s]?\d{3}[-.\s]?\d{4}(?:\s*x\d+)?)", re.I)
_DIGIT_RUN = re.compile(r"\d{3,}")
# Tokens that mean the field holds an instruction or a shared mailbox rather
# than a person. A shared inbox is a usable route but it is NOT a named
# individual, and Band 09 names individuals.
_NOT_A_PERSON = (
    "question", "regarding", "please", "email us", "contact us", "inquiries",
    "helpdesk", "help desk", "customer service", "team", "group", "office of",
    "mailbox", "distribution", "do not reply", "noreply", "no-reply",
    "submit", "solicitation", "n/a", "tbd", "unknown", "see ", "refer to",
)


def clean_poc_name(raw: Any) -> tuple:
    """(name, phone_found, reject_reason). The name field, made honest.

    Splits a phone or DSN run out of the name and hands it back so the
    number is not lost, then refuses anything that is not a person: a
    sentence, an instruction, or a shared mailbox label. Refusing is the
    point. A wrong name in front of a caller is worse than no contact.
    """
    text = _clean(raw)
    if not text:
        return ("", "", "no published name")
    phone = ""
    match = _PHONE_IN_NAME.search(text)
    if match and _DIGIT_RUN.search(text):
        phone = _clean(match.group(1))
        text = _clean(text[:match.start()] + " " + text[match.end():])
    # DSN and other short military runs the 3-3-4 pattern does not catch
    # ("Alla YakoverDSN614-693-0"). Everything from the label to the end of
    # the digit run goes, and the digits are kept as the number when no
    # better one was published.
    dsn = re.search(r"(DSN|COMM|TEL|PHONE|CELL|FAX)[:\s]*([\d\-.()\s]{5,})",
                    text, re.I)
    if dsn:
        if not phone:
            phone = _clean(dsn.group(2)).strip(" -.")
        text = _clean(text[:dsn.start()] + " " + text[dsn.end():])
    # any digit run left over is not part of a person's name, and neither is
    # the punctuation it leaves behind
    text = _clean(_DIGIT_RUN.sub(" ", text))
    text = _clean(re.sub(r"[\-.()/|,;:]{1,}", " ", text))
    text = _clean(re.sub(r"\b(DSN|COMM|TEL|PHONE|CELL|FAX)\b", " ", text,
                         flags=re.I))
    text = _clean(text.strip(" ,;:-/|"))
    lowered = text.casefold()
    for token in _NOT_A_PERSON:
        if token in lowered:
            return ("", phone, f"published name reads as {token!r}, not a person")
    letters = [tok for tok in re.split(r"[^A-Za-z']+", text) if len(tok) > 1]
    if len(letters) < 2:
        return ("", phone, "published name is not a person name")
    if len(letters) > 5:
        return ("", phone, "published name reads as a sentence, not a person")
    return (text, phone, "")


def _clean(value: Any) -> str:
    return " ".join(str(value or "").split())


def _norm(value: Any) -> str:
    return re.sub(r"[^a-z0-9]+", " ", _clean(value).casefold()).strip()


def is_official_domain(email: str) -> bool:
    domain = _clean(email).casefold().rsplit("@", 1)[-1]
    return domain.endswith(".gov") or domain.endswith(".mil")


def persona_function(poc_title: Any) -> tuple:
    """(function, basis). R6: procurement unless the source says otherwise.

    Returns the basis alongside the function so the receipt can show WHY a
    POC was credited with a role, and so a promotion is always traceable to
    published text rather than to a guess.
    """
    text = _clean(poc_title).casefold()
    if not text:
        return (_PROCUREMENT,
                "no published title; a notice POC proves procurement "
                "authority only")
    for function, tokens in _PROMOTION_TITLES.items():
        for token in tokens:
            if token in text:
                return (function,
                        f"published POC title {_clean(poc_title)!r} names "
                        f"{token!r}")
    return (_PROCUREMENT,
            f"published POC title {_clean(poc_title)!r} is a procurement role")


def _capability_match(row: Any, terms: Any) -> Optional[str]:
    """Which approved capability term the notice actually carries.

    Returned rather than a boolean so the binding can print the term it
    matched on. Without this, a same-office or same-subtier bind would be
    "every POC at this agency", which is the thing the operator ruled out.
    """
    haystack = " ".join(_norm(row[k]) for k in ("title", "description_prefix")
                        if _has(row, k))
    for term in (terms or []):
        needle = _norm(term)
        if needle and needle in haystack:
            return _clean(term)
    return None


def _has(row: Any, key: str) -> bool:
    try:
        return row[key] is not None
    except (KeyError, IndexError, TypeError):
        return False


def _get(row: Any, key: str) -> Any:
    return row[key] if _has(row, key) else None


def _identity(row: Any, *, slot: str) -> Optional[dict]:
    """One published POC from a notice row, primary or secondary."""
    prefix = "poc_" if slot == PRIMARY else "poc_secondary_"
    email = _clean(_get(row, prefix + "email"))
    if not email or not _EMAIL.match(email) or not is_official_domain(email):
        return None
    name, name_phone, reject = clean_poc_name(_get(row, prefix + "name"))
    if reject:
        return {"rejected": reject, "email": email}
    title = _clean(_get(row, prefix + "title"))
    function, basis = persona_function(title)
    # Name arrives as "Martinez, Crystal" on many rows. Presented as filed;
    # inverting it would be an unbacked assertion about which token is the
    # surname, and the caller can see the raw form.
    return {
        "name": name,
        "title": title or None,
        "email": email,
        # the published phone field first, then a number recovered from the
        # name field, which is where DLA files it
        "phone": _clean(_get(row, prefix + "phone")) or name_phone,
        "function": function,
        "function_basis": basis,
        "poc_slot": slot,
    }


def _bind(row: Any, spec: dict, *, cited_ids: set, terms: Any) -> Optional[dict]:
    """How tightly this notice joins this spec, or None when it does not."""
    notice_id = _clean(_get(row, "notice_id"))
    if notice_id and notice_id in cited_ids:
        return {"strength": DIRECT_NOTICE,
                "why": "the POC is published on a record this report cites"}
    agency = _norm(_get(row, "agency"))
    subtier = _norm(_get(row, "subtier"))
    office = _norm(_get(row, "office"))
    wanted_agency = _norm(spec.get("buying_component"))
    wanted_office = _norm(spec.get("office"))
    # The buying component is matched against subtier and office as well as
    # agency, because the store files the component in subtier.
    if not wanted_agency or wanted_agency not in f"{agency} {subtier} {office}":
        return None
    term = _capability_match(row, terms)
    if not term:
        # NO CAPABILITY MATCH, NO BIND. This is the line that stops the lane
        # becoming "every POC at this agency".
        return None
    if wanted_office and wanted_office in f"{office} {subtier}":
        return {"strength": SAME_OFFICE, "capability_term": term,
                "why": (f"same buying office as a cited record, and the "
                        f"notice names {term!r}")}
    return {"strength": SAME_SUBTIER, "capability_term": term,
            "why": (f"same buying organisation as a cited record, and the "
                    f"notice names {term!r}")}


def harvest(specs: Any, *, conn, capability_terms: Any = None,
            retrieved_at: str = "", limit_per_spec: int = 25) -> dict:
    """Official POCs bound to the specs that justify calling them.

    Reads the local notice store only. Returns the same contact shape the
    Apollo lane writes, so the store, the screen, the band and the export
    treat both lanes identically, plus a receipt naming what was searched,
    what bound, and what stayed uncovered.
    """
    from agents.golden_press.person_screen import (
        NOTICE_POC, REJECTED, screen_person)

    terms = [t for t in (capability_terms or []) if _clean(t)]
    receipt = {
        "attempted": True, "source": SOURCE_NAME,
        "version": OFFICIAL_CONTACTS_VERSION,
        "retrieved_at": retrieved_at,
        "network_calls": 0, "credits_spent": 0,
        "specs_read": 0, "notices_screened": 0, "notices_matched": 0,
        "identities_found": 0, "accepted": 0, "flagged": 0, "rejected": 0,
        "by_strength": {}, "uncovered_specs": [], "capability_terms": terms,
    }
    if conn is None:
        receipt["attempted"] = False
        receipt["why"] = "no notice store available"
        return {"contacts": [], "receipt": receipt}

    contacts: list = []
    seen: dict = {}
    for spec in (specs or []):
        if not isinstance(spec, dict):
            continue
        receipt["specs_read"] += 1
        cited = {str(r) for r in (spec.get("join_record_ids") or [])}
        agency = _clean(spec.get("buying_component"))
        # THE COMPONENT LIVES IN `subtier`, NOT `agency` (measured
        # 2026-08-06). The store's `agency` is the coarse department, so
        # "Defense Logistics Agency" matches 0 rows there and 16,149 in
        # subtier. Searching only `agency` is why the first build of this
        # lane screened nothing at all. Same class of defect as the
        # extract-vs-store column vocabularies in the events lane: one
        # translation point, stated, rather than a screen taught a second
        # dialect.
        like = f"%{agency[:40]}%" if agency else None
        clause = ["notice_id IN (%s)" % (",".join("?" * len(cited)) or "''")]
        params: list = list(cited)
        if like:
            clause.append("subtier LIKE ? OR office LIKE ? OR agency LIKE ?")
            params.extend([like, like, like])
        rows = conn.execute(
            "SELECT * FROM notices WHERE poc_email IS NOT NULL "
            "AND poc_email != '' AND (" + " OR ".join(clause) + ") "
            "ORDER BY posted DESC LIMIT 4000", params).fetchall()
        found_for_spec = 0
        for row in rows:
            receipt["notices_screened"] += 1
            binding = _bind(row, spec, cited_ids=cited, terms=terms)
            if binding is None:
                continue
            receipt["notices_matched"] += 1
            for slot in (PRIMARY, SECONDARY):
                identity = _identity(row, slot=slot)
                if identity is None:
                    continue
                if identity.get("rejected"):
                    receipt["rejected"] += 1
                    receipt.setdefault("rejected_names", {})
                    reason = identity["rejected"]
                    receipt["rejected_names"][reason] = \
                        receipt["rejected_names"].get(reason, 0) + 1
                    continue
                receipt["identities_found"] += 1
                key = identity["email"].casefold()
                spec_id = str(spec.get("spec_id") or "")
                if key in seen:
                    # DEDUPE THE IDENTITY, NEVER THE BINDING. One POC can
                    # justify two different calls; collapsing the bindings
                    # would lose which record each hangs off.
                    row_out = seen[key]
                    if spec_id and spec_id not in row_out["spec_ids"]:
                        row_out["spec_ids"].append(spec_id)
                        row_out["bindings"].append(
                            {"spec_id": spec_id, **binding,
                             "join_record_ids": sorted(cited)})
                    continue
                screen = screen_person(
                    {"name": identity["name"], "title": identity["title"],
                     "email": identity["email"],
                     "email_status": "verified"},
                    spec, source_class=NOTICE_POC)
                if screen["state"] == REJECTED:
                    receipt["rejected"] += 1
                    continue
                phones = []
                if identity["phone"]:
                    from agents.golden_press.phone_policy import WORK_DIRECT
                    phones.append({
                        "number": identity["phone"], "type": WORK_DIRECT,
                        "basis": "published on the notice as the POC phone",
                        "basis_kind": "government_published"})
                row_out = {
                    "contact_id": f"sam:{key}",
                    "spec_id": spec_id, "spec_ids": [spec_id] if spec_id else [],
                    "name": identity["name"], "title": identity["title"],
                    "email": identity["email"], "email_status": "verified",
                    "organization": _clean(_get(row, "agency")),
                    "office": _clean(_get(row, "office")) or None,
                    "function": identity["function"],
                    "function_basis": identity["function_basis"],
                    "tier": None,          # no silent rung; function is the claim
                    "phones": phones,
                    "join_record_ids": sorted(cited),
                    "bindings": [{"spec_id": spec_id, **binding,
                                  "join_record_ids": sorted(cited)}],
                    "notice_id": _clean(_get(row, "notice_id")),
                    "solicitation_number": _clean(_get(row, "sol_number")) or None,
                    "source_url": _clean(_get(row, "url")) or None,
                    "source_posted": _clean(_get(row, "posted"))[:10] or None,
                    "screen": screen,
                    "provenance": {"class": PROVENANCE_CLASS,
                                   "retrieved_at": retrieved_at,
                                   "source": SOURCE_NAME},
                }
                strength = binding["strength"]
                receipt["by_strength"][strength] = \
                    receipt["by_strength"].get(strength, 0) + 1
                if screen["state"] == "accepted":
                    receipt["accepted"] += 1
                else:
                    receipt["flagged"] += 1
                seen[key] = row_out
                contacts.append(row_out)
                found_for_spec += 1
                if found_for_spec >= limit_per_spec:
                    break
            if found_for_spec >= limit_per_spec:
                break
        if not found_for_spec:
            receipt["uncovered_specs"].append({
                "spec_id": spec.get("spec_id"),
                "buying_component": agency,
                "why": ("no published POC bound to this spec at any strength; "
                        "Apollo would add value here")})
    receipt["contacts_written"] = len(contacts)
    return {"contacts": contacts, "receipt": receipt}
