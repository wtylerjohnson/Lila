"""The capability coverage contract: what we search, and what we refuse.

WHAT THIS IS. A client's federally-plausible capability surface, normalised
into families, each with an explicit disposition. Coverage is not "how many
phrases did we try"; it is "does every family have a stated disposition",
which is why the contract carries a disposition percentage rather than a
recall percentage.

THREE DISPOSITIONS.

  active      searched on its phrases, subject to the positive context gate
  controlled  searched ONLY when its positive trigger is present and its
              rejection rule is absent
  (absent)    a family with no entry is not searched, and that is a defect
              in the contract rather than a decision by this module

WHY CONTROLLED EXISTS. Four families are real capabilities whose phrases are
also ordinary federal boilerplate. "Dynamic discounting" is a product; it is
also a prompt-payment clause in thousands of unrelated solicitations.
"Supplier discovery" is a product; it is also what every market-research
notice asks for. Searching them unconditionally floods the artefact with
noise, and dropping them loses real demand. A trigger plus a rejection rule
keeps both.

THE POSITIVE CONTEXT GATE IS THE SINGLE HIGHEST-VALUE RULE HERE. A phrase
match alone put "Altitude Chambers Contractor Logistics Support" and a
VISN "H-Wave" requirement into a payment-integrity opportunity list, because
a description-wide match on common words matches common words. Requiring one
of supplier / vendor / payee / accounts payable / procure-to-pay /
third-party / payment integrity / vendor master in the record removes that
entire class.

PROSE BECOMES MECHANICAL, VISIBLY. The contract states its rejections in
English. English does not filter anything, so each rule is encoded as an
explicit pattern with the originating prose kept beside it in `WHY`. When a
rule is wrong, the prose and the pattern are in the same place.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

COVERAGE_FAMILIES_VERSION = "coverage_families.v1.2026-08-07"

_ROOT = Path(__file__).resolve().parents[2]

ACTIVE = "active"
CONTROLLED = "controlled"

# Accepted, and why it survived. `family` and `rule` are internal receipts.
ACCEPT = "accept"
REJECT = "reject"


# --------------------------------------------------------------------------- #
# The global rejection rules, each carrying the contract prose it encodes.
# --------------------------------------------------------------------------- #
# Boilerplate that means "this phrase is here as submission furniture, not as
# the subject of the requirement".
_SUBMISSION_BOILERPLATE = re.compile(
    r"\b(submit (?:your |the )?(?:offer|quote|proposal|bid)|"
    r"offers? (?:shall|must|are to) be submitted|"
    r"quotes? (?:shall|must) be (?:submitted|emailed)|"
    r"register (?:in|at|with) (?:sam|the .{0,20}portal)|"
    r"must be registered in sam|"
    r"proposals? (?:shall|will) be (?:submitted|received)|"
    r"upload (?:your )?(?:offer|quote|proposal))\b", re.I)

_PAYMENT_CLAUSE = re.compile(
    r"\b(far 52\.232|invoices? (?:shall|must|will) be submitted|"
    r"payment (?:shall|will) be made|prompt payment act|"
    r"wide area workflow|wawf|ipp\.gov|invoice processing platform|"
    r"electronic funds transfer)\b", re.I)

_GENERIC_AI = re.compile(
    r"\b(chatbot|conversational ai|contact cent(?:er|re)|"
    r"large language model|generative ai|agentic ai|llm)\b", re.I)

# "Vendor vetting" is two different industries. Fortior's Omni at USCG
# Base Kodiak vets PEOPLE entering a facility; apex vets SUPPLIERS being
# paid. The word overlaps; the domains do not. This rejection fires on
# access-credentialing language UNLESS the object being evaluated is a
# supplier-side object, which is the operator's exact boundary.
_ACCESS_CREDENTIALING = re.compile(
    r"\b(personnel credentialing|visitor management|badging|badge issuance|"
    r"facility access|base access|installation access|physical access|"
    r"access control system|piv card|cac issuance|gate access|"
    r"escort(?:ing)? (?:of )?visitors)\b", re.I)

_SUPPLIER_OBJECT = re.compile(
    r"\b(supplier|payee|vendor master|bank account|invoice|payment|"
    r"procure-to-pay|accounts payable|remittance|disbursement)\b", re.I)

_GENERIC_CYBER = re.compile(
    r"\b(penetration test|vulnerability scan|sbom|"
    r"software bill of materials|endpoint protection|siem|"
    r"security operations cent(?:er|re)|zero trust)\b", re.I)

GLOBAL_REJECTIONS = (
    {"key": "procurement_clause_only",
     "why": "procurement clause only",
     "pattern": _SUBMISSION_BOILERPLATE, "needs_subject_absent": True},
    {"key": "vendor_portal_submission_only",
     "why": "vendor portal submission instruction only",
     "pattern": _SUBMISSION_BOILERPLATE, "needs_subject_absent": True},
    {"key": "einvoicing_payment_clause_only",
     "why": "electronic invoicing payment clause only",
     "pattern": _PAYMENT_CLAUSE, "needs_subject_absent": True},
    {"key": "generic_ai",
     "why": "generic AI or chatbot",
     "pattern": _GENERIC_AI, "needs_subject_absent": True},
    {"key": "generic_cyber",
     "why": "generic cybersecurity",
     "pattern": _GENERIC_CYBER, "needs_subject_absent": True},
)

# The controlled families' triggers and rejections, encoded. Keyed by the
# family name in the contract so a contract edit and a rule edit collide
# loudly rather than drift apart.
CONTROLLED_RULES = {
    "Supplier discovery": {
        "trigger": re.compile(
            r"\b(data platform|marketplace|industrial base|"
            r"prequalified|pre-qualified|supplier scouting|"
            r"supplier database)\b", re.I),
        "reject": re.compile(
            r"\b(market research|sources sought to identify|"
            r"identify potential (?:offerors|sources|vendors)|"
            r"vendor list|capability statement)\b", re.I),
    },
    "Insurance monitoring": {
        "trigger": re.compile(
            r"\b(certificate of insurance|coi\b|continuous monitoring|"
            r"expiration alert|insurance (?:tracking|verification)|"
            r"software platform)\b", re.I),
        "reject": re.compile(
            r"\b(insurance broker|employee benefit|health insurance|"
            r"construction insurance|bonding requirement|"
            r"one-time certificate)\b", re.I),
    },
    "Inquiry / response agents": {
        "trigger": re.compile(
            r"\b(payment status|invoice (?:status|inquiry)|"
            r"supplier inquiry|vendor inquiry|ap helpdesk|"
            r"self-service portal|virtual agent)\b", re.I),
        "reject": _GENERIC_AI,
    },
    "Healthcare claims review": {
        # Operator ruling 2026-08-07: healthcare-claims context REQUIRED,
        # generic "claims" must not qualify. The trigger needs both the
        # healthcare setting and the payment-integrity job.
        "trigger": re.compile(
            r"(?=.*\b(tricare|medicare|medicaid|health ?care|reimbursement|"
            r"managed care|beneficiar\w+|medical claims)\b)"
            r"(?=.*\b(improper payment|payment integrity|overpayment|"
            r"recovery audit|claims? (?:review|audit))\b)", re.I | re.S),
        "reject": re.compile(
            r"\b(workers.? compensation|property damage|insurance claim form|"
            r"claims adjudication system procurement)\b", re.I),
    },
    "Early payment / dynamic discounting": {
        "trigger": re.compile(
            r"\b(dynamic discount|early payment (?:program|discount)|"
            r"working capital|procure-to-pay|p2p program|"
            r"supply chain financ)\w*\b", re.I),
        "reject": re.compile(
            r"\b(prompt payment act|far 52\.232|net 30|payment terms of|"
            r"discount for prompt payment)\b", re.I),
    },
}


def _clean(value: Any) -> str:
    return " ".join(str(value or "").split())


# A phrase is a MARKETING NORMAL FORM, and a government title is not. The
# contract says "payment fraud detection"; Treasury posted "RFI - Fraud
# Detection and Payment Integrity Tools". Substring matching scores that a
# miss, and it scored 7 of 8 regression receipts a miss when measured.
#
# So a phrase matches on its CONTENT WORDS, order-free: every discriminating
# word of the phrase must be present in the record, anywhere, in any order.
# "payment fraud detection" -> {payment, fraud, detection}, all three of
# which are in Treasury's title. This is not loosening the filter, because
# the positive context gate and the subject test still apply after it; it is
# removing an accident of word order.
_PHRASE_STOP = {"a", "an", "the", "of", "and", "or", "for", "to", "in",
                "with", "solution", "software", "platform", "system",
                "program", "tool", "tools", "services", "service",
                "application", "saas", "workflow", "framework"}

_WORD = re.compile(r"[a-z0-9]+")


def _content_words(phrase: str) -> tuple:
    """The discriminating words of a phrase, stemmed just enough.

    Trailing plurals and -ing forms are folded, so "alerts" matches "alert"
    and "monitoring" matches "monitor". Nothing more aggressive: over-
    stemming is how "audit remediation" starts matching "auditorium".
    """
    words = []
    for word in _WORD.findall(_clean(phrase).casefold()):
        if word in _PHRASE_STOP or len(word) < 3:
            continue
        words.append(_stem(word))
    return tuple(dict.fromkeys(words))


def _stem(word: str) -> str:
    for suffix in ("ization", "isation", "ing", "ers", "ed", "es", "s"):
        if word.endswith(suffix) and len(word) - len(suffix) >= 4:
            return word[: -len(suffix)]
    return word


def phrase_matches(phrase: str, text: str) -> bool:
    """Order-free content-word containment."""
    words = _content_words(phrase)
    if not words:
        return False
    body = {_stem(w) for w in _WORD.findall(_clean(text).casefold())}
    return all(w in body for w in words)


def load(slug: str) -> dict:
    """The client's coverage contract. Absent is empty, never an exception."""
    path = _ROOT / "clients" / _clean(slug) / "coverage_families.json"
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (ValueError, OSError):
        return {}


def family_phrases(family: dict) -> list:
    """A family's full search surface: what the VENDOR calls it, and what the
    GOVERNMENT calls it.

    These are different languages. The vendor sells "overpayment prevention";
    the VA buys a "Payment Integrity Validation and Oversight Tool". Searching
    only the vendor's half is why 4 of 5 testable regression receipts were
    missed on the first measured pass.
    """
    out = [_clean(p) for p in (family.get("phrases") or []) if _clean(p)]
    for form in (family.get("gov_forms") or []):
        phrase = _clean(form.get("phrase") if isinstance(form, dict) else form)
        if phrase:
            out.append(phrase)
    return out


def phrases(contract: dict, *, statuses: Any = (ACTIVE,)) -> list:
    """Every search phrase for families with the given dispositions."""
    out: list = []
    for family in (contract.get("families") or []):
        if family.get("status") in statuses:
            out.extend(family_phrases(family))
    return sorted(set(out), key=str.casefold)


def family_for(contract: dict, phrase: str) -> dict:
    low = _clean(phrase).casefold()
    for family in (contract.get("families") or []):
        if any(_clean(p).casefold() == low for p in family_phrases(family)):
            return family
    return {}


def _has_subject(text: str, family: dict, phrase: str) -> bool:
    """Is the matched phrase the SUBJECT, or incidental furniture?

    The subject test is position plus company: a capability named in the
    title is the requirement, and a capability named alongside two or more
    of its own family's phrases is the requirement even when it sits in the
    body. One mention buried in a payment clause is neither.
    """
    hits = sum(1 for p in family_phrases(family)
               if phrase_matches(p, text))
    return hits >= 2


def qualify(*, title: str, description: str = "", phrase: str,
            contract: dict) -> dict:
    """Does this record belong in the client's opportunity set?

    Returns the decision AND its reason, always. A silent filter is how a
    real requirement disappears without anyone being able to ask why.
    """
    family = family_for(contract, phrase)
    title_text, body = _clean(title), _clean(description)
    text = f"{title_text} {body}"
    low = text.casefold()
    in_title = phrase_matches(phrase, title_text)

    if not family:
        return {"decision": REJECT, "rule": "phrase not in the contract",
                "family": ""}

    name = _clean(family.get("family"))

    # 0 DOMAIN CHECK BEFORE EVERYTHING: access credentialing is another
    #   industry wearing our vocabulary. The supplier-object guard keeps
    #   real supplier vetting in: rejecting requires access language
    #   PRESENT and supplier objects ABSENT.
    if _ACCESS_CREDENTIALING.search(text) and not _SUPPLIER_OBJECT.search(text):
        return {"decision": REJECT, "family": name,
                "rule": "personnel or facility access credentialing",
                "reason_class": "rejected_wrong_domain",
                "detail": "the record vets people entering a facility, not "
                          "suppliers, payees, bank accounts or payments"}

    # 1 THE POSITIVE CONTEXT GATE. One domain word, or it is not our market.
    #   A family may declare its own context when the global gate does not
    #   reach it: "supply chain" is the domain word for multi-tier supplier
    #   mapping, and the FBI's illumination RFI says supply chain throughout
    #   while spelling vendor "vender". Widening the GLOBAL gate to rescue
    #   one family would loosen all twenty-five, so the extra word is scoped
    #   to the family that needs it.
    gate_words = list(contract.get("positive_context_gate") or [])
    gate_words += list(family.get("family_context") or [])
    gate = [g for g in gate_words if _clean(g).casefold() in low]
    if not gate:
        return {"decision": REJECT, "family": name,
                "rule": "no positive context word present",
                "detail": "the record never says supplier, vendor, payee, "
                          "accounts payable, third-party or payment integrity"}

    # 2 CONTROLLED FAMILIES. Trigger present, rejection absent, both.
    if family.get("status") == CONTROLLED:
        rules = CONTROLLED_RULES.get(name)
        if rules is None:
            return {"decision": REJECT, "family": name,
                    "rule": "controlled family has no encoded rule"}
        if rules["reject"].search(text):
            return {"decision": REJECT, "family": name,
                    "rule": "controlled rejection matched",
                    "detail": _clean(family.get("reject"))}
        if not rules["trigger"].search(text):
            return {"decision": REJECT, "family": name,
                    "rule": "controlled trigger absent",
                    "detail": _clean(family.get("trigger"))}

    # 3 GLOBAL REJECTIONS. Boilerplate wins only when the phrase is NOT the
    #   subject: a real vendor-portal requirement also explains how to submit.
    if not in_title and not _has_subject(text, family, phrase):
        for rule in GLOBAL_REJECTIONS:
            if rule["pattern"].search(text):
                return {"decision": REJECT, "family": name,
                        "rule": rule["why"],
                        "detail": "the phrase appears as boilerplate rather "
                                  "than as the subject of the requirement"}

    return {"decision": ACCEPT, "family": name,
            "tier": _clean(family.get("tier")),
            "rule": "phrase in title" if in_title else "phrase is the subject",
            "context": gate[0]}


def best_match(title: str, contract: dict, *, description: str = "",
               statuses: Any = (ACTIVE, CONTROLLED)) -> dict:
    """The single best family for a record, not merely the first found.

    Specificity wins, measured as content-word count. Scanning phrases in
    alphabetical order and taking the first hit filed a Third Party/Vendor
    Risk Management notice under "contract compliance", because "claims
    review" happened to sort earlier than "third-party risk". A two-word
    coincidence must never outrank a three-word match on the real subject.
    """
    # TITLE AND BODY STAY SEPARATE. Folding them into one string makes every
    # phrase look title-resident, and a title match skips the boilerplate
    # rejections by design. Merging them let a Prompt Payment Act clause be
    # accepted as a supplier payment platform.
    text = f"{_clean(title)} {_clean(description)}"
    best: dict = {}
    best_score = (-1, -1)
    for family in (contract.get("families") or []):
        if family.get("status") not in statuses:
            continue
        for phrase in family_phrases(family):
            if not phrase_matches(phrase, text):
                continue
            verdict = qualify(title=title, description=description,
                              phrase=phrase, contract=contract)
            if verdict["decision"] != ACCEPT:
                continue
            score = (len(_content_words(phrase)), len(_clean(phrase)))
            if score > best_score:
                best_score = score
                best = {**verdict, "phrase": phrase}
    return best


def coverage_report(contract: dict) -> dict:
    """The disposition receipt the client artefact publishes."""
    families = contract.get("families") or []
    active = [f for f in families if f.get("status") == ACTIVE]
    controlled = [f for f in families if f.get("status") == CONTROLLED]
    stated = int(contract.get("normalized_federally_plausible_families") or 0)
    return {
        "families": len(families),
        "active": len(active),
        "controlled": len(controlled),
        "phrases": len(phrases(contract, statuses=(ACTIVE, CONTROLLED))),
        "disposition_coverage_percent": (
            100 if families and len(families) == (len(active) + len(controlled))
            else 0),
        "reconciles_with_header": (stated == len(families)) if stated else False,
        "by_tier": {
            tier: sum(1 for f in families if _clean(f.get("tier")) == tier)
            for tier in ("core", "adjacent", "controlled")},
    }


# --------------------------------------------------------------------------- #
# Route-to-market motion rules (operator ruling 3, 2026-08-07)
# --------------------------------------------------------------------------- #
# "Route-to-market limitations should change the MOTION, not erase the
# market." Capability fit and access feasibility are separate dimensions:
# a CPA-led audit requirement is still a high-fit record; apex enters it as
# the technology layer under a qualified prime, and the report says so
# instead of burying the record.
_CPA_LED = re.compile(
    r"\b(541211|certified public accountant|cpa firm|"
    r"independent public accountant|ipa\b)", re.I)

_PURE_ATTEST = re.compile(
    r"\b(audit opinion|attestation engagement|financial statement audit|"
    r"opinion on the financial statements|examination-level attestation)\b",
    re.I)

_ACCESS_RESTRICTED = re.compile(
    r"\b(top secret|ts/sci|security clearance required|cleared personnel|"
    r"set[- ]aside|8\(a\)|sdvosb|wosb|hubzone)\b", re.I)

MOTION_DIRECT_SHAPE = "shape"
MOTION_PARTNER_CPA = "partner under CPA prime"
MOTION_PARTNER_ACCESS = "partner: access required"
MOTION_SUCCESSOR_WATCH = "successor watch"
MOTION_RECOMPETE_WATCH = "recompete watch"

_AUDIT_FAMILIES = ("Financial improvement and audit readiness",
                   "AP recovery audit", "Contract / pricing compliance")


def route_motion(*, family: str, title: str = "", description: str = "",
                 naics: str = "", set_aside: str = "",
                 response_past: bool = False) -> dict:
    """The record-level motion for audit-family records.

    Returns {motion, access_note, direct} where `direct` says whether apex
    can lead. Precedence: pure attest work excludes direct pursuit first,
    then access restrictions, then CPA-led environments, then the clock;
    an open software-addressable record with none of those is a direct
    shape. Fit is NEVER reduced here; only the route changes.
    """
    if _clean(family) not in _AUDIT_FAMILIES:
        return {}
    text = f"{_clean(title)} {_clean(description)}"
    naics_clean = _clean(naics)
    if _PURE_ATTEST.search(text):
        return {"motion": MOTION_PARTNER_CPA, "direct": False,
                "access_note": "Attest authority required: apex enters as "
                               "the technology layer under a CPA firm, "
                               "never as the auditor of record."}
    if _ACCESS_RESTRICTED.search(text) or _clean(set_aside):
        return {"motion": MOTION_PARTNER_ACCESS, "direct": False,
                "access_note": "High-fit mission, partner-required route: "
                               "the vehicle, set-aside or clearance wall "
                               "means a qualified partner carries access."}
    if _CPA_LED.search(text) or naics_clean.startswith("54121"):
        return {"motion": MOTION_PARTNER_CPA, "direct": False,
                "access_note": "High-fit mission, partner-required route: "
                               "a CPA-services prime environment; apex is "
                               "the analytics, controls and recovery-audit "
                               "layer beneath qualified primes."}
    if response_past:
        return {"motion": MOTION_SUCCESSOR_WATCH, "direct": False,
                "access_note": "The posted window closed; the mission "
                               "demand continues. Watch for the successor "
                               "and shape it early."}
    return {"motion": MOTION_DIRECT_SHAPE, "direct": True,
            "access_note": "Open and software-addressable: apex can lead "
                           "with payment analytics, control monitoring and "
                           "remediation technology."}
