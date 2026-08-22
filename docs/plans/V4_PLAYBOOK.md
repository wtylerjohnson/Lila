# V4 PLAYBOOK · locked 2026-07-17

The game plan and every prompt, in firing order. When a session
finishes, check it off here. No sessions outside this file until V4
ships.

## The golden path (V4 definition, do not drift)

Command Center > New Client > name + scope preset (scope lives in
intake; editable later from client view) > system runs sweep +
relevance unattended > pauses ONCE at the Keyword/NAICS review screen >
operator clicks GO > auto-compose board content from shortlist > award
re-pull > gates > pressed Insignary-format board lands as DRAFT for
operator send. Re-scope = edit scope on client view + refresh press
(delta engine). The DRAFT review is the operator's veto; the
zero-evidence and off-scope lints are what make auto-compose safe.

## Standing rules (apply to every session below)

- Every model session works in a linked worktree from current local
  main: git worktree add <path> -b <branch> main, then
  rsync -a <primary>/data/ <worktree>/data/, then verify suite.
- Session close-out is mandatory: rebase onto current local main
  (keep-both-main-first, gate order normalize links -> freshness ->
  workspace hard-fail -> white-label), full suite, report final SHA
  and count.
- Humans merge. Models never touch main or the primary checkout.
- If main moved after a session's close-out, send the session:
  "main moved; per your close-out rule, re-rebase onto current local
  main in your worktree, re-run the full suite, report the new final
  SHA and count. Do not touch the primary checkout."

---

## TRACK 1 · MONDAY (V3 rails, proven path) — runs first, beside V4

- [ ] M1. Press Mark43 sweep (Command Center). Verify after: scope
      diagnostic + relevance pass; shortlist at data/state/relevance/.
- [ ] M2. Keith's DHS ruling for Riverbed. If DHS IN: delete the
      "070" line in clients/riverbed/engagement_scope.json. Then
      press Riverbed sweep.
- [ ] M3. Corridor hour x2 (Tyler): review evidence spans; pick plays
      per section. Log picks.
- [ ] M4. Compose session x2 (Fable, worktree each): build
      clients/<slug>/signal_board_content.json in the exact Insignary
      schema from the picked plays. Award re-pull every cited figure.
      Press via run_signal_board.py. Fix what gates catch. Press
      clean.
- [ ] M5. Monday AM: final press each, 60-second manual link check
      (INTERNAL sidecar list), send.

---

## TRACK 2 · V4 BUILD (fixed list, no additions)

### V4.1 — Merges only (tonight) — [x]

1. Ping codex/refresh-delta-engine with the re-rebase line; merge on
   green:
   git merge --no-ff codex/refresh-delta-engine -m "Merge refresh-delta engine: snapshots, delta classes, refresh press"
2. Get onboarding branch a3aa69d close-out (or ping re-rebase);
   merge on green:
   git merge --no-ff <onboarding-branch> -m "Merge onboarding flow: add-client forms, validator wiring"

### V4.2 — Auto-Composer (Fable) — [ ]

# Session: Auto-Composer · Shortlist to Board Content

## Preamble
Linked worktree from current local main per standing rule (worktree
add, rsync data/, verify suite). Report baseline count; materially
below the last known main count: stop, flag.

## The model
V4's one missing organ: deterministic selection plus gated composition
that turns a client's relevance shortlist and stored data into
clients/<slug>/signal_board_content.json in the exact Insignary
schema, with zero hand editing. Machine-screened mode: the
zero-evidence lint and off-scope lint bind hard; no card renders
without quoted record evidence and in-scope verdict.

## Scope, in
1. Selection rules, deterministic and documented: from the relevance
   output plus sweep/buyer/recompete data, pick per section —
   best-fit (top N in-scope relevant candidates by score then
   evidence tier, N configurable, default 3), competitor lanes (top
   direct-award vendors in the client's corridors from incumbent/
   buyer records, default 5), teaming (primes on selected plays with
   subaward evidence first, entry theses second, default 4), horizon
   (recompete events, open calls, transitions inside 36 months,
   default 4). Ties broken deterministically. Every selection carries
   its evidence spans forward.
2. Composition: structure fully deterministic (ids, sections, figure
   component references for the hero per the computed-hero pattern,
   builder-derived links only). LLM used ONLY for prose seams: fit
   lines, angle lines, wedge lines — composed through the existing
   compose_cache so replay stays deterministic, and every line passes
   the existing lint battery (house style, no em dashes, softening
   pattern, banned lexicon, R12 titles). A lint failure regenerates
   once, then falls back to a deterministic template line; never
   ships unlinted prose.
3. Hero figure: component fact references summing per the existing
   reconciliation; sub-line "Obligated across the N cited incumbent
   corridors · not pipeline revenue" with N computed.
4. Provenance: every figure row carries source_system,
   source_record_id, retrieved_at from the sweep/re-pull; nothing
   analyst_attested by the composer, ever.
5. Live verification: run end to end on the real mark43 sweep (and
   riverbed if swept): compose, re-pull, press. Report the pressed
   board's gate results verbatim. A DRAFT with named violations is an
   acceptable outcome to report; silent success claims are not.
6. CLI entry: run_compose.py --client <slug> producing the content
   JSON plus an INTERNAL compose trail (what was selected, why, what
   the LLM wrote vs template fallback).

## Scope, out
- No changes to gates, lints, relevance engine, schemas, or the press
  runner; the composer produces content, the existing machine judges
  it. Golden manifest untouched and green.
- No UI; V4.3/V4.4 wire it.

## Constraints
Full suite green each commit; ruff clean on touched files; small
commits: selection, composition, hero, trail, CLI, live run, tests.
Tests: selection determinism; lint-failure fallback path; evidence
carried on every card; composer cannot emit analyst_attested;
schema-exact output validated by the board content loader.

## Session close-out, mandatory
Per standing rules. Report final SHA and count.

## Report
Selection rules verbatim; the mark43 pressed-board gate results;
compose trail sample; LLM-vs-template line counts; final SHA and
count; deferrals with reasoning.

### V4.3 — Chain Orchestrator (Codex) — [ ]

# Task: Assessment Chain Orchestrator · One Path, One Pause

## Preamble, mandatory first action
Linked worktree per standing rule; rsync data/; verify suite; report
baseline. Read docs/VERIFICATION_POLICY.md and the V4 golden path in
docs/plans/V4_PLAYBOOK.md.

## The job
run_assessment.py: a resumable state machine executing the sanctioned
runners in order with exactly one human pause state.
States: INTAKE_DONE -> SWEEPING -> RELEVANCE -> AWAITING_TAXONOMY_GO
(pause) -> COMPOSING -> REPULLING -> GATING_PRESS -> DRAFT_READY |
FAILED(stage, reason). Persistence per client at
data/state/assessment/<slug>.state.json, append-only transitions with
timestamps.

## Spec
1. Each state invokes the existing sanctioned runner (sweep runner,
   relevance pass, run_compose.py from V4.2, award re-pull,
   run_signal_board.py). The orchestrator reimplements nothing and
   parses runner exit codes/artifacts to advance or fail.
2. AWAITING_TAXONOMY_GO: entered after relevance output exists;
   advancing requires an explicit GO marker (CLI flag now, UI writes
   it in V4.4). The state file records who/when.
3. Resume semantics: run_assessment.py --client <slug> continues from
   the persisted state; --restart-from <state> allowed; a FAILED
   state names the stage, the runner's error, and the fix surface.
4. Control Room seam: a status endpoint/function returning the state
   machine per client for the radar cards and an alert line for the
   ticker ("riverbed awaiting your keyword review"). Wire if the
   radar is merged; stub with tests if not.
5. Refresh path: --refresh runs the delta engine's orchestration
   (re-sweep, re-pull, relevance, compose refresh, press, delta) and
   lands in DRAFT_READY the same way.
6. Tests: full state walk with mocked runners; pause cannot be
   skipped; resume from every state; FAILED naming; double-GO
   idempotence; concurrent-client isolation.

## Constraints
Deterministic; no LLM calls in the orchestrator; no new dependencies;
no changes to any runner's internals. Small commits: state machine,
runner adapters, pause/GO, resume, seams, tests.

## Session close-out, mandatory
Per standing rules. Report final SHA and count.

## Report
State diagram as implemented; the state file schema; a full mocked
walk transcript; the pause/GO mechanics; final SHA and count;
deferrals with reasoning.

### V4.4 — Checkpoint UIs (Fable) — [ ]

# Session: V4 Checkpoint UIs · Intake Scope Picker, Taxonomy Review, Re-Scope

## Preamble
Linked worktree per standing rule; rsync data/; verify suite; report
baseline. Read the V4 golden path in docs/plans/V4_PLAYBOOK.md.
Prerequisites on main: onboarding flow (V4.1) and orchestrator (V4.3).

## Scope, in
1. Intake: the Add Client flow gains the scope preset picker
   (civilian / defense / all_federal radio + add/remove code edits)
   if V4.1's merged flow lacks it; saving intake writes the three
   client files AND starts the orchestrator (state INTAKE_DONE ->
   SWEEPING) with one confirmation.
2. Taxonomy review screen: when a client reaches
   AWAITING_TAXONOMY_GO, the Control Room alerts (ticker line + card
   state) and the screen renders the taxonomy as editable structure —
   CORE/ADJACENT/EXCLUDE terms with match modes, kill rules, NAICS
   boundary, scope summary — validated by the existing loaders on
   save. The GO button writes the orchestrator's GO marker and the
   chain proceeds. Edits bump taxonomy version.
3. Re-scope control on the client view: change preset / add/remove
   codes, validated, saved to engagement_scope.json, with a
   "Refresh assessment" press that invokes the orchestrator --refresh
   path. The delta summary renders on completion.
4. DRAFT_READY surfacing: card state + ticker line + one-click open
   of the pressed board and its INTERNAL sidecar.

## Scope, out
- No orchestrator or engine changes; UI writes markers and files, the
  chain does the work. No home-screen redesign (V4.5). Golden
  untouched and green.

## Constraints
Existing Control Room visual language ONLY: reuse the shipped
scoreboard/ticker/component styling; no new fonts or color
vocabulary. Full suite green; ruff clean; UI tests per existing
patterns. Small commits: intake picker + chain start, review screen,
re-scope, DRAFT surfacing, tests.

## Session close-out, mandatory
Per standing rules. Report final SHA and count, plus rendered
screenshots/HTML of each screen with the real client book.

## Report
Each screen rendered with real clients; the GO write path; the
re-scope-to-refresh wiring; final SHA and count; deferrals with
reasoning.

### V4.5 — Home Screen Rebuild (Fable, parallel, non-blocking) — [ ]

NOTE (operator revert, 2026-07-18): the home page is intentionally
bare of radar UI; the first radar band was removed from ui/index.html
on main while all radar backend machinery (ui/lifecycle.py,
/api/radar, the C2 seam, all tests) stays live. Home UI returns only
via the fable/home-rebuild merge, which must present screenshots
before any home UI ships.

# Session: Home Screen Rebuild · House Visual Language, Law Not Vibe

## Preamble
Linked worktree per standing rule; rsync data/; verify suite; report
baseline.

## The defect this session ends
The radar band shipped functional but visually foreign, and its first
version displaced beloved elements. The rebuilt home must read like
the same shop that ships the client boards.

## Design law (violations are defects)
1. The existing client-level scoreboard and ticker are untouchable:
   present, unrestyled, unrepositioned within client views.
2. The home screen's design system IS the existing Control Room +
   board aesthetic: same fonts, spacing, color vocabulary; the
   cross-client ticker literally reuses the shipped ticker component
   CSS/motion; the practice strip uses the scoreboard's visual
   language.
3. Client cards: name, lifecycle stage (from ui/lifecycle.py,
   unchanged), freshness light, last press date, headline stat,
   orchestrator state line when present ("awaiting keyword review").
   Click-through to client view.
4. Screenshot proof REQUIRED in the report: full home page,
   before/after, plus one client view proving the original scoreboard
   and ticker intact. No merge recommendation without them.

## Scope, out
No API/derivation changes; ui/lifecycle.py and /api/radar as-is. No
engine changes. Golden untouched and green.

## Constraints
Full suite green; ruff clean; UI tests updated. Small commits:
layout, cards, ticker reuse, strip, states, tests.

## Session close-out, mandatory
Per standing rules. Report final SHA and count.

---

## Firing order and triggers

1. NOW: Track 1 M1 (Mark43 sweep press) — human, one click.
2. Tonight: V4.1 merges — human, after re-rebase pings come back
   green.
3. Tomorrow AM, parallel worktrees: V4.2 (Fable) + V4.3 (Codex).
   V4.5 may run parallel any time.
4. Track 1 M2-M3 tomorrow; M4 Sunday; M5 Monday AM.
5. V4.4 fires when V4.1 and V4.3 are on main.
6. V4 done = riverbed (or the next new client) travels intake ->
   DRAFT entirely by button, witnessed end to end.

## Merge log (fill in)

- [x] refresh-delta-engine · SHA eb88371 · suite 1548 passed, 1 failed (ledger: riverbed), 1 skipped · cleared ledger 1-3
- [x] onboarding a3aa69d · SHA 55ec304 · suite 1561 passed, 0 failed, 1 skipped (test_govinfo: env key present) · cleared ledger riverbed line
- [ ] V4.2 auto-composer · SHA ______ · suite ______
- [x] V4.3 orchestrator · SHA 224f38f · suite 1644 passed, 0 failed, 1 skipped (test_govinfo: env key present)
- [x] refresh-c3-fix · SHA 9192583 · suite 1651 passed, 0 failed, 1 skipped (test_govinfo: env key present) · closed the C3 refresh deferral
- [ ] V4.4 checkpoint UIs · SHA ______ · suite ______
- [ ] V4.5 home rebuild · SHA ______ · suite ______

---

## V4 CONTRACTS · the whole, written once (both models build against THIS)

Every session reads this section. A session needing an interface not
defined here stops and reports; it never invents one.

### C0. Integration authority
The standing Integrator session is the sanctioned exception to
"humans merge": it alone operates the primary checkout and merges to
main under its charter protocol. Build sessions still never touch
main or the primary checkout.

### C1. Composer CLI (V4.2 provides, V4.3 consumes)
- Invocation: python3 run_compose.py --client <slug>
- Reads: relevance output at data/state/relevance/, stored sweep,
  recompete calendar, buyer data
- Writes: clients/<slug>/signal_board_content.json (exact Insignary
  schema) + INTERNAL compose trail beside it
- Exit 0 = content written and loader-valid; nonzero = named failure
  on stderr; atomic write, never partial

### C2. Orchestrator state (V4.3 provides, V4.4 consumes)
- State file: data/state/assessment/<slug>.state.json, append-only
  transitions [{state, at, by, note}]
- States: INTAKE_DONE, SWEEPING, RELEVANCE, AWAITING_TAXONOMY_GO,
  COMPOSING, REPULLING, GATING_PRESS, DRAFT_READY, FAILED
- GO marker: data/state/assessment/<slug>.go.json {"by","at"}; UI
  writes it (V4.4); CLI flag --go writes it interim
- Status seam: one function/endpoint returning {slug, state, since,
  alert_line} per client; radar cards and ticker consume read-only

### C3. Press + refresh (existing, consumed by V4.3)
- Press: python3 run_signal_board.py --client <slug>; gates inside;
  DRAFT/DO-NOT-SEND semantics unchanged
- Refresh: the delta engine's entry (run_refresh_press.py) as merged;
  V4.3 --refresh wraps it, reimplements nothing

### C4. Intake (V4.1 onboarding flow provides, V4.4 extends)
- Saving intake writes the three client files via the existing
  validators; V4.4 adds the scope preset picker if absent and the
  INTAKE_DONE -> SWEEPING kickoff

C4 AMENDMENT 2 (operator ruling 2026-07-18): The "+ New Client"
intake collects ONLY:
- Company name (with the existing identify-on-type behavior:
  company search + logo resolution as name is entered, and website
  autofill from the identified company; operator may instead paste
  the website directly and identification runs from it. This
  behavior exists and works; preserve it exactly.)
- Scope preset (civilian / defense / all_federal + add/remove
  codes)
- Nothing else.
REMOVED from intake entirely: "what they do", "why they win",
"notable past contracts", certifications, contact email, and any
similar descriptive fields. Discovering capability, edge, and
past-performance context is the ENGINE'S job: profile.json's
capability content (summary, core/adjacent terms, NAICS boundary,
competitors) is DRAFTED BY INFERENCE from the identified company's
website/public materials at intake completion, then surfaced for
correction at the one human checkpoint (AWAITING_TAXONOMY_GO's
keyword/NAICS review). The operator's job at intake is identity +
scope; the operator's job at the checkpoint is judgment on the
machine's draft. Validators still require a populated profile
before SWEEPING: the inference draft satisfies them or the chain
fails named at that stage, never silently.

C4 AMENDMENT (operator ruling 2026-07-18): There is exactly
ONE client-creation entry point in the Control Room: "+ New Client"
(currently rendered "+ Add client"; rename to "+ New Client"). The
separate onboarding button/flow entry is redundant and removed.
"+ New Client" absorbs the onboarding flow's machinery (forms,
validators, file-writing) as the only path in. Scope preset is a
field IN that flow (V4.4 builds it). Completing intake starts the
orchestrator after one confirmation. Any prior wording implying a
separate onboarding entry is superseded.

### C5. Verify pass (codex/verify-pass provides)
- Runs after fact-pack build, before release gates; writes
  Fact.disputed / Fact.competing_values only; never resolves; fills
  the delta snapshot verification field (DEFERRED -> COMPLETE)

### Client view law
CLIENT VIEW LAW (operator ruling 2026-07-18): The client page
opens, in order: (1) client logo as it renders today, (2) the
scoreboard, (3) an engagement scope line/block (preset name +
effective departments summary + edit affordance per V4.4's
re-scope control when it lands). Everything else currently
crowding the top of the client view moves below this trio or
into its existing tabs/sections. The scoreboard and ticker
components themselves remain visually untouched per standing
design law; this ruling is about ORDER and what sits above the
fold, not restyling. SALES VIEW: retired from the UI for now --
entry controls hidden, routes and backend preserved intact for
later resurrection; no deletion of its code or tests.

### Client-side UX law
CLIENT-SIDE UX LAW (operator rulings 2026-07-18):
1. ONE HUMAN GATE, DEFINED. The single stopping point is the
   NAICS-code and keyword refinement review (AWAITING_TAXONOMY_GO).
   Intake completion flows directly into the running chain with no
   intermediate confirmations; after the operator's GO the chain
   runs unattended through compose, re-pull, gating, and press to
   DRAFT_READY with zero further confirmations. DRAFT_READY is
   SEND-GRADE: the pressed artifact requires no human corrections
   before delivery. Any post-GO human fix that proves NECESSARY (not
   stylistic taste or the operator's optional gray-out/legal pass)
   is a machine defect: filed and converted to a gate or composer
   rule the same week, never institutionalized as a review step.
   Sending remains a human act; reviewing is optional.
2. VISIBLE CHAIN MOTION. The client page renders the chain as live
   visual process per the authority-and-motion doctrine: stage
   progression as motion (C2 seam is the data source); SWEEPING
   shows source identity with live tallies; RELEVANCE shows
   candidates counting and agencies lighting; REPULLING/GATING shows
   figures verifying with visible pulse; arrival at the analyst gate
   visibly hands the baton to the human. TRUTHFULNESS RULE: every
   animated number is real data from state files, runner artifacts,
   and the C2 seam — no fake progress ever; stages without granular
   telemetry show honest stage-level motion only, and telemetry is
   added at the runner's owner session later, never faked in UI.
3. Placement: this motion layer lives beneath the logo/scoreboard/
   scope trio per client view law; when no chain is active it
   collapses to the last-run summary line.
Implementation: V4.4 (gate/flow + basic stage motion); V4.5/V5
(motion depth).

### One Deliverable law
ONE DELIVERABLE LAW (operator ruling 2026-07-18): LILA produces
exactly ONE client-facing artifact: the Signal Board in the
Insignary format, client-titled per the naming tiers (Federal
Opportunity Assessment; Pre-Assessment for the provisional/demo
tier). Every chain, press surface, and acceptance test targets this
single output. The capture-brief document format
(run_capture_brief.py's 4-beat chapter deliverable) is RETIRED from
all client-delivery surfaces: it is not a deliverable, its outputs
are internal feedstock at most (fact packs, review directives feed
the composer). The V4-ACCEPT done-test asserts the pressed artifact
is the Signal Board and nothing else. Any future second deliverable
requires an explicit operator ruling amending this law.

### KNOWN REDS ledger (green = zero failures beyond this list)
(empty as of the 2026-07-18 V4.1 merges: refresh-delta cleared the
three test_dashboard_views link-gate lines via its fixture clock pin;
onboarding-flow cleared the riverbed scope line. Green now means
zero failures, full stop.)
An Integrator merge that clears a ledger line updates this list in
the same playbook commit.

### Dependency DAG and predetermined merge order
delta-engine ──┐
onboarding ────┼──> V4.3 orchestrator ──> V4.4 UIs
V4.2 composer ─┘   (verify-pass: independent, merges when green)
                   (V4.5 home rebuild: parallel, independent)
Merge order as reports arrive: 1 refresh-delta-engine, 2
onboarding-flow, 3 whichever of V4.2/V4.3/verify-pass lands first
(later ones re-rebase per protocol), then V4.4, then V4.5 whenever.

### Track unification (Monday IS the V4 test)
- mark43 = V4.2's live verification client; the composer's real-sweep
  run produces Monday's board candidate; operator reviews picks and
  DRAFT (veto/edit, not build). V3 manual compose is the fallback.
- riverbed = first golden-path candidate if V4.3 lands in time,
  else V3 rails. Decision point Sunday 12:00; Monday ships
  regardless.

### Open deferrals
- CLOSED (codex/refresh-c3-fix): run_refresh_press.py now runs the
  C3-required relevance and compose-refresh steps before re-pull;
  the --refresh adapter accepts the complete sequence into
  DRAFT_READY while retaining named refusals for incomplete runs.
  The C1-absent path is mocked here and remains named/resumable until
  the parallel composer lands, as tracked separately below.
- COMPOSING fails exit-127 until run_compose.py (C1) merges;
  expected to clear with fable/auto-composer.

## V4 ACCEPTANCE · the bar every report is judged against
A report failing any line is a defect round-trip, not a merge.

### V4.2 Composer report checklist
- Selection rules stated verbatim and deterministic (tie-break named)
- Live pressed board attempted on real sweep; gate results quoted
  verbatim (DRAFT with named violations acceptable; unquantified
  success claims are an automatic fail)
- Every card carries quoted evidence spans; zero-evidence lint proven
  binding in machine-screened mode
- Hero renders from component references; zero analyst_attested rows
  (test cited)
- LLM-vs-template line counts; lint-failure fallback demonstrated
- Compose trail shows WHY per selection

### Composer output rubric (the live board, operator review)
1. Corridor story: picks cluster into a narrative or read as
   scattered singles (scattered = selection defect)
2. Teaming plausibility: primes tied to THESE plays by subaward
   evidence, not picked for size
3. Hero integrity: components are the corridors the board argues
4. Prose: passes lint AND reads as house voice
SEND-GRADE decision: the composer's board ships only if operator
edits are stylistic taste; any NECESSARY edit (selection or
correctness) = defect round-trip filed against the composer.

### V4.3 Orchestrator checklist — SATISFIED at merge 224f38f.

### codex/verify-pass checklist
- Worked-example test passes UNMODIFIED (empty diff of the test file)
- Live Insignary per-figure table quoted; drift = dispute row + gate
  fire, never resolved
- Tests prove: never calls arbitrate()/append_resolution();
  observed_by always set; idempotent re-run
- Delta snapshot verification field flips DEFERRED -> COMPLETE live

### V4.4 UIs checklist
- Rendered screens with the real client book; existing scoreboard/
  ticker untouched in client views
- GO button writes the C2 marker; chain provably proceeds
- Re-scope saves through existing validators and triggers --refresh
- Zero new fonts/colors; Control Room language only

### V4-ACCEPT · the done-test (fires with wave 2)
V4 is done when an integration test passes, not when sessions report
done: a live-marked test module walking a REAL client slug through
run_assessment.py end to end (intake -> ... -> GO marker ->
DRAFT_READY) then asserting on the pressed artifact: Insignary-format
structure (golden-manifest property classes), zero workspace links,
all federal links builder-derived or checked, every card
evidence-backed, verification COMPLETE, INTERNAL sidecar complete.
Plus a mocked fast variant in the default suite.

---

## V5 CANDIDATES · parked, no session fires on these until V4 ships

Direction (operator, 2026-07-18): the scoreboard evolves into a
CLIENT SNAPSHOT — the engagement in one glance, becoming the monthly
retainer face and the demo tier. Composition, in order: (1) the hero
number, computed-from-components discipline unchanged; (2) agency
seals ranked by evidenced opportunity (obligated dollars in scored
corridors, window proximity, recompete presence — never budget-size
framing, R11 applies); (3) a competitor lane — real company logos
displayed neatly as the DEFAULT design; legal posture is the
operator's call, so the design MUST include a per-logo and per-lane
operator control to gray out / de-mark logos pre-delivery (grayed =
styled text lockup fallback in house aesthetic), persisted per
client so the choice survives re-presses; drag-drop placement
remains the escape hatch; (4) a ticker fed by each research sweep
and future scheduled MONITORING sweeps (delta engine + scheduler +
headline composition — the retainer engine, its own V5 session);
(5) top 1-2 best-fit opportunities only; the full board becomes the
appendix behind the snapshot.

Ranked candidates from the same review: 1. delta ticker on refreshed
boards (machinery exists); 2. sparklines/trajectory on obligation
figures from USAspending history; 3. POC faces from official .gov
bios only, marks-style disclaimer discipline, operator legal check
before shipping; 4. verification pulse (visible VERIFIED marks with
record ID + retrieval date on hover); 5. live-status window chips
("CLOSES IN 63 DAYS" in chip vocabulary).

Rejected, do not build: agency budget charts (R11-adjacent, borrows
no authority); map visualizations (high effort, low information).

Design test for any future element: does it borrow real authority
(seals, records, faces) or signal live motion (ticker, deltas,
trajectories)? If neither, it is ornament — skip.
