"""Evidence-bound values: a number that can defend itself.

THE LAW THIS ENCODES: every material number in a client document carries at
least one of a direct source link, a show-the-work receipt, or a calculation
that resolves to linked source records. A figure that cannot do one of those
is not a fact, it is a string.

WHY IT EXISTS. A rendered total of $106,166,241,480.63 shipped in this
family earlier today. It was produced by summing PRE-CAP screened rows (54
rival records, 87 forecasts) while the document cited 25, and nothing in the
type system could tell the difference between that and a real figure,
because both were `float`. `ExactMoney` makes the population part of the
value: a total that cannot name what it summed cannot be built.

THREE OBJECTS.

  EvidenceReference  a source id, its kind, its canonical url, WHAT it is
                     allowed to prove, and when it was retrieved.
  ExactMoney         an exact Decimal or the source's own literal, its
                     display form, its basis, its population, its evidence,
                     and its components when it was calculated.
  CoverageState      complete, partial, or research_next, with the fields
                     known, the fields missing, and the next research action.

SOURCE ROLES ARE ENFORCED, NOT DOCUMENTED. A USAspending award proves an
award, a recipient, a spend amount and a paper holder. It does NOT prove an
open opportunity, and `claim_roles` is what makes that mechanical: asking an
award reference to support an opportunity claim raises rather than renders.

NO BINARY FLOAT IN A CLIENT FIGURE. Money is a Decimal or a preserved source
literal. `GoldenRecord.obligated_dollars` is still a float and is a contract
surface, so `from_record_float` converts once, at the boundary, and records
that it did.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from typing import Any

EVIDENCE_OBJECTS_VERSION = "evidence_objects.v1.2026-08-07"

# What a source of each kind is ALLOWED to prove. The report's whole
# credibility rests on not letting an award stand in for an opportunity.
AWARD = "usaspending_award"
NOTICE = "sam_notice"
FORECAST = "agency_forecast"
EVENT = "official_event"
CONTACT = "published_contact"
PROFILE = "operator_profile"

CLAIM_AWARD = "award"
CLAIM_SPEND = "spend"
CLAIM_RECIPIENT = "recipient"
CLAIM_PAPER_HOLDER = "paper_holder"
CLAIM_OPPORTUNITY = "opportunity"
CLAIM_EVENT = "event"
CLAIM_CONTACT = "contact"
CLAIM_CAPABILITY = "capability"

SOURCE_ROLES = {
    AWARD: (CLAIM_AWARD, CLAIM_SPEND, CLAIM_RECIPIENT, CLAIM_PAPER_HOLDER),
    NOTICE: (CLAIM_OPPORTUNITY, CLAIM_CONTACT, CLAIM_EVENT),
    FORECAST: (CLAIM_OPPORTUNITY,),
    EVENT: (CLAIM_EVENT,),
    CONTACT: (CLAIM_CONTACT,),
    PROFILE: (CLAIM_CAPABILITY,),
}

# Coverage vocabulary. `research_next` is deliberately not "failed": absence
# is a work order, and the client copy says what happens next.
COMPLETE = "complete"
PARTIAL = "partial"
RESEARCH_NEXT = "research_next"
COVERAGE_STATES = (COMPLETE, PARTIAL, RESEARCH_NEXT)

_USASPENDING = "https://www.usaspending.gov/award/"
_SAM = "https://sam.gov/opp/"


class EvidenceError(ValueError):
    """A value was asked to prove something its source cannot prove."""


def _clean(value: Any) -> str:
    return " ".join(str(value or "").split())


@dataclass(frozen=True)
class EvidenceReference:
    """One primary record, and what it is allowed to prove."""

    source_id: str
    source_kind: str
    source_url: str = ""
    claim_roles: tuple = ()
    retrieved_at: str = ""
    label: str = ""

    def __post_init__(self) -> None:
        if not _clean(self.source_id):
            raise EvidenceError("an evidence reference needs a source id")
        if self.source_kind not in SOURCE_ROLES:
            raise EvidenceError(
                f"unknown source kind {self.source_kind!r}; known kinds are "
                f"{sorted(SOURCE_ROLES)}")
        allowed = SOURCE_ROLES[self.source_kind]
        for role in self.claim_roles:
            if role not in allowed:
                raise EvidenceError(
                    f"a {self.source_kind} cannot prove {role!r}; it proves "
                    f"{list(allowed)}. A USAspending award proves an award, a "
                    f"recipient and a spend amount, never an open "
                    f"opportunity.")

    def supports(self, role: str) -> bool:
        return role in (self.claim_roles or SOURCE_ROLES[self.source_kind])

    def require(self, role: str) -> None:
        if not self.supports(role):
            raise EvidenceError(
                f"{self.source_id} ({self.source_kind}) cannot support a "
                f"{role!r} claim")

    @property
    def url(self) -> str:
        """A URL we can stand behind, or nothing.

        AWARD LINKS ARE NEVER FABRICATED (grading round 1: 17 of 19
        usaspending links 404ed). A USAspending award page needs the
        generated internal id, and guessing CONT_AWD_<piid> manufactures
        dead links that read as receipts. An award reference renders its id
        as text unless a canonical url was captured with it. SAM notice
        links keep the one form that is deterministic from a 32-char id.
        """
        if _clean(self.source_url):
            return _clean(self.source_url)
        sid = _clean(self.source_id)
        if self.source_kind == NOTICE and len(sid) == 32:
            return f"{_SAM}{sid}/view"
        return ""

    def as_dict(self) -> dict:
        return {"source_id": self.source_id, "source_kind": self.source_kind,
                "source_url": self.url, "claim_roles": list(self.claim_roles),
                "retrieved_at": self.retrieved_at, "label": self.label}


def _to_decimal(value: Any) -> Decimal:
    if isinstance(value, Decimal):
        return value
    text = _clean(value).replace("$", "").replace(",", "")
    if not text:
        return Decimal("0")
    try:
        return Decimal(text)
    except InvalidOperation as exc:
        raise EvidenceError(f"{value!r} is not an exact amount") from exc


@dataclass(frozen=True)
class ExactMoney:
    """An exact amount that knows what it counted and where it came from.

    NEVER ABBREVIATED IN THE PRINCIPAL DISPLAY. $142,847,391.26 renders as
    $142,847,391.26. A compact label may sit beside it, never instead of it.
    """

    amount: Decimal
    basis: str                     # "obligated on cited records"
    population: str                # "25 cited records, FY2026 to date"
    evidence: tuple = ()
    components: tuple = ()         # (label, ExactMoney) for a calculated total
    source_literal: str = ""       # the source's own string, when it had one

    def __post_init__(self) -> None:
        if not _clean(self.basis):
            raise EvidenceError("an amount must state its basis")
        if not _clean(self.population):
            raise EvidenceError(
                "an amount must state the population it counted; a total that "
                "cannot name what it summed is how a $106bn figure reached a "
                "client document")
        for ref in self.evidence:
            ref.require(CLAIM_SPEND)
        if self.components:
            total = sum((c[1].amount for c in self.components), Decimal("0"))
            if total != self.amount:
                raise EvidenceError(
                    f"components sum to {total} but the total states "
                    f"{self.amount}; a show-the-work receipt must reconcile "
                    f"exactly")

    @classmethod
    def from_record_float(cls, value: Any, *, basis: str, population: str,
                          evidence: tuple = (), literal: str = "") -> "ExactMoney":
        """The ONE conversion point from the pack's float contract.

        `GoldenRecord.obligated_dollars` is a float and is a contract
        surface, so the conversion happens here, once, and the source
        literal is preserved when the source had one.
        """
        return cls(amount=_to_decimal(literal or repr(float(value or 0.0))),
                   basis=basis, population=population, evidence=tuple(evidence),
                   source_literal=_clean(literal))

    @classmethod
    def summed(cls, parts: Any, *, basis: str, population: str) -> "ExactMoney":
        """A total that carries its components, so it can show its work."""
        rows = [(label, money) for label, money in parts]
        total = sum((m.amount for _, m in rows), Decimal("0"))
        evidence: list = []
        for _, money in rows:
            for ref in money.evidence:
                if ref not in evidence:
                    evidence.append(ref)
        return cls(amount=total, basis=basis, population=population,
                   evidence=tuple(evidence), components=tuple(rows))

    @property
    def display(self) -> str:
        """The principal client display. Exact, always."""
        if self.source_literal:
            return self.source_literal
        q = self.amount.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
        return f"${q:,}"

    @property
    def compact(self) -> str:
        """A secondary label ONLY. Never renders without `display` beside it."""
        a = abs(self.amount)
        for unit, size in (("B", Decimal("1e9")), ("M", Decimal("1e6")),
                           ("K", Decimal("1e3"))):
            if a >= size:
                return f"${(self.amount / size).quantize(Decimal('0.1'))}{unit}"
        return self.display

    @property
    def has_receipt(self) -> bool:
        """Whether a nonzero amount resolves to source evidence.

        A calculated total is receipted only when each nonzero component is
        receipted. Zero is allowed without a receipt because it may represent
        a source-published "value not stated" state rather than spend.
        """
        if self.amount == Decimal("0"):
            return True
        if self.evidence:
            return True
        return bool(self.components) and all(
            money.has_receipt for _, money in self.components)

    def require_receipt(self) -> None:
        """Reject a nonzero client-facing amount without traceable proof."""
        if not self.has_receipt:
            raise EvidenceError(
                "a nonzero client-facing amount must carry source evidence "
                "or resolve through receipted components")

    def as_dict(self) -> dict:
        return {
            "display": self.display, "compact": self.compact,
            "amount": str(self.amount), "basis": self.basis,
            "population": self.population,
            "evidence": [r.as_dict() for r in self.evidence],
            "components": [{"label": lbl, **m.as_dict()}
                           for lbl, m in self.components],
        }


@dataclass(frozen=True)
class CoverageState:
    """What is known, what is missing, and what happens next.

    The client-facing `next_action` is forward language ("Direct phone
    enrichment next"), never commentary about the system ("validation
    remains pending because"). The internal fields keep the exact gap.
    """

    state: str
    known: tuple = ()
    missing: tuple = ()
    next_action: str = ""
    demand_kind: str = ""          # the typed research-demand key
    detail: str = ""               # internal only, never client-facing

    def __post_init__(self) -> None:
        if self.state not in COVERAGE_STATES:
            raise EvidenceError(
                f"unknown coverage state {self.state!r}; known are "
                f"{list(COVERAGE_STATES)}")
        if self.state != COMPLETE and not _clean(self.next_action):
            raise EvidenceError(
                "an incomplete coverage state must name the next action; "
                "absence is a work order, not an apology")

    @property
    def is_client_visible(self) -> bool:
        return True

    def as_dict(self) -> dict:
        return {"state": self.state, "known": list(self.known),
                "missing": list(self.missing),
                "next_action": self.next_action,
                "demand_kind": self.demand_kind}


def complete(*known: str) -> CoverageState:
    return CoverageState(state=COMPLETE, known=tuple(known))


def research_next(*, missing: Any, action: str, demand_kind: str,
                  known: Any = (), detail: str = "") -> CoverageState:
    return CoverageState(state=RESEARCH_NEXT, known=tuple(known),
                         missing=tuple(missing), next_action=action,
                         demand_kind=demand_kind, detail=detail)


def partial(*, known: Any, missing: Any, action: str,
            demand_kind: str, detail: str = "") -> CoverageState:
    return CoverageState(state=PARTIAL, known=tuple(known),
                         missing=tuple(missing), next_action=action,
                         demand_kind=demand_kind, detail=detail)
