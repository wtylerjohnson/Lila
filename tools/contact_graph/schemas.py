"""Schemas for the federal contact graph (phase one).

Two models, one hard rule.

  ContactObservation — an append-only FACT: "on this notice, this person was
      published with this channel at this time." Never mutated, never deleted.
      This is the on-disk source of truth (observations.jsonl).

  ContactProfile — a DERIVED view of one person, recomputed from their
      observations. Fully rebuildable from observations at any time; nothing on
      it is authoritative on its own.

The rule: **grades are never stored on an observation.** A channel's grade is a
function of *when* it was last seen in an official publication, evaluated at
query time (see grading.py), so it decays on its own as time passes. Observations
record only the dates; profiles carry the computed grades.

Two dates, kept separate on purpose:
  - observed_at  — when the channel was seen in the publication (the notice's
                   posted date). This is the GRADE BASIS. Backfilling a 2-year-old
                   notice today must grade it C, not A — so grades key off this,
                   never off the harvest time.
  - harvested_at — when LILA recorded the observation. Provenance/audit only.

Designed so later sources (Federal Register, SBIR, forecasts, GAO) append
observations of the same shape: `source` names the publication; every other
field is source-agnostic.
"""
from __future__ import annotations

from datetime import date, datetime
from typing import Literal, Optional

from pydantic import BaseModel, Field

# Grade is derived at query time, never persisted. See grading.py.
Grade = Literal["A", "B", "C"]
ChannelKind = Literal["email", "phone"]

# Where a channel was found on the notice. Structured POC fields outrank
# pattern-matched description text; downstream consumers weight accordingly.
ChannelSource = Literal["poc_field", "description_text"]

# Sentinel person_name for a channel found in description text with no clearly
# adjacent name. Never merged into a profile — routed to human review instead.
UNATTRIBUTED = "UNATTRIBUTED"


class ContactObservation(BaseModel):
    """One atomic, append-only sighting of a person + channel on one notice.

    Atomic per channel on purpose: a POC published with both an email and a phone
    yields TWO observations. That makes the dedupe key (source + notice_id +
    normalized person + channel) natural and makes per-channel grade decay clean.
    A POC with a name but no reachable channel still gets one observation
    (channel_kind=None) so the sighting counts toward rotation/first-seen signals.
    """

    # --- identity of the sighting (half of the dedupe key) ---
    source: str = Field(
        default="sam.gov",
        description="the official publication this was observed in (future: 'federal_register', etc.)",
    )
    notice_id: str = Field(description="publication record id, e.g. SAM noticeId")
    person_name: Optional[str] = Field(
        default=None,
        description="name exactly as published; None only for a channel-only stub",
    )

    # --- channel observed (the other half of the dedupe key) ---
    channel_kind: Optional[ChannelKind] = Field(
        default=None,
        description="'email' | 'phone'; None means a sighting with no reachable channel",
    )
    channel_value: Optional[str] = Field(
        default=None, description="the email or phone as published"
    )
    channel_source: ChannelSource = Field(
        default="poc_field",
        description="'poc_field' (structured API field) | 'description_text' (pattern-matched from body text — weight lower)",
    )

    # --- role, as the publication expresses it (two orthogonal axes) ---
    role_type: Optional[str] = Field(
        default=None, description="SAM POC 'type': 'primary' | 'secondary'"
    )
    title: Optional[str] = Field(
        default=None,
        description="job title; where 'Contracting Officer' / 'Contract Specialist' appears",
    )

    # --- where ---
    agency: Optional[str] = Field(
        default=None, description="top-level department; the entity-resolution scope"
    )
    office_path: Optional[str] = Field(
        default=None, description="full agency->office path as published (dot-delimited on SAM)"
    )

    # --- notice context ---
    notice_type: Optional[str] = Field(
        default=None, description="e.g. 'Solicitation' | 'Justification'"
    )
    naics: Optional[str] = None

    # --- solicitation links: BOTH forms, always, so any downstream artifact
    # links one click back to the live notice ---
    source_url: Optional[str] = Field(
        default=None, description="human-viewable sam.gov notice URL (uiLink)"
    )
    source_url_derived: bool = Field(
        default=False,
        description="True if source_url was derived from the notice ID, not read from cached data",
    )
    api_ref: Optional[str] = Field(
        default=None, description="API reference for the notice (noticedesc/search endpoint)"
    )
    api_ref_derived: bool = Field(
        default=False,
        description="True if api_ref was derived from the notice ID, not read from cached data",
    )

    # --- time (see module docstring: grade basis vs. provenance) ---
    observed_at: Optional[date] = Field(
        default=None,
        description="date the channel was seen in the publication (notice posted date) — the GRADE BASIS; None => oldest/unknown",
    )
    harvested_at: datetime = Field(
        description="when LILA recorded this observation (provenance/audit only)"
    )


class ChannelGrade(BaseModel):
    """A reachable channel on a derived profile, graded at query time."""

    kind: ChannelKind
    value: str
    grade: Grade = Field(
        description="A/B/C computed from last_observed at query time; never persisted as truth"
    )
    last_observed: Optional[date] = Field(
        default=None, description="most recent publication date this channel was seen (grade basis)"
    )
    source: str = Field(default="sam.gov")
    source_url: Optional[str] = None
    notice_id: Optional[str] = Field(
        default=None, description="the notice backing the most-recent sighting (audit trail)"
    )


class RotationHint(BaseModel):
    """Office-level rotation signal: this person's sightings appear to have stopped
    while a newer name became active at the same office. A queryable flag, not an
    alert system (spec item 7)."""

    office_path: Optional[str] = None
    last_seen: Optional[date] = None
    days_since_last_seen: Optional[int] = None
    likely_successor: Optional[str] = Field(
        default=None, description="a newer name now active at the same office"
    )


class ContactProfile(BaseModel):
    """Merged, derived view of one person. Rebuildable from observations."""

    person_name: str = Field(description="display name (most frequent published form)")
    normalized_name: str = Field(description="lowercased/de-punctuated key used for merging")
    agency: Optional[str] = Field(default=None, description="entity-resolution scope; never merged across agencies")

    offices: list[str] = Field(default_factory=list, description="office paths this person has been seen at")
    naics: list[str] = Field(default_factory=list)
    titles: list[str] = Field(default_factory=list)
    role_types: list[str] = Field(default_factory=list)

    sighting_count: int = Field(default=0, description="distinct notices this person appears on")
    notice_ids: list[str] = Field(default_factory=list)
    first_observed: Optional[date] = None
    last_observed: Optional[date] = None

    channels: list[ChannelGrade] = Field(
        default_factory=list, description="per-channel graded reachability"
    )
    rotation: Optional[RotationHint] = Field(
        default=None, description="office-level rotation signal; queryable flag, not an alert"
    )
