# Candidate Review v1: Monday demo plan (Ed, 2026-07-27)

Recorded: 2026-07-23. Strategy approved-pending-operator-review; no build has
started under this plan. Companion docs: `candidate-review-v1-alias-remap-handoff.md`
(implementation spec for phase 1), `candidate-review-v1-house-style.md` (template
contract), `federal-event-and-idiq-watch-policy.md` (calendar + vehicle contract).

## The goal, in the operator's words

Input a company, click Approve on the Analyst Layer, arrive at a finished
report. One human gate. Zero credit spend. A report and a Command Center that
read as a proprietary LILA product, not a generated artifact. Calendar and
IDIQ/vehicle content in the deliverable. Demo to Ed on Monday 2026-07-27.

## Verified ground truth (2026-07-23 recon; all four sweeps file:line-verified)

1. Suite: 803 passed in the worktree (handoff's "889" is stale; doc drift only).
   All candidate-review work is UNTRACKED in the worktree; nothing committed.
2. Alias remap: not implemented. `run_candidate_review.py:44` guard refuses
   aliases; composer silently drops alias-referencing objects (hollow-report
   failure). This blocks all real-data work. Spec in the handoff is accurate.
3. Calendar: code-complete end to end (seeds -> calendar_engine -> document
   (32 budget) -> rendered rows, four origins merged). Only gap: no live
   `fetch_event`; driver passes None, so zero events exist today.
4. Vehicles (Pass C): composer + validators ready. VehicleSignal renders in
   Section 3; VehicleWatchRecord has NO Section 3 renderer row (dates reach the
   calendar only). Nothing in production mints either object:
   `verification.py:192-221` returns them empty. Watch records also need
   surviving CoverageRecords or the composer drops them (`composer.py:293-302`).
5. Landmine: calendar item titles embed literal em dashes
   (`calendar_engine.py:1486,1504,1529`); trips lint the first time vehicle or
   event rows render. Fix inside Pass C.
6. Template: renderer already implements the locked house style (embedded
   Manrope/Source Sans 3 base64, tokens, fact matrix, milestone rail, evidence
   tabs, KPI scoreboard, ticker, edit mode). No candidate-review HTML has ever
   been rendered on disk. The "LLM look" (Playfair + IBM Plex + gradient hero +
   ghost numerals via Google Fonts CDN) belongs to the retired capture brief;
   its skill lock does not constrain this deliverable.
7. Seals: `data/reference/seals/` drops are consumed by `tools/brand_marks.py`
   and main renderers, but files are untracked and some names will not resolve
   (space in "air force.png", .jpeg variants) against the monogram-keyed lookup.
8. Gates: the one-gate law is already server-side fact: `/api/run` refuses
   every step until the Analyst Layer packet is APPROVED (`ui/server.py:3919`).
   Legacy cockpit + capture-brief surfaces already curtained. Remaining gaps:
   nothing auto-runs after Approve (orchestrator chain is CLI-only), the
   capability-profile forms are unlinked so "+ New Client" dead-ends before a
   sweep (profile scaffold is CLI today), and several approval-shaped controls
   still render (Target door, partial release, requirement span review, arbiter
   key, internal-PDF buttons) plus a dead /onboarding stub.
9. Credits: burn autopsy = triage scales with sweep breadth (1 call/40 notices)
   and capture-brief convergence (~10-14 Opus/press); no global meter exists.
   Candidate review is verified zero-LLM/zero-network by default; both model
   doors raise (`run_candidate_review.py:230`, `authoring.py:110`). The demo
   path spends SAM quota only.

## Strategy decisions

- **Resequence the handoff plan: Pass C before live fetchers.** Operator marked
  calendar + IDIQ as required content. Pass C is fixture-testable, de-risks the
  renderer early, and the live pull then lights up candidates, vehicles, and
  events in one rehearsal instead of three.
- **Demo runs from the worktree checkout, not main.** The branch is ~96 commits
  off the main merge-base with heavy uncommitted work; merging before Monday
  buys risk, no demo value. Integrator merge to main happens after Ed.
- **Deterministic only through Monday.** Prose enrichment (authoring adapter)
  stays raising. Old pipeline stages (triage/qualify/dossiers/brief) are not in
  the demo path. Credit exposure: zero by construction.
- **Gate SETTINGS untouched.** One-gate is delivered by UI consolidation and
  auto-run wiring, not by flipping approvals/release defaults agent-side.
  DRAFT stays the resting state; releasing to a client remains an operator act.

## Phases

### Phase 0 (Thu): secure the base
Commit the untracked worktree work as reviewable commits following the chunk/
gate doc structure; correct the handoff's stale suite count. Acceptance: clean
`git status` except intentional WIP; suite still 803 green.

### Phase 1 (Thu-Fri): alias remap + owed contract fix
Implement `alias_remap.py` + tests exactly per handoff sections 4-5; replace the
driver guard. Separately, as an isolated labeled contract-surface diff: the
`_select_diverse` ranked-order one-line fix + repro regression test
(`candidate_engine.py:989-1016`). Acceptance: handoff test list green incl. the
integration hollow-report test; suite green.

### Phase 2 (Fri): Pass C, calendar to render, first look at the template
- `verification.py`: mint and return VehicleSignal / VehicleWatchRecord /
  accepted_vehicle_evidence_by_query from vehicle leads, with matching surviving
  coverage rows (typed fields per policy doc lines 345-355).
- `renderer.py`: add the Section 3 watch-record row (analog of `_vehicle_row`).
- Fix em-dash calendar titles; run language/lint gates over newly populated rows.
- Render fixture-driven sample reports for mark43 / iMerit / riverbed in bash
  (zero-LLM diagnostic path, sanctioned) and do the first visual pass against
  house-style acceptance checks.
Acceptance: Section 3 and Section 6 populated from fixtures; suite green.

### Phase 3 (Fri-Sat): live path
`live_fetchers.py` (SAM notice detail, USAspending award detail, agency
forecast page, GSA eLibrary; `fetch_event` with redirects + sha256; return
None never raise; pre-dedupe by canonical key; reuse `_http` retry + sam_quota).
Move 24h notice verification into the watch run and persist the notice block
into the generation (the 24h trap in handoff section 6.2). `--live` driver flag;
default stays zero-network. Iterate the event parser against real organizer
HTML (expect drops; that is the listed risk). Acceptance: one operator-triggered
live pull for one client produces a non-hollow document with real events and
vehicle records.

### Phase 4 (Sat): one-gate Command Center
- New `_step_cmd` branch `candidate_review` (NOT pre_assessment) running
  `run_candidate_review_watch.py --client X` then `run_candidate_review.py
  --client X` over POST /api/run -> GET /api/job/<id>.
- Wire Analyst Layer Approve to auto-run the chain to the finished report
  (approve -> candidate_review job -> Deliverables entry). This is the
  "click approve and arrive at a report" requirement.
- Close the profile cliff: "+ New Client" intake auto-scaffolds the minimal
  capability profile (today CLI-only `tools.capability scaffold`), so a fresh
  company reaches the Analyst Layer without terminal work.
- Demo declutter per ruling 2: retire refresh-press + delta UI, capture-brief
  baseline tile, strict evidence ledger board, "since your last approval"
  panel; add the single bottom Refresh button returning to the Analyst Layer;
  replace the dead /onboarding stub. Target door/rail, research picture, and
  full picture UNTOUCHED. Gate endpoints/settings unchanged.
- Report egress per ruling 4: generated Pre-Assessment renders clean (no DRAFT
  band), edit mode + HTML download always live for the generated artifact; no
  release step in the flow; certifier sidecar still written.
Acceptance: golden path clicks end to end on a fixture client with no terminal.

### Phase 5 (Sat-Sun): template control pass
Seal wiring into the report (normalize seal filenames), de-fingerprint audit of
downloaded HTML (no generator branding/comments/metadata per house style),
print/PDF pass, edit-mode QA (resize, drag/drop, reorder, undo), golden
screenshots for the three clients, blind-review check with the operator.
Acceptance: house-style acceptance checklist passes; operator says it reads as
an LILA product.

### Phase 5.5 (Sun): Ed ranking workbook
Interactive spreadsheet generated from pipeline data for the chosen company:
sheet 1 candidate opportunities (identity, agency, dates, links) with a fit
ranking column, sheet 2 keywords with fit ranking, sheet 3 NAICS with fit
ranking; dropdown validation + notes columns so William and Ed can score
together and the rankings can later calibrate inference. Generated, never
hand-authored.

### Phase 6 (Sun + Mon AM): dress rehearsal
Live pull for the demo client(s) through the Command Center so a FINISHED
report exists; walkthrough rehearsal (no live run in front of Ed): home ->
the input -> inference -> Analyst Layer story (NAICS/keyword workshops as the
client-sales-team collaboration surface) -> the finished Pre-Assessment with
scoreboard, ticker, Section 3 vehicles, calendar -> edit mode (seal drag/
resize) -> download-as-approval. Verify SAM quota headroom; Monday AM warm-up
run. Integrator merge to main when everything is green (target Sunday).

## Operator rulings (2026-07-23, William, in chat)

1. Merge topology delegated to the build session: build in the worktree,
   demo-capable at every phase; Integrator merge to main when green (target
   Sunday). The expectation is FINISHED in 3.5 days, not staged.
2. TARGETING STAYS VISIBLE (Target door + rail untouched; Apollo integration
   is the next build and integral). Retire instead: refresh press + delta UI,
   the capture-brief baseline tile, the strict evidence ledger board, and the
   "since your last approval" panel. ONE Refresh button at the bottom that
   returns to the Analyst Layer. Keep research picture view and full picture.
   The process is: input -> inference -> Analyst Layer (approve) -> search ->
   generate report. LILA produces ONE report: the Federal Opportunity
   Pre-Assessment (candidate review v1 document; UI step id stays
   `candidate_review` because the `pre_assessment` artifact family id is
   taken).
3. Monday demo = finished report + the logic that produced it. NO live full
   run in front of Ed. Demo-company choice deferred.
   NEW WEEKEND DELIVERABLE: an interactive ranking workbook (spreadsheet)
   William uses WITH Ed to rank (a) opportunity fit for a chosen company,
   (b) keyword fit, (c) NAICS fit, to train discernment. Generated from
   pipeline data, never hand-authored.
4. NO approval layer on the report. The run generates ONE fully editable
   artifact (text editing, seal/logo drag-and-drop, seal/logo resize) with a
   clean HTML download. No DRAFT band, no release switch in the flow: the
   download IS the approval. Certifier receipts remain as invisible sidecar
   integrity records.

## Standing constraints honored

Command-Center-only report builds (fixture renders in bash are the sanctioned
zero-LLM diagnostic path); no gate settings changed agent-side; LILA_LLM_ROUTE
untouched; contract surfaces shipped as isolated labeled diffs; done = suite
green + lint clean.

## Risks

- Event parser vs real organizer HTML (known; Phase 3 iterates on it).
- SAM quota during rehearsals (pre-dedupe + scoped pulls; watch the ledger).
- DRAFT banner in front of Ed if decision 4 stays DRAFT (honest, but decide).
- Port collision if main's Control Room and the worktree's run simultaneously
  (run one at a time or pass a distinct port).
- Uncommitted work until Phase 0 lands (highest severity, cheapest fix).
