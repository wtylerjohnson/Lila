# Industry-day lane: wiring handoff

Written 2026-07-30. Self-contained: implement from this without re-deriving.

**Repo:** `/Users/wtjohnson/federal-sales-os` (branch `fable/golden-build`)
**Python:** `/Users/wtjohnson/federal-sales-os/.venv/bin/python`
**Tests:** `PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m pytest tests/ -q -p no:cacheprovider`
**Baseline: 3,512 passed, 1 skipped.**
**Press (the ONLY sanctioned path):** Control Room on 8321, then
`POST /api/run {"client_name":"Riverbed","step":"candidate_review"}`, poll `/api/job/<id>`.

---

## 1. The situation

`agents/golden_press/industry_days.py` exists (8.4 KB, 2026-07-28) and **nothing
imports it**. Verified: zero references across `agents/`, `tools/`, `ui/`,
`run_*.py`. It has never produced a row. The delivered Riverbed report contains
0 mentions of "industry day" and the pack carries `events: 0`.

The press already has a *different* events lane (`press.py`, the
`EVENTS_LANE (2026-07-27)` block, ~line 289): it harvests tier A from the daily
SAM extract via `events.harvest_tier_a` -> `events.verify_urls`, then sets
`pack.events` and `pack.events_screen`. On the 2026-07-30 Riverbed press it
logged `0 event-language notices screened, 0 verified events kept`.

So industry days are a SECOND source that must land in the SAME `pack.events`
list, in the SAME shape, or not at all.

## 2. The shape gap (the whole job)

`industry_days.collect(rows, *, today=None) -> {"rows": [...], "receipt": {...}}`

Each emitted row (industry_days.py:158-168):

    notice_id, title, notice_type, agency, sub_agency, office,
    registration_deadline, candidate_event_date

The consumer needs `events.EventRecord` (events.py:138-170). Required/relevant:

    event_id      (str, required)      name (str, required)
    event_type    (str)                source_system (str) - "sam_extract" | "agency_osdbu" | "curated"
    url           (str, required)      url_verified (bool), url_status (int|None)
    host          (str)                host_office, location, is_virtual
    event_start   (str|None ISO)       event_end, event_date_span
    registration_deadline (str|None)   registration_deadline_source, registration_closed
    relevance     (str)                relevance_basis (dict)
    matched_phrase, notice_type, naics, posted_date, retrieved_at

Mapping that is NOT free:
- **`url`** - industry_days emits no URL. Synthesize the canonical
  `https://sam.gov/opp/<notice_id>/view`. This is the same canonical form the
  L1 lane uses (`retrieval._notice_url`); reuse it rather than formatting a
  second time.
- **`host`** - from `agency` / `sub_agency` / `office`.
- **`event_start`** - from `candidate_event_date` (see its parser at
  industry_days.py:94). It returns a dict, NOT a string; unwrap to an ISO day
  or leave `None`.
- **`relevance`** - must be one line bound to a pack fact. Do not invent one.
- **`event_id`**, `source_system="sam_extract"`, `retrieved_at`.

## 3. The four rules that will bite you

1. **DATE PROVENANCE.** `validate._pack_date_keys` ingests event
   `event_start`, `event_end`, `registration_deadline`, `posted_date`
   (validate.py, the `EVENTS_LANE (2026-07-27)` block). A date RENDERED in the
   events band that is not on the event record is `date_without_provenance`.
   Any date you synthesize must live on the record you put in `pack.events`.
2. **A malformed events row yields an UNCERTIFIED press.** `golden_press`
   ships the report anyway with a `.FAILED.html` copy and
   `certified=False`, and `_deliver_to_desktop` then parks it as
   `... · DRAFT n.html` instead of the clean name. Do not half-wire this.
3. **NO-DATE RULE.** When `event_start` is null the date block renders the
   single token `TBD` and nothing else (stated in `_COMPOSE_SYSTEM`). Never
   approximate an event date.
4. **URL VERIFICATION.** The existing lane verifies every URL live and
   *skips + discloses* failures rather than approximating. Industry-day rows
   must go through the same `events.verify_urls` so `url_verified` /
   `url_status` are real. A synthesized SAM URL is a CANDIDATE, not a
   verified one.

## 4. Where it plugs in

`agents/golden_press/press.py`, inside the existing `EVENTS_LANE` try-block
(~line 289-310), AFTER tier A:

```python
from agents.golden_press import industry_days
day_rows = industry_days.collect(extract_rows)          # same rows tier A read
mapped = [_industry_day_event(r) for r in day_rows["rows"]]
verified_days, skipped_days = verify_urls(mapped)
pack.events = [json.loads(e.model_dump_json())
               for e in (*verified, *verified_days)]
pack.events_screen["industry_days"] = {**day_rows["receipt"],
                                       "verified": len(verified_days),
                                       "skipped": skipped_days}
```

Notes:
- `industry_days.collect` reads sqlite3.Row OR dict (it probes `.keys()`), so
  the extract rows tier A already loaded can be passed straight in.
- Dedupe against tier A by `notice_id` before extending, or the same notice
  arrives twice and trips `dock_record_count` / `repeated_prose`.
- `add_band_nav(skeleton, with_events=bool(pack.events))` (press.py:166)
  already switches the events band on; nothing else to toggle.
- The band renders at `render.py:932-964` and already reads `event_start`,
  `registration_deadline`, `registration_closed`, `host`, `url`, `event_id`,
  and feeds the calendar. No renderer change should be needed.

## 5. Tests to write (`tests/test_industry_days_wiring.py`)

1. Mapping: a `collect()` row becomes a valid `EventRecord` (model validates).
2. Canonical URL: `notice_id` -> `https://sam.gov/opp/<id>/view`, matching
   `retrieval._notice_url`.
3. No-date: a row whose `candidate_event_date` is None yields
   `event_start=None` and never a synthesized day.
4. Date provenance: every date on the mapped record appears in
   `validate._pack_date_keys(pack)` once the record is in `pack.events`.
5. Dedupe: a notice present in BOTH tier A and industry days appears once.
6. Unverified URL is skipped and disclosed, never shipped as verified.
7. Empty case: no industry-day rows leaves `pack.events` and the band exactly
   as they are today (no empty band, no regression).
8. End-to-end: press with a stubbed extract, assert `certified=True` and the
   events band renders with the row.

## 6. Definition of done

- Full suite green (baseline 3,512).
- A press via the Command Center returns `validator ok: True, violations: 0`
  and delivers to `~/Desktop/<Client>/` under the CLEAN name (no `DRAFT n`).
- `pack.events_screen` carries an industry-day receipt with real counts, so an
  empty band states what it screened instead of reading as a hole.

## 7. Context worth carrying

- Prose may now cite pack figures in its own voice; the gate is
  `compose._prose_usable(text, pack)` reusing the validator's own helpers. If
  prose behaviour ever refuses to change, check `_PROSE_SYSTEM` /
  `_COMPOSE_SYSTEM` for a conflicting standing rule FIRST - a system-prompt
  rule outranks slot-level instructions and cost two presses on 2026-07-30.
- Riverbed still returns ZERO sam.gov solicitations because its approved
  capability terms are vendor marketing. That is an Analyst Layer decision and
  operator-owned. See `docs/reviews/` and the term-logic measurements: the
  headline "0 -> 9" is really 0 -> ~3 genuine without a title-scope guard.
