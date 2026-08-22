# GTM Playbook — proprietary expertise accumulation
2026-07-09 · Tyler's spec: the system grows its opportunity-identification
ability engagement over engagement. Judgment becomes a durable, scored,
proprietary asset — never a junk drawer, never silent self-modification.

## Five stages
1. CAPTURE — the judgment trail persists as it happens:
   - strategy v0 snapshot at request_approval + append-only revisions journal
     (data/review/<slug>.journal.jsonl): every revise()/amend_terms()/scope
     change with before/after and timestamp  [BUILT 2026-07-09, this commit]
   - already persisted: re-screen guidance (job args), horizon refine rounds
     (horizon.json rounds[]), arbiter sidecars (.arbiters.json), reviewer notes
2. DEBRIEF — run_debrief.py --client X, the lessons-learned pass:
   diffs inferred-vs-final terms, reads guidance/rounds/violations/notes,
   engine PROPOSES lessons each citing its artifact; a human gate
   (approve/edit/reject, dialogue like the horizon gate) admits them.
   Nothing enters the playbook unapproved.
3. STORE — data/playbook/ (proprietary, operational; gitignored like all
   data/). Typed entries with provenance + confidence + scope:
   - term_lesson  (keyword/NAICS adjustments and why)
   - pattern      (opportunity-identification heuristics, testable when possible)
   - screen_rule  (triage steering that generalizes)
   - vertical     (bundles: client #2 in a vertical inherits client #1's vision)
   - process_rule (graduation candidates -> code/prompts; playbook keeps pointer)
4. INJECT — entries load as "earned heuristics (provenance + confidence)" into:
   intake inference, candidates pass, triage screen, horizon compose.
   HARD RULE: patterns guide attention; signals still cite the fact bank.
   Playbook text is INTERNAL-ONLY (same wall as client footprint).
5. SCORE — the flywheel: each sweep/debrief checks horizon projections and
   pattern predictions against what actually posted; hit/miss lands ON the
   entry; confidence updates visibly; the pattern batting average is the
   measurable-growth scoreboard (and a sales artifact).

## Graduation tiers
code gates (lint_*) < prompt rules < playbook entries — lessons move UP the
tiers by human decision, with the review-findings of 2026-07-09 (client bleed,
em dash, superlatives, stale dates) as the founding precedent.

## Build order (fresh session, after Osprey ships)
J1 journal [DONE] -> J2 playbook store + schemas -> J3 run_debrief + gate UI ->
J4 injection points -> J5 outcome scorer + scoreboard tile.
