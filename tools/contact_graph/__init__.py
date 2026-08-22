"""Federal contact graph (phase one).

An append-only, source-verified store of contacts observed on official federal
publications — starting with SAM.gov notices — with grade decay and historical
backfill. Contacts are network assets shared across clients, so the store lives
at data/state/contact_graph/, not under any per-client path (TODO: migrate to
the _shared area of the workspace once the LILA_WORKSPACE feature exists).

Public surface:
    schemas   — ContactObservation (fact) / ContactProfile (derived view)
    extract   — observations_from_notice(notice)
    store     — ContactGraphStore (append-only observations + derived index)
    grading   — grade_for(observed_at, now)
    resolve   — derive_profiles(observations, now), rebuild(store, now)
    query     — query(store, ...), format_profile(profile)
"""
from __future__ import annotations

from tools.contact_graph.extract import observations_from_notice
from tools.contact_graph.grading import grade_for
from tools.contact_graph.query import format_profile, query
from tools.contact_graph.resolve import derive_profiles, rebuild
from tools.contact_graph.schemas import (
    UNATTRIBUTED,
    ChannelGrade,
    ChannelKind,
    ChannelSource,
    ContactObservation,
    ContactProfile,
    Grade,
    RotationHint,
)
from tools.contact_graph.store import ContactGraphStore

__all__ = [
    "ContactObservation",
    "ContactProfile",
    "ChannelGrade",
    "RotationHint",
    "Grade",
    "ChannelKind",
    "ChannelSource",
    "UNATTRIBUTED",
    "observations_from_notice",
    "ContactGraphStore",
    "grade_for",
    "derive_profiles",
    "rebuild",
    "query",
    "format_profile",
]
