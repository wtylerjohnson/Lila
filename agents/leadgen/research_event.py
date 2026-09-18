"""Source-bound early buying decisions, distinct from program research priority.

These are conservative sufficient-evidence rules, not semantic entailment.
Unrecognized wording and missing provenance remain explicit research gaps.
"""
from datetime import date
import re
import json
from typing import Literal
from urllib.parse import urlsplit

from pydantic import Field, model_validator

from agents.assess.contracts import EvidenceRef
from agents.assess.source_clock import acquired_at
from ._base import _FrozenContract
from .enums import SellerPathKind

CLAIMS = {"need", "decision", "fit", "seller_route", "owner"}


class EventClaim(_FrozenContract):
    kind: Literal["need", "decision", "fit", "seller_route", "owner"]
    evidence_id: str = Field(min_length=1)
    quote: str = Field(min_length=8)


class ResearchBuyingEvent(_FrozenContract):
    """One externally named event; never an analyst-created duplicate family."""
    version: Literal["research-buying-event.v1"] = "research-buying-event.v1"
    event_id: str = Field(min_length=3)
    requirement: str = Field(min_length=12)
    buyer_office: str = Field(min_length=3)
    decision_date: date
    supplier: str = Field(min_length=3)
    seller_path: SellerPathKind
    target_name: str = Field(min_length=3)
    claims: tuple[EventClaim, ...]

    @model_validator(mode="after")
    def complete_claim_slots(self):
        for name in ('event_id', 'requirement', 'buyer_office', 'supplier', 'target_name'):
            if len(getattr(self, name).strip()) < 3:
                raise ValueError('buying-event identity and role fields must contain text')
        if len(self.claims) != len(CLAIMS) or {c.kind for c in self.claims} != CLAIMS:
            raise ValueError("buying event needs exactly one claim for each qualification leg")
        if self.seller_path not in {SellerPathKind.CHANNEL_RESELLER, SellerPathKind.PRIME_TO_SUB,
                                    SellerPathKind.VEHICLE_ACCESS}:
            raise ValueError("early buying event needs an explicit eligible supplier path")
        return self


def compact(value):
    return " ".join(str(value or "").split()).casefold()


def contact_gaps(target, as_of):
    proof = target.channel_verification
    if proof is None:
        return ["email and mobile verification receipt missing"]
    gaps = []
    if not target.email or proof.email_status != "verified":
        gaps.append("verified business email missing")
    if not target.phone or proof.phone_type != "mobile" or proof.phone_status != "valid_number":
        gaps.append("verified mobile missing; office/direct phones do not substitute")
    if proof.dnc_status == "listed":
        gaps.append("contact is on a do-not-call list")
    age = ((as_of - proof.verified_at).total_seconds() / 86400 if proof.verified_at
           else (as_of.date() - proof.verified_on).days)
    if not 0 <= age <= 90:
        gaps.append("contact verification is future-dated or older than 90 days")
    refs = {e.evidence_id: e for e in target.evidence}
    ref = refs.get(proof.evidence_id)
    if ref is None or not target.email or compact(target.email) not in compact(ref.excerpt):
        gaps.append("email verification is not bound to retained source")
    digits = re.sub(r"\D", "", target.phone or "")
    if ref is None or not digits or digits not in re.sub(r"\D", "", ref.excerpt):
        gaps.append("mobile verification is not bound to retained source")
    if ref is not None:
        text = compact(ref.excerpt)
        if compact(target.name) not in text or "mobile" not in text or "verified" not in text:
            gaps.append("source does not bind this person's verified email and mobile type")
        try:
            record = json.loads(ref.excerpt)
            expected = {'name': target.name, 'email': target.email, 'phone': target.phone,
                        'email_status': proof.email_status, 'phone_type': proof.phone_type,
                        'phone_status': proof.phone_status, 'dnc_status': proof.dnc_status}
            if any(record.get(k) != v for k, v in expected.items()):
                gaps.append('contact verification fields disagree with the retained provider/source record')
            field = 'verified_at' if proof.verified_at else 'verified_on'
            if str(record.get(field)) != (proof.verified_at or proof.verified_on).isoformat():
                gaps.append('contact verification date is not retained in its source record')
        except (ValueError, TypeError, AttributeError):
            gaps.append('structured contact verification record missing')
    return list(dict.fromkeys(gaps))


def event_gaps(event, evidence, targets, *, as_of, client_name):
    if event is None:
        return ["current buying-event evidence not established"]
    event = ResearchBuyingEvent.model_validate(event.model_dump())
    refs = {e.evidence_id: EvidenceRef.model_validate(e.model_dump()) for e in evidence}
    gaps, quotes = [], {}
    if event.decision_date <= as_of.date():
        gaps.append("decision date is not in the future")
    for claim in event.claims:
        ref = refs.get(claim.evidence_id)
        if ref is None or compact(claim.quote) not in compact(ref.excerpt):
            gaps.append(claim.kind + ": exact source quotation missing")
            continue
        quotes[claim.kind] = compact(claim.quote)
        stamp = acquired_at(ref)
        if stamp is None or not 0 <= (as_of - stamp).total_seconds() <= 90 * 86400:
            gaps.append(claim.kind + ": current source acquisition not established")
        if not ref.primary_source:
            gaps.append(claim.kind + ": primary source required")
        if claim.kind != "fit":
            host = urlsplit(str(ref.source_url)).hostname or ""
            if not host.endswith((".gov", ".mil")):
                gaps.append(claim.kind + ": official buyer source required")
            if ref.kind.value in {"budget", "agency_forecast", "award", "vehicle", "web_lead"}:
                gaps.append(claim.kind + ": planning, award or vehicle evidence alone is insufficient")
            if compact(event.event_id) not in quotes[claim.kind] or compact(event.requirement) not in quotes[claim.kind]:
                gaps.append(claim.kind + ": quotation must identify the same event and requirement")
        if re.search(r"\b(?:not|no|cannot|never|unknown|unconfirmed|cancelled|canceled|withdrawn|completed|awarded|may|might|whether)\b", quotes[claim.kind]):
            gaps.append(claim.kind + ": conditional or closed evidence cannot qualify")
    for ref in refs.values():
        for sentence in re.split(r'(?<=[.!?])\s+|\n', ref.excerpt):
            text = compact(sentence)
            if (compact(event.event_id) in text and compact(event.requirement) in text
                    and re.search(r'\b(?:cancelled|canceled|withdrawn|already awarded|no longer required|completed)\b', text)):
                gaps.append('counterevidence closes or contradicts this buying event')
    need = quotes.get("need", "")
    if not re.search(r"\b(?:requires|will acquire|is procuring|will procure)\b", need):
        gaps.append("buyer has not stated a current requirement")
    decision = quotes.get("decision", "")
    from .qualify import _dated
    if not _dated(decision, event.decision_date) or not re.search(r"\b(?:decision|select|selection|purchase)\b", decision):
        gaps.append("source does not state this buying decision on the exact date")
    route = quotes.get("seller_route", "")
    route_words = {SellerPathKind.CHANNEL_RESELLER: "reseller", SellerPathKind.PRIME_TO_SUB: "prime",
                   SellerPathKind.VEHICLE_ACCESS: "holder"}
    if (compact(event.supplier) not in route or route_words[event.seller_path] not in route
            or not re.search(r"\b(?:eligible|authorized)\b", route)
            or not re.search(r"\b(?:supply|provide|sell)\b", route)
            or compact(client_name) not in route):
        gaps.append("named supplier eligibility to supply this client's product is unproven")
    owner = quotes.get("owner", "")
    if (compact(event.target_name) not in owner or compact(event.buyer_office) not in owner
            or not re.search(r"\b(?:owns|responsible for|decision owner)\b", owner)):
        gaps.append("named person's responsibility for this requirement is unproven")
    fit = quotes.get("fit", "")
    if compact(client_name) not in fit or not re.search(r"\b(?:supports|provides|protects|recovers)\b", fit):
        gaps.append("client capability evidence missing")
    # Shared concrete terms must connect the capability to the actual need.
    terms = set(re.findall(r"[a-z0-9]+", compact(event.requirement))) - {"the", "and", "for", "with", "system", "services"}
    if len(terms & set(re.findall(r"[a-z0-9]+", fit))) < min(2, len(terms)):
        gaps.append("capability evidence does not address the stated requirement")
    selected = [t for t in targets if compact(t.name) == compact(event.target_name)]
    if len(selected) != 1:
        gaps.append("one exact responsible target with email and mobile is required")
    else:
        gaps.extend(contact_gaps(selected[0], as_of))
    return list(dict.fromkeys(gaps))
