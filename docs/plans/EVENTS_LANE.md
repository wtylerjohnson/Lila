# EVENTS AND INDUSTRY DAYS — new lane, two surfaces. Frozen scope.

Same laws as everything else: linkage, provenance, never pad, verified
live or skipped.

WHY IT EXISTS: clients ask "are there events or conferences we should
be attending." The answer must be specific to their book, not a
generic industry calendar.

RECORDS: event name, start/end date, REGISTRATION DEADLINE as its own
date, host agency or organization, location or virtual, registration
or notice URL, source system, type (government-hosted / industry
conference / client-hosted), and a one-line relevance rationale tied
to something in this client's pack (an agency they are funded in, a
corridor, a competitor position, a forecast).

SOURCES, tier A first, ship A alone if time runs out:
A. SAM.gov special notices and pre-solicitation carrying event
   language (industry day, sources sought session, pre-proposal
   conference). Structured, canonical URL, already an API you call.
   Screen against client keywords, NAICS, and the agencies present in
   the pack.
B. Agency small-business / OSDBU event calendars — only for agencies
   that actually appear in client packs. Build the handful that
   recur, not fifty.
C. Curated annual conference map (AFCEA, ACT-IAC, NDIA and peers) plus
   the client's own event calendar from the research stage. Static map
   in the repo. EVERY entry verified live before it ships; anything
   that fails verification is skipped and reported, never
   approximated. Re-verify quarterly.

REPORT SURFACE: new band "Events and industry days", placed after the
Federal opportunity calendar. Each event is a row: date block, name
linked to its registration URL, host, location, registration deadline
if one exists, and the relevance line. Event rows ALSO appear in the
calendar band tagged EVENT, visually distinct from deadlines, clocks
and forecast windows.

COMMAND CENTER SURFACE — adaptive calendar, this is the design rule:
- Default view is an AGENDA grouped by month, month headers, rows in
  date order. This is the view that works when events are months
  apart.
- Above it, a DENSITY RIBBON: one cell per month across the horizon,
  each showing a count and a dot cluster. Click a month to jump.
  Sparse months stay visible and empty rather than being hidden, so
  the spread reads as coverage, not as gaps.
- A month renders as a MINI GRID only when it holds 4 or more items.
  Below that threshold the grid is worse than the list; do not render
  it.
- Toggle between agenda and grid is available but agenda is default.
- Registration deadlines render as their own rows in amber, distinct
  from the event date itself. An event whose registration closes
  before the client would see it is flagged, not hidden.
- Every row links out. Rows inherit the harvested agency seal where
  one exists, monogram otherwise.

LAWS: an event with no verifiable URL does not ship. The composer may
never invent, infer, or approximate an event, date, or location.
Validator extends date provenance to event and registration dates from
the pack. Events never count toward the evidence sufficiency gate.

ACCEPTANCE: run against the existing Red Hat pack. Deliver the event
list with host, both dates, URL and relevance per row, the raw queries,
and candidates screened versus kept. Every URL verified live, zero
invented. Then press one report end to end with the lane active and
confirm the validator stays clean with event rows present.

TIMEBOX: two days. Cut order: tier C, then tier B. Tier A ships alone.

---

## BUILD NOTE (2026-07-27): tier A costs ZERO SAM quota

The metered opportunities API is capped near 10 calls/key/day without a
role. The daily Contract Opportunities extract carries the whole active
universe (80,579 notices) WITH full description text, including 5,957
Special Notices and 7,938 Presolicitations. Tier A therefore harvests
from the extract, not the metered API: complete coverage, canonical
`Link` per notice, zero quota. The metered API stays reserved for
same-day verification of the handful that reach the deliverable.
