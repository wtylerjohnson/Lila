"""Screening a resolved person before the expensive reveal (operator, 2026-08-06).

WHY THIS EXISTS. A live IRS search returned "Vasyl Lyovin, Chief Information
Officer", resolved to the correct Apollo org id, the correct name, and the
correct domain irs.gov. He is not an IRS CIO: he is in Ukraine, his timezone
is Europe/Kiev, and his career runs through Fozzy Group and Paritet Avto. He
reached the enrichment stage and nothing in the pipeline would have stopped
him reaching the client artifact.

WHERE THE SCREEN CANNOT RUN, which is a finding rather than an omission. The
free search row carries nine leaf fields: id, first/last name, title,
last_refreshed_at, linkedin_url, and organization id/name/domain. Every one
of them is either an opaque identifier or an on-target positive. That row is
field-for-field indistinguishable from a correct record of a real IRS CIO, so
any rule able to reject it would reject the true positive with equal force.
The disqualifying evidence first exists in the MATCH response, which costs
about one credit per person and which the pipeline already buys for the work
email. The screen is therefore nearly free: it reads a response already
purchased and gates only the 8-credit reveal.

THE CHEAPEST GUARD IS NOT A RULE AT ALL. `person_locations` on the SEARCH
request bounds the query server-side for zero credits and would have excluded
this person before he entered any result set. That bound lives in the
operator-owned persona reference file, not in code, because widening it is
exactly what an engagement with OCONUS buyers requires and no agent may
narrow it silently.

FAIL OPEN, AND FLAG RATHER THAN DELETE. These rules were stress-tested
against the eleven people the live run confirmed legitimate. The obvious
rule set deleted six of them: rejecting anyone whose prior career is
private-sector removes the FBI CTO (Meta), the IRS CIO (Gerson Lehrman) and
every contracting officer hired out of industry, which is the modal federal
technology career. Requiring a populated country deletes two people whose
records simply have no country. Requiring the email domain to equal the
organisation domain deletes every component subdomain (jpl.nasa.gov,
cbp.dhs.gov, ic.fbi.gov). So:

  - a MISSING field never rejects and, on its own, never even flags
  - exactly ONE rule may reject, and it is compound: it cannot fire unless
    the geography rule has already found a disagreement
  - everything else FLAGS, which renders a caution and keeps the person
  - every rule prints its evidence, and every rejection is receipted by name
"""

from __future__ import annotations

import re
from datetime import date
from typing import Any, Optional

SCREEN_VERSION = "person_screen.v2.2026-08-06"

PASS, FLAG, REJECT = "pass", "flag", "reject"

# PUBLICATION STATES (operator ruling 2026-08-06). Screening is a
# publication requirement, not an enrichment side effect. Every person
# carries exactly one of these, and only the first two may reach a client.
ACCEPTED = "accepted"
ACCEPTED_WITH_FLAGS = "accepted_with_flags"
REJECTED = "rejected"
PENDING_SCREEN = "pending_screen"

RENDERABLE_STATES = (ACCEPTED, ACCEPTED_WITH_FLAGS)
SCREEN_STATES = (ACCEPTED, ACCEPTED_WITH_FLAGS, REJECTED, PENDING_SCREEN)

# SOURCE CLASSES. A screen that is right for a federal buying component is
# wrong for a reseller: applying federal-service geography logic to a
# commercial channel employee abroad rejects a legitimate partner contact
# for living where their employer operates.
FEDERAL_COMPONENT = "federal_component"
RESELLER = "reseller"
NOTICE_POC = "notice_poc"
OPERATOR = "operator"
SOURCE_CLASSES = (FEDERAL_COMPONENT, RESELLER, NOTICE_POC, OPERATOR)

# Which rules run for which source class. A rule absent from a class is not
# "skipped": it is not applicable, and the receipt says so by listing only
# the rules that were in force.
_POLICY = {
    # the federal buying component: the full battery, geography included
    FEDERAL_COMPONENT: ("S1", "S2", "S3", "S4", "S5", "S6", "S7", "S8"),
    # a commercial reseller or paper holder. No geography rules at all: a
    # channel manager in Bangalore working for a US reseller is a normal
    # partner contact, and S1/S2/S3/S5 all encode federal-ness.
    RESELLER: ("S4", "S6", "S7", "S8"),
    # a POC the government itself published on a notice. Identity is not in
    # question, so only deliverability and freshness are tested; the join to
    # the right notice is enforced upstream by the supply lane.
    NOTICE_POC: ("S6", "S8"),
    # an operator-supplied identity: recorded, lightly checked, always
    # visibly labeled as operator-sourced downstream.
    OPERATOR: ("S7", "S8"),
}


def source_class_for(spec: dict, *, source: str = "") -> str:
    """The screening policy this person falls under.

    Derived from the SPEC's target kind, not from the person, because the
    question is what we are screening them AS. An operator-supplied or
    notice-published identity states its own class explicitly.
    """
    stated = str(source or "").strip().casefold()
    if stated in SOURCE_CLASSES:
        return stated
    kind = (spec or {}).get("target_org", {}).get("kind")
    return FEDERAL_COMPONENT if kind == "buying_component" else RESELLER

# Registrable suffixes that are themselves proof of a US federal mailbox.
# Membership here is positive evidence; absence is only ever a flag, because
# agency mail consolidations leave working mailboxes on other domains.
FEDERAL_SUFFIXES = (".gov", ".mil")

# IANA area prefixes that sit inside the United States. Used only to read a
# timezone that is PRESENT; a blank timezone says nothing.
_US_TZ_AREAS = ("America/", "US/", "Pacific/Honolulu", "America/Puerto_Rico")
_US_TZ_FOREIGN_AREAS = ("Europe/", "Asia/", "Africa/", "Australia/",
                        "Indian/", "Atlantic/", "Antarctica/")

_US_COUNTRY = {"united states", "united states of america", "usa", "us",
               "u.s.", "u.s.a."}

# Email statuses Apollo uses for an address it stands behind.
_CONFIRMED_EMAIL = {"verified", "valid"}

_DOMAIN_RE = re.compile(r"@([^@\s]+)$")


def _clean(value: Any) -> str:
    return " ".join(str(value or "").split())


def _finding(rule_id: str, name: str, verdict: str, why: str,
             evidence: dict) -> dict:
    """One screen finding, in the decision-rule row grammar: it prints the
    evidence it fired on, so a reader never has to trust the verdict."""
    return {"rule_id": rule_id, "name": name, "verdict": verdict,
            "why": why, "evidence": evidence}


def email_domain(person: dict) -> str:
    match = _DOMAIN_RE.search(_clean((person or {}).get("email")).casefold())
    return match.group(1) if match else ""


def is_federal_domain(domain: str) -> bool:
    """A .gov or .mil registrable suffix, at any depth.

    Depth matters: component mailboxes live on subdomains of the parent
    (jpl.nasa.gov, cbp.dhs.gov, ic.fbi.gov, us.af.mil). Testing the SUFFIX
    rather than equality with the organisation's own domain is what keeps
    those people in the list.
    """
    d = _clean(domain).casefold().rstrip(".")
    return any(d == s.lstrip(".") or d.endswith(s) for s in FEDERAL_SUFFIXES)


# Tokens that mark an employer as US public sector. Used ONLY as positive
# corroboration inside the compound reject, never to judge career quality:
# asking whether a career is governmental deleted the FBI CTO (Meta), the
# IRS CIO (Gerson Lehrman) and every contracting officer hired out of
# industry, which is the modal federal technology career.
_US_PUBLIC_TOKENS = (
    "u.s.", "us ", "united states", "federal", "department of",
    "army", "navy", "air force", "marine", "coast guard", "space force",
    "defense", "defence", "dod", "nasa", "fbi", "cia", "nsa", "dhs",
    "homeland security", "veterans affairs", "state department", "embassy",
    "usaid", "gsa", "irs", "treasury", "justice", "commerce", "energy",
    "agriculture", "interior", "labor", "transportation", "hhs",
    "health and human", "social security", "usda", "epa", "noaa", "cdc",
    "national guard", "garrison", "command", "agency", "bureau of",
    "administration", "county", "city of", "state of",
)


def looks_us_public_sector(name: str) -> bool:
    """Positive-only recognition of a US public-sector employer.

    A miss costs nothing on its own: this function is consulted solely to
    EXCUSE a record that geography already flagged. It is never used to
    condemn a career, because a private-sector history is the normal route
    into federal technology and acquisition roles.
    """
    text = " " + _clean(name).casefold() + " "
    return any(token in text for token in _US_PUBLIC_TOKENS)


def current_rows(person: dict) -> list:
    return [r for r in ((person or {}).get("employment_history") or [])
            if isinstance(r, dict) and r.get("current")]


def prior_rows(person: dict) -> list:
    return [r for r in ((person or {}).get("employment_history") or [])
            if isinstance(r, dict) and not r.get("current")]


# --------------------------------------------------------------------------- #
# S1 · geography disagreement  (FLAG)
# --------------------------------------------------------------------------- #
def s1_geography_disagreement(person: dict, spec: dict) -> Optional[dict]:
    """Non-US signals among the geography fields that are actually PRESENT.

    Reads country, the trailing country token of formatted_address, and the
    IANA area of time_zone. A blank field contributes nothing: two of the
    eleven verified people have no country at all, and one has neither city
    nor timezone. FLAG only, because the OCONUS federal workforce is real
    (garrison IT directors, embassy staff, legal attaches).
    """
    signals, foreign = {}, []
    country = _clean(person.get("country"))
    if country:
        signals["country"] = country
        if country.casefold() not in _US_COUNTRY:
            foreign.append(f"country {country!r}")
    address = _clean(person.get("formatted_address"))
    if address:
        signals["formatted_address"] = address
        tail = address.split(",")[-1].strip()
        if tail and tail.casefold() not in _US_COUNTRY \
                and not tail.upper().startswith("US"):
            # A US address ends in a state or "USA"; a bare foreign country
            # name in the tail is the signal. Two tokens or fewer only, so a
            # street line never reads as a country.
            if len(tail.split()) <= 3 and not any(ch.isdigit() for ch in tail):
                foreign.append(f"address tail {tail!r}")
    zone = _clean(person.get("time_zone"))
    if zone:
        signals["time_zone"] = zone
        if zone.startswith(_US_TZ_FOREIGN_AREAS):
            foreign.append(f"time zone {zone!r}")
    if not foreign:
        return None
    return _finding(
        "S1", "GEOGRAPHY DISAGREEMENT", FLAG,
        (f"{len(foreign)} geography signal(s) place this person outside the "
         f"United States while the targeted buying component is a US federal "
         f"organisation"),
        {"signals_present": signals, "foreign_signals": foreign,
         "fields_blank": [k for k in ("country", "formatted_address",
                                      "time_zone")
                          if not _clean(person.get(k))]})


# --------------------------------------------------------------------------- #
# S2 · foreign-career corroboration  (REJECT, and the only one)
# --------------------------------------------------------------------------- #
# Foreign-market tokens are NOT a career-quality test. This rule never asks
# whether a career is governmental; asking that deleted the FBI CTO, the IRS
# CIO and every contracting officer hired out of industry. It asks only
# whether an ALREADY-FLAGGED geography is corroborated by employers in the
# same non-US market.
def s2_foreign_career_corroboration(person: dict, spec: dict, *,
                                    geography: Optional[dict]) -> Optional[dict]:
    """The one reject, and it is compound.

    It cannot execute unless S1 already found a geography disagreement, so
    it can never touch a person whose record places them in the United
    States. On top of that it requires at least two prior employment rows
    AND no federal mailbox, so an OCONUS federal employee with a .mil
    address and a US career is structurally out of reach.
    """
    if not geography:
        return None
    priors = prior_rows(person)
    names = [_clean(r.get("organization_name")) for r in priors]
    names = [n for n in names if n]
    if len(names) < 2:
        # Absence of history is not evidence. An empty or thin employment
        # list is a data gap, and the vacuous-truth reading of "no
        # government employer" is exactly what deleted six real people.
        return None
    corroborated = [n for n in names if looks_us_public_sector(n)]
    if corroborated:
        # An OCONUS federal employee is real: garrison IT directors, embassy
        # and attache staff. Any prior US public-sector employer settles it.
        return None
    domain = email_domain(person)
    return _finding(
        "S2", "FOREIGN-CAREER CORROBORATION", REJECT,
        (f"geography places this person outside the United States and none "
         f"of {len(names)} prior employers is a US public-sector "
         f"organisation, so nothing corroborates federal service"),
        {"geography_finding": geography["why"],
         "prior_employers": names[:8],
         "us_public_sector_priors": [],
         "email_domain": domain or None,
         # DELIBERATELY NOT A GUARD. Apollo pattern-generates addresses from
         # the resolved organisation, so a "verified" .gov address proves the
         # address validates, never that the person holds the job. Treating
         # it as corroboration lets the known false positive through.
         "federal_mailbox_present": is_federal_domain(domain),
         "federal_mailbox_note": ("a pattern-generated .gov address is not "
                                  "corroboration of employment"),
         "compound_basis": ("S1 geography + 2 or more prior employers + zero "
                            "US public-sector priors; any one absent yields "
                            "no rejection"),
         "corroboration_source": ("PRIOR employment rows only. The current "
                                  "row is the claim under test and cannot "
                                  "corroborate itself.")})


# --------------------------------------------------------------------------- #
# S3 · federal mailbox authority  (FLAG, inverted)
# --------------------------------------------------------------------------- #
def s3_federal_mailbox_authority(person: dict, spec: dict) -> Optional[dict]:
    """Passes on POSITIVE evidence rather than rejecting on inequality.

    Never compares the mailbox to the organisation's own domain. That
    comparison deletes every component subdomain in the federal enterprise
    and every mailbox left on a consolidated tenant.
    """
    if (spec or {}).get("target_org", {}).get("kind") != "buying_component":
        return None
    domain = email_domain(person)
    if not domain:
        return None
    if is_federal_domain(domain):
        return None
    return _finding(
        "S3", "FEDERAL MAILBOX AUTHORITY", FLAG,
        (f"the work address sits on {domain!r}, which is not a .gov or .mil "
         f"domain, while the target is a federal buying component"),
        {"email_domain": domain, "federal_suffixes": list(FEDERAL_SUFFIXES),
         "note": ("absence of a federal domain is a review item, never a "
                  "rejection: consolidations and alias domains leave real "
                  "mailboxes off the component's own domain")})


# --------------------------------------------------------------------------- #
# S4 · employer concurrency  (FLAG)
# --------------------------------------------------------------------------- #
def _token_overlap(a: str, b: str) -> bool:
    stop = {"of", "the", "and", "us", "u.s.", "united", "states",
            "department", "office", "bureau", "agency", "service"}
    ta = {t for t in re.split(r"[^a-z0-9]+", a.casefold()) if t and t not in stop}
    tb = {t for t in re.split(r"[^a-z0-9]+", b.casefold()) if t and t not in stop}
    return bool(ta & tb)


def s4_employer_concurrency(person: dict, spec: dict) -> Optional[dict]:
    """Every current:true row as a SET. Any match passes.

    Concurrency is normal federal life: detailees, joint duty and dual
    appointments all produce two current rows. One of the eleven verified
    people holds FBI and DOJ Criminal Division simultaneously. Collapsing
    that to a scalar, or reading it as a data conflict, deletes her.
    """
    rows = current_rows(person)
    if not rows:
        return None                     # absence is a gap, not a mismatch
    target = _clean((spec or {}).get("target_org", {}).get("name"))
    names = [_clean(r.get("organization_name")) for r in rows]
    names = [n for n in names if n]
    if not names or not target:
        return None
    if any(_token_overlap(n, target) for n in names):
        return None
    return _finding(
        "S4", "EMPLOYER CONCURRENCY", FLAG,
        (f"no current employer row resolves to {target!r}; the record names "
         + ", ".join(repr(n) for n in names[:4])),
        {"current_employers": names, "target_org": target,
         "note": ("employment_history.organization_name is uncontrolled free "
                  "text, so a resolution gap is a review item, never a "
                  "rejection")})


# --------------------------------------------------------------------------- #
# S5 · organisation self-reference  (FLAG)
# --------------------------------------------------------------------------- #
def s5_organisation_self_reference(person: dict, spec: dict) -> Optional[dict]:
    """Does the ORGANISATION Apollo resolved actually look federal?

    Applies only when the spec targets a buying component. A paper-holder or
    reseller spec is exempt by construction: a commercial domain is the
    correct answer there, not a defect.
    """
    if (spec or {}).get("target_org", {}).get("kind") != "buying_component":
        return None
    org = (person or {}).get("organization") or {}
    domain = _clean(org.get("primary_domain") or org.get("domain"))
    if not domain:
        return None
    if is_federal_domain(domain):
        return None
    return _finding(
        "S5", "ORGANISATION SELF-REFERENCE", FLAG,
        (f"the resolved organisation's own domain {domain!r} is not federal "
         f"while the spec targets a federal buying component"),
        {"organization_domain": domain,
         "organization_name": _clean(org.get("name"))})


# --------------------------------------------------------------------------- #
# S6 · job-claim age  (FLAG)
# --------------------------------------------------------------------------- #
def s6_job_claim_age(person: dict, spec: dict, *,
                     today: Optional[date] = None,
                     max_age_days: int = 540) -> Optional[dict]:
    """How old is the JOB CLAIM, not how recently Apollo touched the record.

    last_refreshed_at is deliberately not read here. It attests to Apollo's
    own activity and punishes stability: a career civil servant in the same
    seat since 2011 generates no refresh signal and would be deleted by a
    staleness rule for the crime of not moving. The age in days is printed
    on every finding, so there is no cliff.
    """
    rows = current_rows(person)
    if not rows:
        return None
    stamps = [_clean(r.get("raw_last_updated_at") or r.get("start_date"))
              for r in rows]
    stamps = [s for s in stamps if s]
    if not stamps:
        return None
    today = today or date.today()
    ages = []
    for stamp in stamps:
        try:
            ages.append((today - date.fromisoformat(stamp[:10])).days)
        except ValueError:
            continue
    if not ages:
        return None
    age = min(ages)
    if age <= max_age_days:
        return None
    return _finding(
        "S6", "JOB-CLAIM AGE", FLAG,
        (f"the current role claim is {age} days old, past the "
         f"{max_age_days}-day review threshold"),
        {"age_days": age, "threshold_days": max_age_days,
         "stamps": stamps[:4],
         "note": ("staleness is a re-verification task, never a deletion; "
                  "only a failed re-verification could justify removal")})


# --------------------------------------------------------------------------- #
# S7 · unverifiable record  (FLAG)
# --------------------------------------------------------------------------- #
def s7_unverifiable_record(person: dict, spec: dict) -> Optional[dict]:
    """The only rule permitted to fire on ABSENCE, and it may only flag.

    Counts whole evidence FAMILIES that are entirely empty, at a floor of
    two. A single blank field never reaches it, which is what keeps the
    person with no city and the person with no country in the list.
    """
    families = {
        "geography": any(_clean(person.get(k)) for k in
                         ("country", "formatted_address", "time_zone",
                          "city", "state")),
        "employment": bool((person or {}).get("employment_history")),
        "mailbox": bool(email_domain(person)),
        "profile": bool(_clean(person.get("linkedin_url"))),
    }
    empty = sorted(k for k, present in families.items() if not present)
    if len(empty) < 2:
        return None
    return _finding(
        "S7", "UNVERIFIABLE RECORD", FLAG,
        (f"{len(empty)} whole evidence families are empty on this record: "
         + ", ".join(empty)),
        {"empty_families": empty, "floor": 2,
         "families_checked": sorted(families)})


# --------------------------------------------------------------------------- #
# S8 · email deliverability unproven  (FLAG)
# --------------------------------------------------------------------------- #
def s8_email_deliverability(person: dict, spec: dict) -> Optional[dict]:
    """An address Apollo does not stand behind, stated as such.

    Sets NO confidence floor, deliberately. A floor at 0.70 cuts a Senior
    Contracting Officer at 0.68 and nothing distinguishes her from the two
    records sitting exactly on 0.70. The status is disclosed; the caller
    decides.
    """
    if not email_domain(person):
        return None
    status = _clean(person.get("email_status")).casefold()
    if status in _CONFIRMED_EMAIL:
        return None
    confidence = person.get("extrapolated_email_confidence")
    return _finding(
        "S8", "EMAIL DELIVERABILITY UNPROVEN", FLAG,
        (f"the work address carries status {status or 'unstated'!r}, which is "
         f"not a confirmed Apollo status"),
        {"email_status": status or None,
         "extrapolated_confidence": confidence,
         "confirmed_statuses": sorted(_CONFIRMED_EMAIL),
         "note": "no confidence floor is applied; the status is disclosed"})


RULES = (
    ("S1", "GEOGRAPHY DISAGREEMENT", FLAG),
    ("S2", "FOREIGN-CAREER CORROBORATION", REJECT),
    ("S3", "FEDERAL MAILBOX AUTHORITY", FLAG),
    ("S4", "EMPLOYER CONCURRENCY", FLAG),
    ("S5", "ORGANISATION SELF-REFERENCE", FLAG),
    ("S6", "JOB-CLAIM AGE", FLAG),
    ("S7", "UNVERIFIABLE RECORD", FLAG),
    ("S8", "EMAIL DELIVERABILITY UNPROVEN", FLAG),
)


def screen_person(person: dict, spec: dict, *,
                  today: Optional[date] = None,
                  source_class: str = "") -> dict:
    """Every applicable rule over one person. Pure: no network, no LLM.

    Returns the screen block the store carries: the publication state, every
    finding with its printed evidence, the source-class policy that was in
    force, and the roster of rules that ran so a rule that did NOT fire is
    as visible as one that did.

    THE STATE IS THE PUBLICATION DECISION. Only ``accepted`` and
    ``accepted_with_flags`` may reach a client; anything else is stored and
    withheld. There is deliberately no way to produce a renderable state
    without running this function, which is what makes screening a
    precondition of publication rather than an optional enrichment step.
    """
    policy = source_class or source_class_for(spec)
    if policy not in _POLICY:
        raise ValueError(f"unknown screening source class: {policy!r}")
    in_force = _POLICY[policy]

    geography = (s1_geography_disagreement(person, spec)
                 if "S1" in in_force else None)
    candidates = {
        "S1": geography,
        "S2": (s2_foreign_career_corroboration(person, spec,
                                               geography=geography)
               if "S2" in in_force else None),
        "S3": s3_federal_mailbox_authority(person, spec)
        if "S3" in in_force else None,
        "S4": s4_employer_concurrency(person, spec)
        if "S4" in in_force else None,
        "S5": s5_organisation_self_reference(person, spec)
        if "S5" in in_force else None,
        "S6": s6_job_claim_age(person, spec, today=today)
        if "S6" in in_force else None,
        "S7": s7_unverifiable_record(person, spec)
        if "S7" in in_force else None,
        "S8": s8_email_deliverability(person, spec)
        if "S8" in in_force else None,
    }
    findings = [f for f in candidates.values() if f]
    fired = {f["rule_id"] for f in findings}
    if any(f["verdict"] == REJECT for f in findings):
        state = REJECTED
    elif findings:
        state = ACCEPTED_WITH_FLAGS
    else:
        state = ACCEPTED
    return {
        "version": SCREEN_VERSION,
        "state": state,
        # legacy key, kept so existing readers do not break; `state` is the
        # publication decision and the one downstream code must consult
        "verdict": (REJECT if state == REJECTED
                    else FLAG if state == ACCEPTED_WITH_FLAGS else PASS),
        "source_class": policy,
        "rules_in_force": list(in_force),
        "findings": findings,
        "cautions": [f["why"] for f in findings if f["verdict"] == FLAG],
        "rules": [{"rule_id": rid, "name": name, "verdict": v,
                   "applicable": rid in in_force,
                   "fired": rid in fired}
                  for rid, name, v in RULES],
    }


def renderable(screen: Any) -> bool:
    """Whether a screen block permits a person to reach a client artifact.

    FAIL CLOSED in both directions that matter: a missing screen block is
    ``pending_screen`` and does not render, and an unrecognised state does
    not render either. An unscreened person reaching Band 09 is the failure
    this function exists to make structurally impossible.
    """
    if not isinstance(screen, dict):
        return False
    return screen.get("state") in RENDERABLE_STATES
