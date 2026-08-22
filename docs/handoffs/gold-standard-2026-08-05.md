# Handoff: Golden Press gold-standard build (2026-08-05)

Paste the prompt at the bottom into a new session. Everything above is the
state it will find.

---

## Where the code is

One line, no forks: **`fable/decision-engine` == `gold/merged-base` ==
`d495c9e`**. Suite **3847 passed, 1 skipped, exit 0**.

- Primary checkout `/Users/wtjohnson/federal-sales-os`
- Worktree `/Users/wtjohnson/federal-sales-os/.claude/worktrees/compassionate-leakey-4ba782`
- Safety tag `pre-merge-safety-2026-08-05` (pre-merge HEAD)

Three separate bodies of work were committed verbatim and merged today,
each verified green before committing: Items 1/3/4 (`da27220`), the
proactive competitive engine (`acd1b45`), and the contract architecture
already on the branch.

## What is DONE

- **Reconciliation.** Items 1/3/4 (WRONG DOMAIN attestation, golden-target
  recall, corpus widening, witnessed negative context) and the competitive
  engine now coexist with the REPORT_CONTRACT eight-band grammar.
- **Thinklogical entities.** `clients/thinklogical/profile.json` carries
  both feeds: the competitive roster (seeds discovery, anchors ambiguous
  aliases at match time) and `research_entities` (feeds
  `press._bridge_entities` and the verbatim-search L2 lane). The APPROVED
  packet was never edited; `revise()` refuses one and re-approval is an
  operator gate.
- **An untested competitive lane can no longer print a tested zero.**
  `render.competitor_coverage(pack)` derives `not_supplied /
  supplied_not_screened / partial / screened` from supplied entities plus
  the pack's own LaneQuery rows. Band 01 says COMPETITIVE VALIDATION NEXT
  or PENDING until a real screen has run. Two new fail-closed rules:
  `competitor_zero_untested`, `client_facing_internal_language`.
- **Treemap correctness.** `_squarify` terminates on tail-dominant sets and
  no longer crosses box dimensions; `svg_geometry_non_positive` fails any
  artifact carrying a rect the renderer would silently drop.
- **Amendment v1.2.** Bands render their strongest cited content instead of
  a zero when no account is in play; empty bands refuse model prose.
- **One editing runtime.** The press injects nothing
  (`compile_editable_studio(..., inject_runtime=False)`); the skeleton's
  own editor handles marks natively, proven by
  `tests/test_studio_interaction.py`.

## What is NOT done

1. **The competitive BAND does not render.** `competitive.py` runs before
   composition and writes all six sidecars, but its band renderer was
   written against the retired grammar (sb-* markup, accounts/capabilities/
   forecast/candidates/signals). Port it into the contract's eight-band
   form, add the band to `docs/REPORT_CONTRACT.md` §1 (the contract loader
   is the single source and `_assert_prose_keys_match_contract()` enforces
   it), and give it a prose slot.
2. **Phases 2-5 of the gold-standard brief** (see the brief itself for the
   full acceptance list). In dependency order:
   - Forward-window classification: CURRENT WINDOW / STATUS CHECK /
     HISTORICAL off the pack press date, never the wall clock. TACTICS
     `08/03/2026` is the regression case: a passed anticipated
     solicitation date must never appear in future-oriented prose.
   - Deterministic money proofs keyed by semantic proof id, emitted as a
     JSON payload map. Never scrape ancestor DOM text.
   - Computed pink-to-purple agency treemap with per-tile amount, record
     count, percentage, seal slot and click-through, derived from the
     agency rollups.
   - Prime-holder ranked bars with per-recipient editable logo slots keyed
     to normalized entity identity.
   - Header/logo contract: client mark left, GTM right, both
     `object-fit: contain`, no clipping or stretching.
   - Editability contract: every visible mark an editable slot, dropped
     images embedded as `data:` URIs, portable save/reopen, state merge
     across regeneration, and a locked client export with zero editor
     machinery.
   - The remaining fail-closed validator rules and the browser tests.

## Traps that cost time today

- **The gold reference HTML is a visual comparator, not a code source.**
  Its agency tiles are hand-packed CSS grid whose areas contradict their
  own printed percentages (DLA renders 15% while labeled 20.2%); its money
  proofs scrape the ancestor card and harvest `mailto:` links as "cited
  records"; its GTM logo is deliberately distorted with `object-fit: fill`.
  The brief overrides all three. Its own document prints three
  inconsistent record counts and an off-by-one agency total.
- **Code paths gated off for months are untested code.** Two latent bugs
  surfaced the moment a rule change let them run: `_CITED_RECORD` was
  referenced and never defined, and a note builder emitted the banned term
  "corridor". Expect more when the competitive band starts rendering.
- **Silent breakage the automerge cannot see.** Losing one local binding
  (`scan_text` in `check_links`) broke 32 tests at once. Always run the
  full suite exit-gated after a merge, never `| tail`.

## Standing rules

- Report builds run through the **Command Center only**
  (`POST /api/run`), never ad-hoc bash. Diagnostic zero-LLM scripts in
  bash are fine.
- **Never change gate settings** (scope, approvals, release) agent-side.
  A run-scoped `scope_override` is the sanctioned mechanism and leaves the
  stored gate untouched.
- **Item 2 stays unbuilt.** Its content is unrecoverable from any
  transcript on disk; reconstructing it would be invention.
- Prose is pinned to `claude-opus-5`. No em dashes in output-reachable
  strings. Done means full suite green with the exit code checked.

---

## THE PROMPT

> Continue the Federal Sales OS golden-press gold-standard build.
>
> Read first: `docs/handoffs/gold-standard-2026-08-05.md` (this file),
> `CLAUDE.md`, `docs/CONVENTIONS.md`, `docs/CONTRACT_SURFACES.md`, and
> `docs/REPORT_CONTRACT.md` including amendments v1.1 and v1.2.
>
> The tree is one line at `d495c9e` on `fable/decision-engine`, suite 3847
> passed / 1 skipped, exit 0. Verify that before you change anything.
>
> Build, in this order, committing each with its tests and an
> exit-code-verified full suite:
>
> 1. Port the competitive band into the contract's eight-band grammar.
>    The engine already computes everything and writes six sidecars; only
>    the renderer is missing. Amend `docs/REPORT_CONTRACT.md` §1 to name
>    the band, since the contract loader is the single source of truth.
> 2. Forward-window classification (CURRENT WINDOW / STATUS CHECK /
>    HISTORICAL) off the pack press date, with a fail-closed rule that a
>    passed date never appears in a future-oriented claim. TACTICS
>    `08/03/2026` is the regression test.
> 3. Deterministic money proofs keyed by semantic proof id. Emit a JSON
>    payload map; never scrape ancestor DOM text.
> 4. The computed pink-to-purple agency treemap and the prime-holder
>    ranked bars, both derived from the rollups, both carrying editable
>    logo/seal slots keyed to normalized entity identity.
> 5. The editability contract and the locked client export, with the
>    browser tests the brief lists.
>
> Do NOT press a report unless I ask. Code only: future presses should
> reflect the changes. Report builds go through the Command Center when I
> do ask.
>
> Tell me at the end what you did not finish and why.
