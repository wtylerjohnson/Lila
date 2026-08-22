"""Channel grading — computed from age, never stored as truth.

A grade answers one question: how fresh is the last official-publication sighting
of this channel? Because it is a pure function of observed_at evaluated against
*now*, it decays on its own as time passes — a channel graded A today slides to B
and then C with no write, no deletion. Nothing in the graph is ever downgraded by
mutation; it is downgraded by the calendar.

    A  last seen in an official publication within 90 days
    B  within 1 year
    C  older than a year (or no date on record)
"""
from __future__ import annotations

from datetime import date
from typing import Optional

from tools.contact_graph.schemas import Grade

GRADE_A_MAX_DAYS = 90
GRADE_B_MAX_DAYS = 365


def grade_for(observed_at: Optional[date], *, now: date) -> Grade:
    """A/B/C for a channel last observed on `observed_at`, evaluated at `now`.

    No date on record grades C (we cannot claim freshness we never observed).
    A future-dated observation grades A (age <= 90).
    """
    if observed_at is None:
        return "C"
    age_days = (now - observed_at).days
    if age_days <= GRADE_A_MAX_DAYS:
        return "A"
    if age_days <= GRADE_B_MAX_DAYS:
        return "B"
    return "C"
