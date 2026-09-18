"""Phone classification and render policy for Band 09 (operator spec, Task 5).

EVERY NUMBER CARRIES A TYPE AND A RECEIPT OF HOW IT GOT ONE. A federal
switchboard and a personal mobile are both "a phone number" in a JSON blob
and are completely different objects in a sales motion: one transfers you on
a name, the other reaches someone at dinner. Storing them in one field is
how a caller ends up dialling the wrong thing, so the type is part of the
record and an unclassified number is never promoted into a classified slot.

FOUR TYPES:
  org_main       the organisation's switchboard. Real value in federal
                 outreach because the desk transfers on a name, and it costs
                 nothing because it rides the enrichment response already
                 paid for. It gets its OWN column and may NEVER occupy a
                 person's phone field.
  work_direct    the person's direct line. This is the dial.
  mobile         a personal handset.
  unclassified   a number whose type the response did not establish.

RENDER POLICY, FAIL CLOSED:
  org_main     -> its own labeled main-line column
  work_direct  -> the dial column
  mobile       -> stored and shown by default; the existing operator switch
                  can suppress it
  unclassified -> stored and shown in its own type by the same switch;
                  never selected as a person's mobile/direct research channel
  DNC-listed or invalid numbers -> retained in storage, withheld from display
  missing or unknown classification -> withheld

The withheld ones are STORED, deliberately. The operator can still work them
from the store and the sequence export; what the policy governs is the
client-facing artifact, where an unlabeled mobile number is a liability.
"""

from __future__ import annotations

import os
import re
from typing import Any, Optional

ORG_MAIN = "org_main"
WORK_DIRECT = "work_direct"
MOBILE = "mobile"
UNCLASSIFIED = "unclassified"

PHONE_TYPES = (ORG_MAIN, WORK_DIRECT, MOBILE, UNCLASSIFIED)

# Types that may reach the client artifact without an explicit switch.
CLIENT_VISIBLE = (ORG_MAIN, WORK_DIRECT)

INCLUDE_MOBILE_ENV = "LILA_TARGETING_INCLUDE_MOBILE"

# Apollo's own phone-type vocabulary, mapped to ours. A type this table does
# not know becomes unclassified rather than a guess.
_APOLLO_TYPE_MAP = {
    "mobile": MOBILE,
    "mobile_phone": MOBILE,
    "home": UNCLASSIFIED,
    "home_phone": UNCLASSIFIED,
    "direct": WORK_DIRECT,
    "direct_phone": WORK_DIRECT,
    "work_direct": WORK_DIRECT,
    "work": WORK_DIRECT,
    "corporate": ORG_MAIN,
    "corporate_phone": ORG_MAIN,
    "other": UNCLASSIFIED,
    "other_phone": UNCLASSIFIED,
}

_DIGITS = re.compile(r"[^0-9+]")


def include_mobile() -> bool:
    """LILA_TARGETING_INCLUDE_MOBILE, default ON (operator ruling
    2026-08-06: nothing is withheld from the client artifact).

    The switch survives so a future engagement can suppress mobiles without
    a code change, but the default is now to show them. What did NOT change
    is the integrity rule underneath: a number with no positively
    established type still does not render, because an unlabeled number is
    not a fact about anybody.
    """
    raw = os.environ.get(INCLUDE_MOBILE_ENV, "").strip().lower()
    if raw in ("0", "false", "off", "no"):
        return False
    return True


def normalise(number: Any) -> str:
    """Digits and a leading plus only. Never reformats into a shape the
    source did not state; this is for comparison, not for display."""
    return _DIGITS.sub("", str(number or "")).strip()


def classify(number: Any, *, apollo_type: Optional[str] = None,
             source_field: Optional[str] = None,
             org_numbers: Any = ()) -> dict:
    """One number, typed, with the receipt of HOW it was typed.

    Order of authority, strongest first:
      1. Apollo's own type flag, when it maps to a type we know
      2. the source FIELD the number came out of (an organisation phone is
         an organisation phone whatever else is true)
      3. equality with a known organisation number, which is how a person
         record carrying the switchboard is caught
      4. nothing: unclassified, and the receipt says so

    There is deliberately no area-code or prefix heuristic. Guessing that a
    number "looks like a mobile" is exactly the kind of inference that puts
    a wrong label on a real person's handset.
    """
    value = " ".join(str(number or "").split())
    if not value:
        return {"number": "", "type": UNCLASSIFIED,
                "basis": "no number present", "basis_kind": "absent"}

    flag = str(apollo_type or "").strip().casefold()
    if flag:
        mapped = _APOLLO_TYPE_MAP.get(flag)
        if mapped:
            return {"number": value, "type": mapped,
                    "basis": f"apollo type flag {flag!r}",
                    "basis_kind": "apollo_type_flag"}
        return {"number": value, "type": UNCLASSIFIED,
                "basis": f"apollo type flag {flag!r} is not in the known "
                         f"vocabulary; refusing to guess",
                "basis_kind": "apollo_type_flag_unknown"}

    field = str(source_field or "")
    if field.startswith("organization.") or field.startswith("account."):
        return {"number": value, "type": ORG_MAIN,
                "basis": f"source field {field}",
                "basis_kind": "apollo_field"}

    known = {normalise(n) for n in (org_numbers or ()) if n}
    if normalise(value) in known:
        return {"number": value, "type": ORG_MAIN,
                "basis": "equals the organisation's own published number",
                "basis_kind": "org_number_match"}

    return {"number": value, "type": UNCLASSIFIED,
            "basis": (f"no apollo type flag and source field "
                      f"{field or 'unstated'} does not establish a type"),
            "basis_kind": "unestablished"}


def renders(entry: Optional[dict]) -> bool:
    """Whether one classified number may reach the client artifact.

    FAIL CLOSED: an entry with no type, an unknown type, or no number does
    not render. mobile and unclassified render only behind the switch.
    """
    if not isinstance(entry, dict) or not entry.get("number"):
        return False
    if entry.get('dnc_status_cd') in {'found', 'listed'} or entry.get('status_cd') == 'invalid':
        return False
    kind = entry.get("type")
    if kind not in PHONE_TYPES:
        return False
    if kind in CLIENT_VISIBLE:
        return True
    return include_mobile()


def select(entries: Any, kind: str) -> Optional[dict]:
    """The first renderable entry of one type, or None."""
    for entry in (entries or ()):
        if isinstance(entry, dict) and entry.get("type") == kind \
                and renders(entry):
            return entry
    return None


def withheld(entries: Any) -> list:
    """Classified numbers the policy is holding back, for the receipt. A
    withheld number is DISCLOSED as a count, never silently absent."""
    return [e for e in (entries or ())
            if isinstance(e, dict) and e.get("number") and not renders(e)]


PHONE_METADATA = ('status_cd', 'dnc_status_cd', 'confidence_cd', 'observed_at')


def from_enrichment(person: dict, organisation: Optional[dict] = None,
                    *, observed_at: Optional[str] = None) -> list:
    """Every number an enrichment record carries, classified.

    The organisation's switchboard is taken deliberately (operator ruling,
    2026-08-06): it rides the same response at no extra cost and a federal
    desk transfers on a name. It is typed org_main here so that it can never
    later be read as somebody's direct line.
    """
    out: list = []
    organisation = organisation or (person or {}).get("organization") or {}
    org_numbers = []
    for key in ("phone", "sanitized_phone"):
        value = organisation.get(key)
        if value:
            org_numbers.append(value)
    primary = organisation.get("primary_phone") or {}
    if isinstance(primary, dict):
        for key in ("number", "sanitized_number"):
            if primary.get(key):
                org_numbers.append(primary[key])

    def add(value, *, flag=None, field):
        # Webhook results group arrays by type; synchronous matches use
        # phone_numbers entries. Never stringify a list/dict into a number.
        if isinstance(value, list):
            for item in value:
                add(item, flag=flag, field=field)
            return
        raw = value if isinstance(value, dict) else {}
        number = (raw.get('sanitized_number') or raw.get('raw_number') or raw.get('number')) if raw else value
        if not isinstance(number, str) or not number.strip():
            return
        entry = classify(number, apollo_type=raw.get('type') or flag,
                         source_field=field, org_numbers=org_numbers)
        for key in PHONE_METADATA:
            if isinstance(raw.get(key), str) and raw[key]:
                entry[key] = raw[key]
        if 'status_cd' not in entry and raw.get('status'):
            entry['status_cd'] = str(raw['status'])
        if observed_at and 'observed_at' not in entry:
            entry['observed_at'] = observed_at
        out.append(entry)

    # Person-level numbers, whatever shape the response used.
    for entry in ((person or {}).get("phone_numbers") or ()):
        if not isinstance(entry, dict):
            continue
        add(entry, field='phone_numbers[]')
    for key in ("phone_number", "direct_phone", "mobile_phone",
                "corporate_phone", "home_phone", "other_phone"):
        if (person or {}).get(key):
            add(person[key], flag=key if key != 'phone_number' else None, field=key)

    # The organisation switchboard, in its own lane.
    if org_numbers:
        out.append(classify(org_numbers[0],
                            source_field="organization.primary_phone",
                            org_numbers=org_numbers))
    return merge_phones(out)


def merge_phones(entries: Any) -> list:
    """Merge retained and returned channels without losing explicit types.

    A bare duplicate phone field is weaker than a typed provider entry.
    Conflicting explicit types and blocking findings remain conservative.
    """
    seen: dict = {}
    unique: list = []
    for value in entries:
        if not isinstance(value, dict):
            continue
        entry = dict(value)
        key = normalise(entry.get("number"))
        if not key:
            continue
        if key in seen:
            previous = seen[key]
            blocked = {field: previous[field] for field, values in
                       [('dnc_status_cd', {'found','listed'}), ('status_cd', {'invalid'})]
                       if previous.get(field) in values}
            if previous.get('basis_kind') == 'unestablished' and entry['type'] != UNCLASSIFIED:
                previous.update(entry)
            elif entry.get('basis_kind') != 'unestablished' and previous['type'] != entry['type']:
                previous.update(type=UNCLASSIFIED, basis='conflicting provider phone types', basis_kind='conflicting_types')
            elif previous['type'] == entry['type'] and not previous.get('status_cd') and entry.get('status_cd'):
                # Status and its observation time move together; a bare new
                # phone value cannot make a saved validation look fresh.
                for field in PHONE_METADATA:
                    previous.pop(field, None)
                    if field in entry:
                        previous[field] = entry[field]
            # A second occurrence must not erase a DNC/invalid finding.
            for field, blocked_values in [('dnc_status_cd', {'found', 'listed'}), ('status_cd', {'invalid'})]:
                if entry.get(field) in blocked_values:
                    previous[field] = entry[field]
            previous.update(blocked)
            continue
        seen[key] = entry
        unique.append(entry)
    return unique
