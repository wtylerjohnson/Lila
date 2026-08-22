# Candidate Review v1 Chunk 3A checkpoint

Recorded: 2026-07-22

## Decision

**COMPLETE; FINAL GATE 2 GO.** The standing event universe and
client-specific IDIQ/vehicle watch are implemented in the isolated integration
worktree. The lanes are client/run/scope bound, preserve incomplete coverage,
and feed the existing eight-section Candidate Review document without creating
a ninth report section or an automated business decision.

## Implemented behavior

- A versioned registry represents 68 official organizers, 80 recurring event
  families, and 146 deterministic watch targets. Applicability is derived from
  the approved client frame; every applicable target is attempted every run,
  while priority controls search order only.
- Raw registry entries and aggregators remain discovery inputs. Only an exact
  current event edition with authoritative official evidence can enter the
  calendar.
- Typed vehicle manifests cover public SAM notices, USAspending parent IDVs and
  order lineage, official program/forecast sources, GSA sources, and optional
  authorized restricted exports. Unavailable, failed, stale, partial, and
  not-run lanes stay explicit.
- Vehicle, parent IDV, notice, order, and award identities remain separate.
  Task/delivery/order publication requires an exact official order record that
  names both the order and its exact parent IDV.
- Board-cited USAspending rows are a partial research subset, never a complete
  query census or a valid zero-result claim.
- Verified procurement deadlines, research clocks, event dates, and IDIQ or
  on-ramp dates project into the existing Federal opportunity calendar.
  Vehicles do not increment opportunity counts unless a current official
  notice independently passes the normal candidate rules.
- The assessment and refresh chains run compose, mandatory award re-pull, then
  Candidate Review watch. Refresh fails closed if the re-pull exits zero but
  does not change the exact sweep contents.
- Immutable replay source bundles bind only after the mandatory current sweep
  exists. Provider state, manifests, results, coverage, receipts, snapshots,
  and ticker diffs persist with exact hashes.
- Removal requires an exactly comparable frame, registry, query manifest, and
  complete returned census. Changed search semantics or partial coverage cannot
  manufacture a removal.
- Analysts retain discretion. No signal, mismatch, ranking, access gap, or
  source failure automatically pursues, rejects, disqualifies, assigns win
  probability, or assigns pipeline value.

## Audit findings closed

Two strict read-only audit rounds found and verified finite repairs for:

1. A pre-bound replay configuration that could not bind after the required
   fresh award re-pull.
2. False removals across changed frames, registries, or query semantics.
3. Weak parent-IDV lineage on typed and legacy vehicle/order publication paths.
4. Priority cadence that could skip applicable event targets.
5. Refresh proceeding after an exit-zero but unchanged award re-pull.
6. Board-cited USAspending subsets incorrectly implying complete zero results.

Every repaired boundary now has an adversarial regression test.

## Verification evidence

- Candidate Review v1 focused suite: **379 passed**.
- Adjacent assessment, refresh, report, UI, intake, and arbiter suite:
  **288 passed**.
- Composer suite: **150 passed**; four tests require ignored Mark43/Riverbed
  cleaned snapshots absent from the isolated worktree.
- Independent final read-only release audit: **GO**.
- Claude Code Max final read-only Gate 2 audit: **GO**.
- Ruff and `git diff --check`: passed.
- No live federal-source or OpenAI provider call occurred in tests or audits.

## Next step

Begin Chunk 4, the optional OpenAI adversarial collaborator. Keep its outputs
independent and advisory, never expose or persist the API key, and stop for the
dedicated Gate 3 security and model-independence review before the renderer.
