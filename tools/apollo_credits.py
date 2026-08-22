"""Credit guardrails for every billable Apollo call.

WHY THIS MODULE EXISTS (measured 2026-08-06, not theorised). A phone reveal
was fired for two people because the tool that retrieves the result was
PRESENT in the caller's tool list. Present turned out not to mean
authorised: every retrieval attempt refused, and 16 direct-dial credits were
spent on numbers that could never be fetched. The lesson is not "document
the prerequisite". The lesson is that a billable call must not be reachable
until its own retrieval path has answered in the same run.

FOUR GUARANTEES, ALL FAIL-CLOSED:

1. RETRIEVAL PROOF. An async billable call raises unless a ZERO-COST probe
   of its own result path succeeded in this run. Not a flag, not a config
   value, not the presence of a function: an actual answer, this run.
2. PRE-FLIGHT ESTIMATE. Nothing bills until the projected spend has been
   computed and printed: people, credit class, per-unit, projected total,
   balance now, balance after.
3. A CAP THAT REFUSES. A run whose projection exceeds
   LILA_APOLLO_CREDIT_CAP does not start. The default is deliberately low;
   raising it is an explicit operator act.
4. MEASURED DELTA. Balance is read before and after and the difference is
   printed and written to the receipt beside the call count that produced
   it, so a future burn is visible in the artifact rather than in a
   surprise invoice.

THE RATIO THAT DECIDES THE DESIGN, measured on this account: a work-email
match is ~1 credit and synchronous; a direct-dial reveal is ~8 credits and
async. Eight times the price for the harder-to-retrieve half is why the
email path ships first and the dial path stays gated.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Any, Callable, Optional

# Credit classes, and the per-record cost measured on this account on
# 2026-08-06. These are OBSERVED figures, not published rates: two records
# matched cost 2 credits, and two phone reveals cost 16 direct-dial credits.
CREDIT_CLASSES = {
    "match": {"per_record": 1, "pool": "credits",
              "basis": "measured 2026-08-06: 2 records matched, 2 credits"},
    "direct_dial": {"per_record": 8, "pool": "direct_dial_credits",
                    "basis": "measured 2026-08-06: 2 reveals, 16 dial credits"},
}

CREDIT_CAP_ENV = "LILA_APOLLO_CREDIT_CAP"
# OPERATOR RULING 2026-08-06: a pass covers EVERY buying account, always.
# Coverage is never trimmed to fit a budget; the only thing that makes a
# plan excessive is its credit cost, and that gets ALERTED, not silently
# avoided. Measured at book scale: one client's full pass at five people
# per account runs 45 to 360 credits, and the entire seven-client book runs
# 900. The old ceiling of 25 would have refused every real pass, which is
# how a cap turns into an invisible coverage limit.
#
# 500 admits any single-client full pass and still stops a runaway. A
# whole-book run is a deliberate raise, made in the environment.
DEFAULT_CREDIT_CAP = 500

# Alert, do not refuse. A plan costing more than this share of the remaining
# balance prints loudly so the operator sees the number before it is spent.
ALERT_BALANCE_FRACTION = 0.10


class CreditRefusal(RuntimeError):
    """A billable call was refused before it could spend anything."""


class RetrievalUnproven(CreditRefusal):
    """The result path for a billable async call has not answered this run."""


def credit_cap() -> int:
    raw = os.environ.get(CREDIT_CAP_ENV, "").strip()
    if not raw:
        return DEFAULT_CREDIT_CAP
    try:
        value = int(raw)
    except ValueError:
        raise CreditRefusal(
            f"{CREDIT_CAP_ENV}={raw!r} is not an integer; refusing rather "
            f"than guessing a spending ceiling")
    if value < 0:
        raise CreditRefusal(f"{CREDIT_CAP_ENV} may not be negative")
    return value


# --------------------------------------------------------------------------- #
# 1 · retrieval proof
# --------------------------------------------------------------------------- #
@dataclass
class RetrievalProof:
    """Which result paths have actually answered, in THIS run.

    ``prove`` runs a caller-supplied ZERO-COST probe and records the result.
    ``require`` raises unless that probe already succeeded. There is
    deliberately no way to mark a path proven without running a probe: an
    "assume it works" switch is the exact failure this class exists to make
    impossible.
    """

    proven: dict = field(default_factory=dict)

    def prove(self, path: str, probe: Callable[[], Any]) -> bool:
        """Run the zero-cost probe for ``path``. True when it answered.

        A probe that raises is a FAILED proof, never a crash: the caller
        then refuses the billable call with a named reason.
        """
        try:
            probe()
        except Exception as exc:  # noqa: BLE001 - a failed probe is a result
            self.proven[path] = f"{type(exc).__name__}: {exc}"[:200]
            return False
        self.proven[path] = True
        return True

    def is_proven(self, path: str) -> bool:
        return self.proven.get(path) is True

    def require(self, path: str, *, spend_note: str = "") -> None:
        if self.is_proven(path):
            return
        why = self.proven.get(path)
        raise RetrievalUnproven(
            f"refusing a billable call: the result path {path!r} has not "
            f"answered in this run"
            + (f" ({why})" if isinstance(why, str) else
               " (no zero-cost probe was attempted)")
            + ". On 2026-08-06 this exact situation spent 16 direct-dial "
              "credits on numbers that could not be retrieved"
            + (f". {spend_note}" if spend_note else ""))


# --------------------------------------------------------------------------- #
# 2 · pre-flight estimate and 3 · the cap
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class CostEstimate:
    people: int
    calls: int
    credit_class: str
    per_record: int
    projected_total: int
    pool: str
    basis: str
    balance_before: Optional[int]
    projected_remaining: Optional[int]
    cap: int

    def over_cap(self) -> bool:
        return self.projected_total > self.cap

    def alert(self) -> Optional[str]:
        """The ALERT the operator asked for: a plan that is large relative to
        the remaining balance says so in numbers, and still runs.

        Coverage is never trimmed to silence this. The alert exists so the
        spend is seen, not so the pass is shortened.
        """
        if self.balance_before is None or self.balance_before <= 0:
            return None
        share = self.projected_total / float(self.balance_before)
        if share < ALERT_BALANCE_FRACTION:
            return None
        return (f"this plan spends {self.projected_total} of "
                f"{self.balance_before} credits ({share * 100:.0f} percent of "
                f"the remaining balance) across {self.people} person(s). "
                f"Coverage is not being reduced; the number is stated so the "
                f"decision is yours.")


def estimate(people: int, *, calls: int, credit_class: str,
             balance_before: Optional[int] = None) -> CostEstimate:
    if credit_class not in CREDIT_CLASSES:
        raise CreditRefusal(
            f"unknown credit class {credit_class!r}; refusing rather than "
            f"billing against a cost this module cannot price")
    spec = CREDIT_CLASSES[credit_class]
    total = int(people) * int(spec["per_record"])
    remaining = (None if balance_before is None
                 else int(balance_before) - total)
    return CostEstimate(
        people=int(people), calls=int(calls), credit_class=credit_class,
        per_record=int(spec["per_record"]), projected_total=total,
        pool=str(spec["pool"]), basis=str(spec["basis"]),
        balance_before=balance_before, projected_remaining=remaining,
        cap=credit_cap())


def render_estimate(est: CostEstimate) -> str:
    """The pre-flight block, printed before anything bills."""
    lines = [
        "[apollo] PRE-FLIGHT COST ESTIMATE",
        f"  people to enrich   : {est.people}",
        f"  calls              : {est.calls}",
        f"  credit class       : {est.credit_class} "
        f"({est.per_record}/record, pool {est.pool})",
        f"  basis              : {est.basis}",
        f"  projected total    : {est.projected_total} credit(s)",
        "  balance before     : "
        + ("unknown" if est.balance_before is None else str(est.balance_before)),
        "  projected remaining: "
        + ("unknown" if est.projected_remaining is None
           else str(est.projected_remaining)),
        f"  cap ({CREDIT_CAP_ENV}) : {est.cap}",
    ]
    warning = est.alert()
    if warning:
        lines += ["", "  *** CREDIT ALERT ***", "  " + warning]
    return "\n".join(lines)


def enforce_cap(est: CostEstimate) -> None:
    if est.over_cap():
        raise CreditRefusal(
            f"projected spend {est.projected_total} credit(s) exceeds the "
            f"{est.cap}-credit cap ({CREDIT_CAP_ENV}). Nothing was called. "
            f"Raise the cap deliberately or narrow the run.")
    if (est.projected_remaining is not None
            and est.projected_remaining < 0):
        raise CreditRefusal(
            f"projected spend {est.projected_total} credit(s) exceeds the "
            f"balance of {est.balance_before}. Nothing was called.")


# --------------------------------------------------------------------------- #
# 4 · the measured delta
# --------------------------------------------------------------------------- #
@dataclass
class SpendRecord:
    balance_before: Optional[int] = None
    balance_after: Optional[int] = None
    calls_made: int = 0
    estimate: Optional[CostEstimate] = None

    @property
    def delta(self) -> Optional[int]:
        if self.balance_before is None or self.balance_after is None:
            return None
        return self.balance_before - self.balance_after

    def receipt(self) -> dict:
        """The spend block the store receipt carries. A None balance is
        recorded as None and never as zero: unknown is not free."""
        projected = None if self.estimate is None else \
            self.estimate.projected_total
        return {
            "balance_before": self.balance_before,
            "balance_after": self.balance_after,
            "credits_spent": self.delta,
            "credits_projected": projected,
            "estimate_accurate": (None if self.delta is None
                                  or projected is None
                                  else self.delta == projected),
            "calls_made": self.calls_made,
            "cap": None if self.estimate is None else self.estimate.cap,
        }

    def render(self) -> str:
        spent = "unknown" if self.delta is None else str(self.delta)
        projected = ("unknown" if self.estimate is None
                     else str(self.estimate.projected_total))
        return (f"[apollo] SPEND: {spent} credit(s) actually consumed across "
                f"{self.calls_made} call(s), against a projected "
                f"{projected}; balance "
                f"{self.balance_before} -> {self.balance_after}")
