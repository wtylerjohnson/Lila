# Codex review and repair: NAICS boundary explanations

**Base:** `aa6661bad2781d873e1f1d5ce95f4a0f6169ecae`

**Contract commit:** `ab0c6d1` (`feat(intake): require explained NAICS boundaries [ISOLATED CONTRACT]`)

**UI commit:** `477f11f` (`fix(ui): surface NAICS boundary reasoning [ISOLATED]`)

**Disposition:** **HOLD lifted. No remaining actionable findings.**

**Mode:** implementation plus independent adversarial re-review. No gate,
approval, scope, search, release, pointer, press, report production, or metered
action occurred.

## Result

`inferred_naics` remains the authoritative boundary. Every active card now
shows one of three honest states:

1. **Why it is in the boundary** for an explicit code-specific rationale or an
   exact legacy NAICS-keyword rationale.
2. **Original review context** when the retained text explains why a code was
   considered and cut. That text is never recast as an affirmative reason.
3. **Explanation needed** for a genuinely unexplained legacy code.

A normal save refuses an unexplained active code and focuses its Inspect
control. The vocabulary-to-NAICS navigation may carry an honestly incomplete
legacy record into the workshop, including title, role, note, and provenance,
without fabricating a rationale or losing those edits.

## New-inference contract

New intake generation uses a strict subtype while stored `IntakeStrategy`
packets remain additive and backward-compatible. The strict path now requires:

- a nonempty, duplicate-free ASCII six-digit boundary;
- one ordered metadata entry per inferred code;
- a substantive and distinct rationale for each code;
- system provenance, blank operator notes, and empty kept-out lanes;
- a nonblank strategy source trail and unchanged human review;
- system-only generated keyword provenance with no invented edit history; and
- every valid NAICS code supplied on the intake form.

Form-provided codes are bound into the schema passed to the decision engine.
The Max route can self-correct an omission on its existing validation retry;
other routes fail loudly. No generic post-model rationale is appended.

The structural contract proves completeness, distinctness, and traceability.
The inference prompt plus the unchanged human review gate own the semantic
judgment that the explanation is actually supported by the evidence.

## Legacy and interaction repairs

- Exact NAICS-keyword rationale hydrates only the matching code.
- Exact near-miss text remains separate review context, including when an
  affirmative rationale also exists.
- Records created by the first workshop version are migrated on load/restore:
  when a saved rationale exactly matches cut-context, it is demoted back to
  review context with a blank affirmative rationale.
- Backend move-out reconciliation keeps transition text in `note`; it never
  manufactures a boundary rationale.
- Candidate Keep out followed by restore cannot leave the code in both lanes.
- Candidate promotion opens with review context and requires an affirmative
  operator explanation before normal save.
- Undo expiry removes only its own banner, retains focus, and cannot let an old
  timer remove a newer banner after a server rerender.
- Inspector edits update the card in place, preserve focus, and mark prior
  system/consultant/restored provenance as edited.
- Save-and-advance rebuilds from the exact server-returned strategy before
  focusing the fresh NAICS region.
- Long explanations remain contained at 320px and 390px viewports.

## NETSCOUT proof

The existing approved packet remains untouched. Read-only live inspection
showed all five authoritative codes in the boundary:

- `334118`, `511210`, `541512`, and `541519` display their exact retained
  NAICS-keyword rationales.
- `334290` displays its retained near-miss language as **Original review
  context**, not as proof that the code belongs.

The opened NETSCOUT workstation had no workshop or document overflow at 320px
or 390px. The live review used GET requests only and did not save or approve.

## Verification

- Focused backend suite: **67 passed**.
- Real Chrome workshop suite: **20 passed**.
- Full restricted suite: **998 passed, 21 skipped**, with the expected single
  Chrome PDF sandbox failure (`rc=-6`).
- The exact PDF smoke outside the sandbox: **1 passed**.
- Effective complete result after running the 20 browser tests and PDF smoke
  with Chrome access: **1019 passed, 1 skipped**.
- Ruff and the relevant diff check: clean.
- Independent contract audit: **111 focused tests passed; HOLD lifted**.
- Independent final UI/backend re-review: **89 focused backend tests and 20
  real-Chrome tests passed; HOLD lifted**.

## Handoff

This explanation tranche is safe to build on. The unrelated scope-workstation
files and the pre-existing dirty review/entity files were not staged or
modified by these commits.
