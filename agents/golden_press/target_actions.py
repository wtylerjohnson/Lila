"""The actionable target list: motion, account, person, next action.

WHAT THIS PRODUCES. Not a flat list of names. One row per (person, spec)
binding, grouped by commercial motion, carrying the whole chain an operator
has to be able to trace without inference:

    this is the opportunity   -> the decision rule and its cited records
    this is the route         -> the motion and the target organisation
    these are the people      -> the person and the persona they answer
    this is why each matters  -> why_this_account / why_this_person / why_now
    this is what to do next   -> recommended_action, specific to the motion
    these are the records     -> the ids and canonical links proving it

EVERY SENTENCE IS DETERMINISTIC. Not one field here is model prose. The
reasoning is assembled from rule output and OPERATOR-APPROVED parts:

    why_this_account   the decision rule itself (R1 tension, R2 verbatim
                       sentence, R4 adjacency)
    why_this_person    a template over the approved persona: the matched
                       title, its function, and the motion that sought it
    why_now            the clock already on the cited record
    recommended_action a template per motion class

That is what keeps contract L2/L10 intact while the SEARCH vocabulary is
inferred: a model proposes titles, the operator approves them, and the page
is written by templates over what survived. `persona.why` never appears here
(reviewer R5); it stays in the workshop and show-the-work.

NO EVIDENCE UNION. A row carries the join records of ITS OWN spec binding,
never the union across every spec a person happens to answer. That was the
defect one lane over, and it is the join law failing quietly.
"""

from __future__ import annotations

from typing import Any

TARGET_ACTIONS_VERSION = "target_actions.v1.2026-08-06"

_USASPENDING = "https://www.usaspending.gov/award/"
_SAM = "https://sam.gov/opp/"

# Organisation roles a target can occupy. Derived from the spec, never
# asserted about a company beyond what the record shows.
BUYER = "buyer"
PAPER_HOLDER = "paper holder"
ROLE_LABELS = {"buying_component": BUYER, "paper_holder": PAPER_HOLDER}

# One recommended action per motion. Specific enough to run: an operator
# should be able to read it and know what the call is for. Deliberately not
# "reach out" or "introduce the company", which say nothing.
RECOMMENDED_ACTIONS = {
    "defend renewal": (
        "Ask who owns the renewal decision on this record and whether the "
        "vehicle and scope carry forward unchanged past the period end."),
    "validate displacement": (
        "Establish whether the named rival is the incumbent, is specified in "
        "the requirement, or is only referenced, before positioning against "
        "it."),
    "shape future requirement": (
        "Reach the requirement owner while the scope is still being written "
        "and ask what the evaluation will actually test."),
    "engage paper holder": (
        "Ask this holder who owns the route on the cited record and whether "
        "they intend to carry the requirement forward."),
    "partner or channel route": (
        "Qualify whether this channel can reach the requirement the client "
        "cannot reach directly, and who inside it owns that account."),
    "monitor pending evidence": (
        "Confirm whether the anticipated date has moved into an active "
        "procurement before committing outreach effort."),
}

# What a function is called when it is presented to a caller.
FUNCTION_LABELS = {
    "mission/program": "owns the requirement",
    "security operations": "gates the security approval",
    "procurement/contracting": "puts the requirement on contract",
    "channel/prime": "holds the route to the requirement",
}


def _clean(value: Any) -> str:
    return " ".join(str(value or "").split())


def _record_url(record_id: str, rows: Any = None) -> str:
    """The canonical link for a record id, preferring the row's own url."""
    for row in (rows or []):
        if isinstance(row, dict) and str(row.get("record_id")) == str(record_id):
            if _clean(row.get("url")):
                return _clean(row["url"])
    rid = _clean(record_id)
    if len(rid) == 32 and all(c in "0123456789abcdef" for c in rid.casefold()):
        return f"{_SAM}{rid}/view"
    return f"{_USASPENDING}CONT_AWD_{rid}" if rid else ""


def why_this_account(spec: dict) -> str:
    """The decision rule's own reasoning. Never rewritten, never summarised."""
    stated = _clean(spec.get("rationale"))
    if stated:
        return stated
    component = _clean(spec.get("buying_component")) or "this organisation"
    return f"{component} carries a record this report cites."


def why_this_person(contact: dict, spec: dict, motion: dict) -> str:
    """A template over the APPROVED persona. No model prose reaches here."""
    title = _clean(contact.get("title"))
    function = _clean(contact.get("function"))
    label = FUNCTION_LABELS.get(function, "")
    motion_name = _clean(motion.get("motion")) or "this motion"
    if title and label:
        return (f"{title} {label} at the buying organisation, which is the "
                f"function {motion_name} needs.")
    if title:
        return (f"{title} answers the persona {motion_name} seeks at this "
                f"organisation.")
    if label:
        return f"This contact {label}, which is the function {motion_name} needs."
    return f"This contact answers a persona {motion_name} seeks."


def why_now(spec: dict) -> str:
    """The clock already on the cited record. Nothing is invented."""
    for row in (spec.get("join_records") or []):
        if isinstance(row, dict) and _clean(row.get("period_end")):
            return (f"The cited record's current period ends "
                    f"{_clean(row['period_end'])}.")
    evidence = spec.get("evidence") or {}
    for key in ("period_end", "deadline", "anticipated_date"):
        if _clean(evidence.get(key)):
            return f"The cited record carries {key.replace('_', ' ')} " \
                   f"{_clean(evidence[key])}."
    return "No dated clock rides the cited record; timing is unestablished."


def _contact_source_class(contact: dict) -> str:
    source = _clean((contact.get("provenance") or {}).get("source"))
    if source.startswith("sam."):
        return "government published"
    if source.startswith("apollo"):
        return "commercial enrichment"
    return _clean((contact.get("provenance") or {}).get("class")) or "unstated"


def _binding_for(contact: dict, spec_id: str) -> dict:
    for binding in (contact.get("bindings") or []):
        if isinstance(binding, dict) and str(binding.get("spec_id")) == spec_id:
            return binding
    return {}


def build_target_actions(motions: Any, contacts: Any, *,
                         client: str = "", scope: str = "",
                         as_of: str = "") -> dict:
    """One row per (person, spec) binding, grouped by motion.

    A contact answering three specs produces three rows, each carrying only
    ITS OWN spec's records. That is the opposite of unioning the evidence,
    and it is what lets a reader check any single row against the report.
    """
    from agents.golden_press.person_screen import renderable

    by_spec: dict = {}
    for motion in (motions or []):
        for spec in (motion.get("specs") or []):
            by_spec[str(spec.get("spec_id"))] = (motion, spec)

    groups: dict = {}
    rows: list = []
    withheld = {"unscreened": 0, "no_binding": 0}
    for contact in (contacts or []):
        if not isinstance(contact, dict):
            continue
        if not renderable(contact.get("screen")):
            withheld["unscreened"] += 1
            continue
        spec_ids = [str(s) for s in (contact.get("spec_ids")
                                     or [contact.get("spec_id")]) if s]
        matched = False
        for spec_id in spec_ids:
            pair = by_spec.get(spec_id)
            if pair is None:
                continue
            matched = True
            motion, spec = pair
            binding = _binding_for(contact, spec_id)
            # THE JOIN, PER BINDING. Never the union across specs.
            record_ids = [str(r) for r in (binding.get("join_record_ids")
                                           or spec.get("join_record_ids") or [])]
            screen = contact.get("screen") or {}
            phones = contact.get("phones") or []
            row = {
                "client": client, "scope": scope,
                "motion": motion.get("motion"),
                "motion_id": motion.get("motion_id"),
                "outreach_objective": motion.get("objective"),
                "decision_rule": spec.get("source_rule"),
                "spec_id": spec_id,
                "row_class": spec.get("row_class"),
                "buying_agency": spec.get("buying_component"),
                "target_organisation": (spec.get("target_org") or {}).get("name"),
                "organisation_role": ROLE_LABELS.get(
                    (spec.get("target_org") or {}).get("kind"), "other"),
                "route_role": motion.get("route_role"),
                "mission_applicability": motion.get("mission_applicability"),
                "record_ids": record_ids,
                "record_urls": [_record_url(r, spec.get("join_records"))
                                for r in record_ids],
                "bind_strength": binding.get("strength"),
                "bind_why": binding.get("why"),
                "name": contact.get("name"),
                "title": contact.get("title"),
                "persona_tier": contact.get("tier"),
                "persona_function": contact.get("function"),
                "contact_source_class": _contact_source_class(contact),
                "email": contact.get("email"),
                "email_status": contact.get("email_status"),
                "phones": phones,
                "screening_state": screen.get("state"),
                "cautions": list(screen.get("cautions") or []),
                "why_this_account": why_this_account(spec),
                "why_this_person": why_this_person(contact, spec, motion),
                "why_now": why_now(spec),
                "recommended_action": RECOMMENDED_ACTIONS.get(
                    _clean(motion.get("motion")),
                    RECOMMENDED_ACTIONS["monitor pending evidence"]),
                "retrieved_at": (contact.get("provenance") or {}).get(
                    "retrieved_at"),
                "source_refreshed_at": contact.get("source_refreshed_at"),
                "notice_id": contact.get("notice_id"),
                "solicitation_number": contact.get("solicitation_number"),
                "source_url": contact.get("source_url"),
                "as_of": as_of,
            }
            rows.append(row)
            group = groups.setdefault(motion.get("motion_id"), {
                "motion_id": motion.get("motion_id"),
                "motion": motion.get("motion"),
                "objective": motion.get("objective"),
                "organisations": list(motion.get("organisations") or []),
                "spec_count": motion.get("spec_count"),
                "rows": [],
            })
            group["rows"].append(row)
        if not matched:
            withheld["no_binding"] += 1

    # PERSON-LEVEL VIEW OVER THE SAME ROWS. The row model is one per
    # (person, spec) binding and must stay that way, because collapsing it
    # would union the evidence. But a caller reads a call sheet, not a
    # cross-product: one POC bound to twenty specs at the same agency
    # produced twenty identical-looking rows. This groups them without
    # merging their records, so the person appears once and every spec they
    # cover is named underneath.
    for group in groups.values():
        people: dict = {}
        for row in group["rows"]:
            key = _clean(row.get("email")).casefold() or _clean(row.get("name"))
            person = people.setdefault(key, {
                "name": row.get("name"), "title": row.get("title"),
                "email": row.get("email"),
                "email_status": row.get("email_status"),
                "phones": row.get("phones"),
                "persona_function": row.get("persona_function"),
                "persona_tier": row.get("persona_tier"),
                "contact_source_class": row.get("contact_source_class"),
                "organisation": row.get("target_organisation"),
                "screening_state": row.get("screening_state"),
                "cautions": row.get("cautions"),
                "why_this_person": row.get("why_this_person"),
                "recommended_action": row.get("recommended_action"),
                "covers": [],
            })
            person["covers"].append({
                "spec_id": row.get("spec_id"),
                "why_this_account": row.get("why_this_account"),
                "why_now": row.get("why_now"),
                "record_ids": row.get("record_ids"),
                "record_urls": row.get("record_urls"),
                "bind_strength": row.get("bind_strength"),
            })
        group["people"] = [people[k] for k in sorted(people)]
        group["person_count"] = len(people)

    covered = {g["motion_id"] for g in groups.values()}
    gaps = [{"motion_id": m.get("motion_id"), "motion": m.get("motion"),
             "organisations": m.get("organisations"),
             "why": "no accepted contact binds to any spec in this motion"}
            for m in (motions or []) if m.get("motion_id") not in covered]
    return {
        "version": TARGET_ACTIONS_VERSION,
        "client": client, "scope": scope, "as_of": as_of,
        "groups": [groups[k] for k in sorted(groups)],
        "rows": rows,
        "withheld": withheld,
        "supply_gaps": gaps,
        # coverage is stated, never assumed (the FULL COVERAGE defect)
        "action_list_complete": not gaps,
        "motions_total": len(list(motions or [])),
        "motions_covered": len(covered),
    }


ACTION_COLUMNS = (
    "Client", "Motion", "Decision Rule", "Spec ID", "Buying Agency",
    "Target Organisation", "Organisation Role", "Name", "Title",
    "Persona Function", "Persona Tier", "Contact Source", "Email",
    "Email Status", "Mobile", "Direct Phone", "Main Line",
    "Record IDs", "Record URLs", "Bind Strength",
    "Why This Account", "Why This Person", "Why Now",
    "Recommended Action", "Outreach Objective",
    "Screening", "Cautions", "Retrieved At", "Source Refreshed",
    "Notice ID", "Solicitation Number", "Source URL",
)


def action_export_rows(projection: dict) -> list:
    """The CSV shape. It carries the REASONING, not just the identity.

    An export reduced to name, title, company and email throws away every
    reason the person is on the list, which is the whole product.
    """
    from agents.golden_press import phone_policy

    out = []
    for row in (projection or {}).get("rows") or []:
        phones = row.get("phones") or []

        def _pick(kind):
            got = phone_policy.select(phones, kind)
            return got["number"] if got else ""

        out.append({
            "Client": row.get("client") or "",
            "Motion": row.get("motion") or "",
            "Decision Rule": row.get("decision_rule") or "",
            "Spec ID": row.get("spec_id") or "",
            "Buying Agency": row.get("buying_agency") or "",
            "Target Organisation": row.get("target_organisation") or "",
            "Organisation Role": row.get("organisation_role") or "",
            "Name": row.get("name") or "",
            "Title": row.get("title") or "",
            "Persona Function": row.get("persona_function") or "",
            "Persona Tier": (row.get("persona_tier")
                             if row.get("persona_tier") is not None
                             else "unattributed"),
            "Contact Source": row.get("contact_source_class") or "",
            "Email": row.get("email") or "",
            "Email Status": row.get("email_status") or "",
            "Mobile": _pick(phone_policy.MOBILE),
            "Direct Phone": _pick(phone_policy.WORK_DIRECT),
            "Main Line": _pick(phone_policy.ORG_MAIN),
            "Record IDs": " · ".join(row.get("record_ids") or []),
            "Record URLs": " · ".join(row.get("record_urls") or []),
            "Bind Strength": row.get("bind_strength") or "",
            "Why This Account": row.get("why_this_account") or "",
            "Why This Person": row.get("why_this_person") or "",
            "Why Now": row.get("why_now") or "",
            "Recommended Action": row.get("recommended_action") or "",
            "Outreach Objective": row.get("outreach_objective") or "",
            "Screening": row.get("screening_state") or "",
            "Cautions": " · ".join(row.get("cautions") or []),
            "Retrieved At": row.get("retrieved_at") or "",
            "Source Refreshed": row.get("source_refreshed_at") or "",
            "Notice ID": row.get("notice_id") or "",
            "Solicitation Number": row.get("solicitation_number") or "",
            "Source URL": row.get("source_url") or "",
        })
    return out
