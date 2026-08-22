# Codex review — NAICS Boundary Workshop

**Commit:** `22a72846e85357756836b5fa24d4c3c3629d3a86`

**Parent:** `f61cd6d25a530b69921df2ce7df1a0931ba9b2df`

**Disposition:** **HOLD — repair before building further on this contract.**

**Review mode:** independent, read-only; no gate, pointer, press, approval, scope, or metered action.

## Findings

### P1 — an empty boundary cannot be restored into the search plan

`agents/review.py:516-541` reconciles a search spec only when its *previous*
`naics_codes` value is truthy. Saving an empty boundary correctly empties the
SAM.gov and USAspending specs; adding a code on the next revision then skips
both specs because their lists are now empty. The authoritative
`inferred_naics` and the searches that actually run are left in contradiction.

Reproduced directly:

- initial: SAM/USAspending `['541512']`
- save empty boundary: both become `[]`
- restore `541519`: both remain `[]`

The new reconciliation test covers only nonempty-to-nonempty replacement. Key
the reconciliation to source identity (or another stable NAICS-bearing marker),
then add empty-to-restored coverage and a nonempty-web-spec defense.

### P1 — “Inspect first” adds the code to the authoritative boundary

`ui/index.html:2020-2027` calls `promoteNaicsCandidate()` before opening the
inspector. That makes “Inspect first” semantically identical to “Use in
boundary,” despite the adjacent action and the promise that reconsideration
never adds a code automatically.

Live NETSCOUT proof: clicking Inspect first on `541513` increased the boundary
from 5 cards to 6 and removed `541513` from the judgment lane. The mutation was
not saved during review. Inspection needs a read-only candidate selection state;
only “Use in boundary” may promote.

### P1 — the audit/non-destructive decision contract is not enforced by the persistence owner

Three related gaps make the new decision record lossy or contradictory:

1. `agents/review.py:354-358`, `:612-616`, and `:693-697` omit `naics_meta`
   and `kept_out_naics` from journal before/after snapshots. A role, rationale,
   provenance, title, note, or move-out-reason edit records only a touched field,
   not the decision. This contradicts the UI statement that every change is
   journaled and prevents debrief reconstruction.
2. `agents/review.py:260-268` deduplicates `kept_out_naics` but never enforces
   disjointness from `inferred_naics`. The API can persist the same code in “in”
   and “kept out” simultaneously. Inferred-only removal also prunes metadata
   without transactionally preserving it in kept-out; inferred-only restoration
   does not recover metadata. The advertised non-destructive invariant currently
   depends on a perfect browser payload rather than the persistence owner.
3. `agents/review.py:225-230` silently drops malformed `naics_meta` and
   `kept_out_naics` codes while returning success and incrementing the revision.
   `_validate_naics_codes` covers only `inferred_naics`. `NaicsEntry` itself
   declares code/role/origin constraints only in descriptions, so invalid initial
   records also validate. An attempted operator decision must be rejected loudly,
   never erased under a successful save.

Make a single reconciliation/validation owner enforce six digits, enum values,
dedupe, boundary/kept-out disjointness, and transactional move/restore semantics;
include the full NAICS decision state in both journal paths.

### P1 — keyboard removal is broken by nested interactive controls

`ui/index.html:1976-1988` makes the whole card `role="button"` and nests a real
Remove button inside it. Enter/Space on Remove bubbles to the card key handler,
which prevents the button action and redraws the editor. Live proof: Space on
“Move NAICS 334118 out of the boundary” left all 5 boundary codes present and
opened `334118` in the inspector.

Use a non-button card with a separate explicit Inspect button, or stop the child
keyboard event without creating nested button semantics. Add a keyboard
interaction test.

### P2 — phone layout overflows instead of reflowing

`ui/index.html:335-355` forces two card columns at `max-width:720px`; the
unwrapped code/role/provenance/remove header cannot fit two tracks on a phone.
At a live 320px viewport, the document scroll width was 525px and the NAICS
workshop was 166px wide with 185px cards extending past it. The test at
`tests/test_naics_workshop.py:218-220` merely asserts the problematic CSS text.

Use one column at the phone breakpoint (or a proven compact header), and replace
the source assertion with rendered 320/390px overflow checks.

### P2 — alternate restore/edit paths lose or misstate provenance

- `ui/index.html:1997-2003` overwrites the preserved origin with `restored`.
- `ui/index.html:2097-2100` removes a matching kept-out record and creates a new
  generic consultant entry, discarding its title, role, rationale, origin, and
  note.
- `ui/index.html:2125-2138` lets a consultant edit a system title/note without
  marking the entry edited; redraw-on-blur/change also disrupts keyboard focus.

Keep original provenance as a field (record restoration as a decision/event),
route manual re-add of a kept-out code through the same lossless restore owner,
and make inspector edits accurately update provenance without replacing the
focused form mid-tab.

### P2 — navigation, undo, payload, and execution-copy tests are incomplete

- `ui/index.html:2258-2261` scrolls to NAICS but never moves focus, then replaces
  the anchor 480ms later.
- `ui/index.html:2058-2060` clears undo state without redrawing; the stale visible
  Undo action then dereferences `null`.
- Non-dictionary metadata entries raise `AttributeError`; the new API fields can
  return HTTP 500 rather than the documented 400.
- The inspector/docs say web is untouched, but `run_searches.py:217-223` passes
  authoritative NAICS to the web lane and `tools/api/web_search.py:78-85`
  appends the codes to its prompt. Either narrow the claim to persisted web
  specs or change execution.

The four UI tests are source-string checks, not interaction, focus, keyboard, or
viewport tests; they do not exercise any of these contracts.

## Verification

- `tests/test_naics_workshop.py`: **14 passed**.
- Full suite in the restricted review environment: **956 passed / 1 skipped**;
  the Chrome PDF smoke test was blocked by the sandbox. Re-running that exact
  test with Chrome access: **1 passed**, matching the reported effective total
  of **957 passed / 1 skipped**.
- Live current-build review on a second local port; read-only GETs only. No
  strategy save, approval, scope change, search, report generation, or metered
  call occurred.

## Repair order / re-review bar

1. Fix search reconciliation and persistence invariants first.
2. Make candidate inspection non-mutating and repair keyboard semantics.
3. Make journal snapshots complete and malformed payloads fail loudly.
4. Repair phone layout, focus, undo, and provenance-preserving alternate paths.
5. Add behavioral regressions for every reproduced case; then rerun the full
   suite and live-test desktop plus 320px/390px viewports.

The additive defaults are backward-compatible and the single approval leg was
not moved. Those sound foundations are not enough to lift HOLD while the
authoritative boundary, audit trail, and operator actions can contradict one
another.
