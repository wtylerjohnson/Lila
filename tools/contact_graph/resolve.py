"""Entity resolution and profile derivation.

Merge rule (conservative by design):
  - Group observations by (normalized name, agency). Each group is one person.
  - NEVER merge across agencies — a name at the VA and the same name at DoD are
    two profiles until a human says otherwise.
  - NEVER auto-merge near-matches. "Jane A. Doe" and "Jane Doe" in the same
    agency are kept as separate profiles and written to a review file as an
    *ambiguous candidate* — the graph guesses nothing.

Everything here is derived: given the observations, `derive_profiles` reproduces
the exact same profiles, grades (for a given `now`), rotation hints, and review
queue. That is what makes the index rebuildable.
"""
from __future__ import annotations

from collections import Counter, defaultdict
from datetime import date
from typing import Iterable, Optional

from tools.contact_graph.grading import grade_for
from tools.contact_graph.names import (
    core_key,
    normalize_name,
    plausible_phone,
    split_name_and_phone,
)
from tools.contact_graph.schemas import (
    UNATTRIBUTED,
    ChannelGrade,
    ContactObservation,
    ContactProfile,
    RotationHint,
)

# A person whose last sighting at an office is older than this, while a new name
# has since appeared there, is flagged as possibly rotated out. A loose hint.
ROTATION_STALE_DAYS = 365


def _agency_key(agency: Optional[str]) -> str:
    return (agency or "").strip().lower()


def _distinct(values: Iterable[Optional[str]]) -> list[str]:
    """Order-preserving distinct, dropping None/empty."""
    seen: dict[str, None] = {}
    for v in values:
        if v:
            seen.setdefault(v, None)
    return list(seen)


def _newer(a: Optional[date], b: Optional[date]) -> bool:
    """Is `a` a more recent observation than `b`? None ranks oldest."""
    if a is None:
        return False
    if b is None:
        return True
    return a > b


def _merge_parent(decisions: list[dict]) -> dict[tuple[str, str], tuple[str, str]]:
    """Union-find parent map over (normalized_name, agency_key) built from
    APPROVED human decisions. Approval unions names within one agency only —
    the cross-agency guard survives even a malformed decision record."""
    parent: dict[tuple[str, str], tuple[str, str]] = {}

    def find(k):
        while parent.get(k, k) != k:
            parent[k] = parent.get(parent[k], parent[k])
            k = parent[k]
        return k

    for d in decisions:
        if d.get("action") != "approve":
            continue
        ak = (d.get("agency") or "").strip().lower()
        names = sorted(normalize_name(n) for n in d.get("names") or [] if n)
        keys = [(n, ak) for n in names if n]
        for k in keys[1:]:
            ra, rb = find(keys[0]), find(k)
            if ra != rb:
                parent[rb] = ra
    return {k: find(k) for k in list(parent)}


def _decided_sets(decisions: list[dict]) -> set[tuple[str, frozenset]]:
    """(agency_key, {normalized names}) for every human-decided candidate —
    approved or rejected, it leaves the review queue either way."""
    out = set()
    for d in decisions:
        ak = (d.get("agency") or "").strip().lower()
        names = frozenset(normalize_name(n) for n in d.get("names") or [] if n)
        if names:
            out.add((ak, names))
    return out


def derive_profiles(
    observations: Iterable[ContactObservation], *, now: date,
    decisions: Optional[list[dict]] = None,
) -> tuple[list[ContactProfile], list[dict]]:
    """Return (profiles, ambiguous-merge review candidates).

    `decisions` are human merge calls (store.read_decisions()): approved pairs
    merge into one profile; decided candidates (either way) leave the queue."""
    observations = list(observations)
    decisions = decisions or []
    merge_to = _merge_parent(decisions)

    groups: dict[tuple[str, str], list[ContactObservation]] = defaultdict(list)
    unattributed: list[ContactObservation] = []
    cleaned_observations: list[ContactObservation] = []
    for o in observations:
        if o.person_name == UNATTRIBUTED:
            unattributed.append(o)  # human review, never a profile
            cleaned_observations.append(o)
            continue
        # POC-field hygiene (2026-07-12): phone digits bled into the name
        # split back into a channel with the sighting's own provenance; a
        # label-only row ("Telephone: ...") carries no person and can never
        # mint a profile. The stored observation is never rewritten.
        clean_name, salvaged_phone = split_name_and_phone(o.person_name)
        nn = normalize_name(clean_name)
        if not nn:
            continue  # a nameless channel stub can't be attributed to a person
        key = (nn, _agency_key(o.agency))
        key = merge_to.get(key, key)
        cleaned = (o if clean_name == o.person_name
                   else o.model_copy(update={"person_name": clean_name}))
        groups[key].append(cleaned)
        cleaned_observations.append(cleaned)
        if salvaged_phone and plausible_phone(salvaged_phone):
            groups[key].append(cleaned.model_copy(update={
                "channel_kind": "phone",
                "channel_value": salvaged_phone,
            }))

    profiles_by_key: dict[tuple[str, str], ContactProfile] = {}
    for (nn, ak), obs in groups.items():
        profiles_by_key[(nn, ak)] = _build_profile(nn, obs, now=now)

    _annotate_rotation(profiles_by_key, cleaned_observations, now=now,
                       merge_to=merge_to)

    profiles = list(profiles_by_key.values())
    decided = _decided_sets(decisions)
    review = [
        c for c in _review_candidates(profiles)
        if ((c.get("agency") or "").strip().lower(),
            frozenset(x["normalized_name"] for x in c["candidates"])) not in decided
    ] + _unattributed_review(unattributed)
    return profiles, review


def _build_profile(
    normalized_name: str, obs: list[ContactObservation], *, now: date
) -> ContactProfile:
    names = [o.person_name for o in obs if o.person_name]
    display = Counter(names).most_common(1)[0][0] if names else normalized_name
    agency = next((o.agency for o in obs if o.agency), None)
    notice_ids = _distinct(o.notice_id for o in obs)
    dates = [o.observed_at for o in obs if o.observed_at]

    return ContactProfile(
        person_name=display,
        normalized_name=normalized_name,
        agency=agency,
        offices=_distinct(o.office_path for o in obs),
        naics=_distinct(o.naics for o in obs),
        titles=_distinct(o.title for o in obs),
        role_types=_distinct(o.role_type for o in obs),
        sighting_count=len(notice_ids),
        notice_ids=notice_ids,
        first_observed=min(dates) if dates else None,
        last_observed=max(dates) if dates else None,
        channels=_grade_channels(obs, now=now),
    )


def _grade_channels(obs: list[ContactObservation], *, now: date) -> list[ChannelGrade]:
    """One ChannelGrade per distinct (kind, value), graded on its most recent
    sighting."""
    best: dict[tuple[str, str], ContactObservation] = {}
    for o in obs:
        if not o.channel_kind or not o.channel_value:
            continue
        if o.channel_kind == "phone" and not plausible_phone(o.channel_value):
            # switchboard placeholders ("0000000000") are published noise;
            # they never become a graded, reachable-looking channel
            continue
        key = (o.channel_kind, o.channel_value.strip().lower())
        cur = best.get(key)
        if cur is None or _newer(o.observed_at, cur.observed_at):
            best[key] = o

    channels = [
        ChannelGrade(
            kind=o.channel_kind,  # type: ignore[arg-type]
            value=o.channel_value,  # type: ignore[arg-type]
            grade=grade_for(o.observed_at, now=now),
            last_observed=o.observed_at,
            source=o.source,
            source_url=o.source_url,
            notice_id=o.notice_id,
        )
        for o in best.values()
    ]
    # deterministic: email before phone, freshest grade first, then value
    channels.sort(key=lambda c: (c.kind, c.grade, c.value))
    return channels


def _annotate_rotation(
    profiles_by_key: dict[tuple[str, str], ContactProfile],
    observations: list[ContactObservation],
    *,
    now: date,
    merge_to: Optional[dict] = None,
) -> None:
    """Flag office-level rotation: a person's sightings stop while a new name
    starts at the same office (spec item 7). A queryable hint, not an alert."""
    # office -> normalized_name -> {first, last, agency}
    office_people: dict[str, dict[str, dict]] = defaultdict(
        lambda: defaultdict(lambda: {"first": None, "last": None, "agency": None})
    )
    for o in observations:
        nn = normalize_name(o.person_name)
        if not nn or not o.office_path or not o.observed_at:
            continue
        rec = office_people[o.office_path][nn]
        if rec["first"] is None or o.observed_at < rec["first"]:
            rec["first"] = o.observed_at
        if rec["last"] is None or o.observed_at > rec["last"]:
            rec["last"] = o.observed_at
        rec["agency"] = o.agency

    for office, people in office_people.items():
        if len(people) < 2:
            continue
        for nn, rec in people.items():
            if rec["last"] is None or (now - rec["last"]).days < ROTATION_STALE_DAYS:
                continue
            # someone who first appeared AFTER this person went quiet
            successors = [
                (other["first"], other_nn, other)
                for other_nn, other in people.items()
                if other_nn != nn and other["first"] and other["first"] > rec["last"]
            ]
            if not successors:
                continue
            successors.sort(key=lambda t: t[0])  # immediate successor
            _, succ_nn, succ_rec = successors[0]

            merge_to = merge_to or {}
            key = (nn, _agency_key(rec["agency"]))
            prof = profiles_by_key.get(merge_to.get(key, key))
            if prof is None:
                continue
            skey = (succ_nn, _agency_key(succ_rec["agency"]))
            succ_prof = profiles_by_key.get(merge_to.get(skey, skey))
            succ_display = succ_prof.person_name if succ_prof else succ_nn

            hint = RotationHint(
                office_path=office,
                last_seen=rec["last"],
                days_since_last_seen=(now - rec["last"]).days,
                likely_successor=succ_display,
            )
            # if flagged at more than one office, keep the most recent one
            if prof.rotation is None or _newer(hint.last_seen, prof.rotation.last_seen):
                prof.rotation = hint


def _review_candidates(profiles: list[ContactProfile]) -> list[dict]:
    """Near-matches within one agency: same first+last name, differing full name.
    Flagged for a human, never merged."""
    by_agency: dict[str, list[ContactProfile]] = defaultdict(list)
    for p in profiles:
        by_agency[_agency_key(p.agency)].append(p)

    out: list[dict] = []
    for _ak, plist in by_agency.items():
        buckets: dict[tuple[str, str], list[ContactProfile]] = defaultdict(list)
        for p in plist:
            ck = core_key(p.person_name) or core_key(p.normalized_name)
            if ck:
                buckets[ck].append(p)
        for ck, group in buckets.items():
            if len({p.normalized_name for p in group}) < 2:
                continue  # identical normalized names already merged — not ambiguous
            out.append(
                {
                    "reason": "same first+last name in same agency, differing full name — possible same person",
                    "agency": group[0].agency,
                    "core_key": list(ck),
                    "candidates": [
                        {
                            "person_name": p.person_name,
                            "normalized_name": p.normalized_name,
                            "offices": p.offices,
                            "notice_ids": p.notice_ids,
                            "sighting_count": p.sighting_count,
                        }
                        for p in group
                    ],
                }
            )
    return out


def _unattributed_review(observations: list[ContactObservation]) -> list[dict]:
    """Channels found in description text with no clearly adjacent name. A human
    decides who they belong to; the graph records the sighting, not a guess."""
    return [
        {
            "reason": "channel found in description text with no clearly adjacent name",
            "agency": o.agency,
            "office_path": o.office_path,
            "notice_id": o.notice_id,
            "channel": {"kind": o.channel_kind, "value": o.channel_value},
            "source_url": o.source_url,
            "observed_at": o.observed_at,
        }
        for o in observations
    ]


def rebuild(store, *, now: date) -> tuple[list[ContactProfile], list[dict]]:
    """Recompute the derived index + review queue from observations on disk.

    Fully rebuildable: reads observations.jsonl, derives everything, overwrites
    index.json and merge_review.jsonl. Grades in the written index are a snapshot
    for `now`; the query layer recomputes from observations so answers never go
    stale (grades are never trusted as stored truth).
    """
    obs = store.read_observations()
    profiles, review = derive_profiles(obs, now=now, decisions=store.read_decisions())
    store.write_index(
        {
            "generated_for": now,
            "observation_count": len(obs),
            "profile_count": len(profiles),
            "profiles": [p.model_dump() for p in profiles],
        }
    )
    store.write_review(review)
    return profiles, review
