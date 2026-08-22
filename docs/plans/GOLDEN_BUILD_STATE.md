# GOLDEN_BUILD_STATE · updated 2026-07-24 (Phase 0 complete)

Plan: docs/plans/GOLDEN_BUILD.md (frozen scope). Branch: `fable/golden-build` off main @ 5d85f10.
Command Center is LIVE at http://127.0.0.1:8321 serving THIS checkout.

## Phase 0 - PREFLIGHT: COMPLETE

### a. Composer LLM call audit
- The golden flow's composer (`agents/candidate_review_v1/composer.py` + `authoring.py`) is
  DETERMINISTIC: zero LLM calls ("makes no network or model call"); the Max-plan enrichment
  adapter raises `SeedAuthorLaneNotImplemented`. This is the diagnosed failure; the fix IS
  Phase 3 (composer replacement), built fresh on `maxplan_cli.run_claude` free-text transport.
- Other composers (not the golden flow): capture-brief sectioned composer = claude-opus-4-8,
  JSON-schema-constrained per section, max_tokens 8000 on api route only (ignored on max
  route); V4.2 signal-board `compose_line` = opus, free text, per-line.
- Frontier model for Phase 3: `claude-fable-5` VERIFIED ALIVE through `run_claude`
  (39.5s round trip, correct reply). No token cap exists on the max route; timeout is the
  only budget knob (LILA_LLM_TIMEOUT_S; pass a large per-call `timeout_s` for the big
  compose call). Temperature: not settable on max route (CLI). Compose call will be
  free text (NO JSON schema), per plan.

### b. Per-lane access verdicts (probed live 2026-07-24 ~10:55-11:00 PT)
- USASpending POST /api/v2/search/spending_by_award/ -> HTTP 200 (0.4s). LANE LIVE. No hard stop.
- Anthropic route (max-plan CLI) -> ALIVE (claude-fable-5). No hard stop.
- SAM.gov /opportunities/v2/search with pooled key -> HTTP 429 Too Many Requests (daily
  quota exhausted right now; single key in .env, SAM_GOV_API_KEY_2 not set). Per table:
  PROCEED, L1 DEGRADED. Golden dock has zero SAM records; L1 cannot block golden density.
  L1 will be attempted live at press time; 429 stays a reported degradation, never padded.
- DHS APFS (apfs-cloud.dhs.gov) -> HTTP 200 (1.7s), LILA_ENABLE_DHS_APFS=on, adapter
  already exists at tools/api/forecasts/dhs_apfs.py. L4 LIVE via existing adapter.
- OpenAI -> NO key (.env has none; only .env.example placeholder; collaboration key is
  session-injected via UI and none is stored). Per table: critic loop = ONE Anthropic
  self-critique pass with the same output contract (exactly "PASS" or numbered fix list).

### c. Golden reference
- Copied to fixtures/golden/riverbed_golden.html (404,131 bytes).
- CHOICE MADE (flag for Tyler): three differing versions existed in ~/Downloads. All three
  share the IDENTICAL 18-record evidence core (14 USASpending + 4 APFS URLs, zero SAM) and
  identical 9-band section grammar (01-09 markers). Chose the NEWEST/largest
  "...(2).html" (08:06, 404KB) because it alone contains the CLOCK ticker band = the L3
  "research clocks" lane named in the plan; the exact-name file (07:56, 286KB, identical
  copy in the Codex worktree outputs/) lacks it. Swap = re-point one fixture + re-split.

## Environment facts for later phases
- LLM routing: `agents/decisions/maxplan_cli.py::run_claude`; LILA_LLM_ROUTE=max default;
  ANTHROPIC/OPENAI keys stripped from CLI subprocess by design. Engine schema calls are
  Pydantic-validated client-side; the golden composer will use run_claude directly.
- Research stage: run_intake.py -> agents/company_research.py::research_company()
  (claude-sonnet-5 research engine; _site_worker = httpx scrape via tools/scrape/site.py;
  _web_worker = engine.web_research() = CLI WebSearch tool).
- Candidate-review flow = the golden flow (the golden reference is its hand-finished
  output). Its retrieval/lanes map is being confirmed; see Phase 2 notes when written.
- Tests: max-route mocking pattern = monkeypatch maxplan_cli.run_claude
  (tests/test_maxplan_route.py); offline doctrine, no LLM in tests.

## Phase 1 - RESEARCH STAGE EMITS ENTITIES: CODE COMPLETE (live run in flight)

- `agents/decisions/schemas.py`: new `ResearchEntity` model (name, kind
  product|competitor|reseller, rationale, source) + additive
  `IntakeStrategy.research_entities` (default empty; legacy packets load unchanged).
- `agents/decisions/intake.py`: SYSTEM_PROMPT now instructs grounded entity
  extraction (exact proper names, empty-when-none, no padding; NO client-specific
  names anywhere). `GeneratedIntakeStrategy` validator: nonblank name+rationale,
  no duplicate (kind, name) pairs — new generations only; stored packets unaffected.
- `revise()` merge preserves the field (it touches EDITABLE_FIELDS only; proven by test).
- Tests: 4 new in tests/test_intake.py (emission+prompt, blank/dupe rejection,
  legacy-load, gate-revision survival). Intake/revision/workshop files: 68 passed.
- Live verification: scratchpad driver runs research_company()+build_strategy()
  for riverbed (same code path as run_intake.py, NO persistence — operator packet
  untouched). Output -> data/state/golden_build/riverbed.research.raw.json.

## Test baseline (recorded before any edit)
Full suite: 5 failed, 3138 passed, 1 skipped. The 5 reds are SAM quota/throttle
tests (test_candidate_review_v1_live_providers x3, test_sam_census,
test_sam_reliability) — pre-existing, none in files this build touches; SAM daily
quota is exhausted until 2026-07-25 00:00 UTC which is also why L1 is degraded today.

## Files touched so far
- docs/plans/GOLDEN_BUILD.md (new, committed 84051f3)
- fixtures/golden/riverbed_golden.html (new, uncommitted)
- agents/decisions/schemas.py, agents/decisions/intake.py, tests/test_intake.py
- docs/plans/GOLDEN_BUILD_STATE.md (this file)

## Plan v2 (operator update, mid-build)
docs/plans/GOLDEN_BUILD.md replaced with the operator's v2 (commit 3872e1e).
Four deltas confirmed: Phase 3c linkage law (anchors from pack source_url via
canonical builders), Phase 3f link check, sanctioned "Federal opportunity
calendar" section before the Evidence dock, DoD adds click-resolving ids +
calendar completeness. All Phase 3/DoD scope; pack records already carry
canonical source_url per record (agents/reports/links.py builders for
SAM/USASpending; DhsApfsSource DETAIL_URL for APFS).

## Phase 2 - RETRIEVAL LANES: BUILT, live-tuned (TIMEBOX OVERRUN, reported)

Overrun cause: first live run exposed four generic defects fixed in place:
(1) screen vocabulary contained entity names (the approved keyword list
carries product names), so entity searches self-matched: steelhead-the-fish
rows passed the screen. screen_vocabulary() now strips entity names from the
capability-context vocabulary. (2) product-suffix terms under 4 alphanumerics
("NPM+" -> NPM) matched unrelated acronyms (NPMS pipeline mapping); tightened.
(3) selection ranked dollars over corridor diversity and recency; now core =
best record per corridor first (sub-agency granularity) then clock+dollars
fill, competitors = corridor-first then most-recent then dollars, 2 per term.
(4) L4 match_forecasts hard profile gates + phrase-only keywords scored the
golden-class programs at NAICS-only level; replaced with golden-local
score_forecast (phrase*3 + content-unigram + exact/family NAICS, keep>=3,
value-magnitude tiebreak). NEVER-CUT items intact: L2, composer, validator.

Module: agents/golden_press/{records,retrieval,recall}.py.
Pack artifacts: data/state/golden_build/riverbed.{research.raw,evidence_pack,
universe,recall}.json.

Live tuning arc (7 takes): 8/18 -> 6/18 -> 12/18 -> 14/18 -> FINAL 15/18.
Landed rules, all generic and disclosed in pack.selection:
- Engagement scope enforced pull-side (riverbed = civilian preset, DoD
  excluded by the operator artifact; exclude-only; off-scope counted).
- Core: clocked corridor reps -> seven-figure clocks -> ONE dollar-ranked
  anchor pool (mega-anchor = >=$5M and >=5x corridor's clocked rep) ->
  clock+dollar fill; cap 26; corridor key = sub-agency.
- Competitors: round-robin 3 per rival (best-rank order), corridor ->
  clock -> dollars; cap 10.
- L4: phrase + capability-frequency x normalized-corpus-IDF unigrams +
  title boost + NAICS bonus, keep>=3 with keyword signal mandatory;
  VALUE-FIRST ranking; cap 8.
- Screen vocabulary excludes entity names (self-match leak: steelhead fish).
- Client term paged 6 deep (recall backbone for common-noun client names).

FINAL PACK (take 7): 44 records = 26 core + 10 competitor + 8 forecast;
26 research clocks; sufficiency MET (L2+L3+L4); L1 degraded (SAM quota,
resets 2026-07-25 00:00 UTC; sweep notices carried zero full-phrase hits so
L1 kept 0 - golden has zero SAM records, no recall impact).

RECALL 15/18 (12/14 awards, 3/4 forecasts). Misses, each explained:
1. SSA Gigamon 28321326FDX030101 - Gigamon never surfaced in either live
   research run (no-hardcode rule = never queried). Tyler's fresh intake
   today is a third roll; competitor directive now asks for rivals.
2. FAA Dynatrace 697DCK25F00873 ($170K) - retrieved, in universe; outranked
   by THREE newer larger Dynatrace entrenchments post-dating the golden
   (SSA $13.4M, CBP $6.3M FY26 base, TSA $1.1M FY26) under the disclosed
   round-robin; a live-data delta.
3. Cribl APFS 74529 ($2M) - keyword-qualified but below eight larger
   programs under value-first ranking; niche small-dollar product buy.

Phase 1 live note: run 1 surfaced 0 competitors (research directive never
asked); fixed generically (competitive-landscape question in
_COMPANY_WEB_SYSTEM). Run 2: 15 entities incl. SolarWinds + Dynatrace with
analyst-comparison rationales. Gigamon did NOT surface in either live run =
reported gap, bounds award recall at 13/14 non-forecast.

## Commits on fable/golden-build
84051f3 plan v1 · 3872e1e plan v2 · 880dab1 Phase 1 · cb651bd Phase 2 lanes ·
553f700 curation + clone-test pin · 730ee1c final selection + IDF L4 (15/18)

## CHECKPOINT 1: DELIVERED, awaiting Tyler's external verification
Deliverables in the session message and on disk:
- deduped record list -> data/state/golden_build/riverbed.evidence_pack.json
- raw queries per lane -> pack.queries (verbatim bodies, 26 executed)
- raw research output -> data/state/golden_build/riverbed.research.raw.json
- recall + misses -> data/state/golden_build/riverbed.recall.json (15/18)
- screened-universe forensics -> data/state/golden_build/riverbed.universe.json

## Checkpoint 1 VERIFIED (operator): PASS at 15/18, misses adjudicated.
Ratified: 08:06 "(2)" golden; civilian scope pull-side; pack of 44; C5ISC
clusters as one family in composition.

## Post-verification selection modification (operator-directed)
L4 adds up to 2 ADDITIVE reserved niche-signal slots: sub-$5M (inclusive)
score-qualified forecasts the value-first cap would drop, ranked by
forecast_rarity (normalized IDF of the rarest capability unigram hit), then
score. Main value-first picks are never evicted. Disclosed in
pack.selection.niche_forecast_slots. Live result: Cribl F2026074529 packs
via slot 1 -> RECALL 16/18 (12/14 awards + 4/4 forecasts). Remaining misses
= the adjudicated pair (Gigamon research gap; FAA Dynatrace outranked by
three newer, larger Dynatrace positions). Pack now 46 records
(26 core + 10 competitor + 10 forecast).

## Phase 3 - COMPOSER REPLACEMENT: BUILT (38 tests green)
- agents/golden_press/skeleton.py: byte-reversible split (content region =
  sections id=forecast..id=evidence; hero/ticker/chrome/assets inherit
  verbatim in the skeleton; embedded portrait/seal data-URIs elide to
  data:,LILA-ASSET-n placeholders for the prompt and restore mechanically).
  Split round-trip and elide/restore round-trip are test-pinned.
- agents/golden_press/compose.py: ONE free-text claude-fable-5 compose
  (timeout 2400s, no schema) with the linkage law, the calendar section
  contract, no-em-dash, evidence-only rules; Anthropic self-critique
  (PASS / numbered fix list, nothing else) max 3 loops; a single
  validator-driven revision entry point.
- agents/golden_press/validate.py: mechanical battery per plan v2 3f:
  sentinel, tag-balance well-formedness (calibrated: the golden itself
  passes), dollar provenance (verbatim values + derivable aggregates: lane/
  corridor/clock/entity sums, forecast bounds; display-precision tolerance),
  date provenance (ISO, D-MMM, FY, quarter forms vs pack dates), contract-
  number provenance, repeated-prose (48+ char normalized fragments),
  dock exactly-once, LINK CHECK (href == pack source_url), calendar
  completeness + placement, lane representation, em-dash. One revision
  attempt then FAILS LOUDLY with .FAILED.html + violation sidecar.
- agents/golden_press/press.py: orchestrator; writes
  data/state/candidate_review_v1/<slug>/<slug>.golden_report.html +
  .evidence_pack/.critique/.validation/.recall.json sidecars, prints
  [out:golden-report] and the /report?path= Command Center link.
  ENTITY BRIDGE (2026-07-24 only, loud + disclosed in pack.research
  .entities_source): a packet approved before Phase 1 carries no research
  entities; the press uses the golden_build dev research artifact when
  present. Tyler's fresh intake tonight writes entities into the packet and
  the bridge goes inert.

## Phase 4 - CC WIRING: DONE (no server change)
run_candidate_review.py STEP 15 presses the golden deliverable after the
existing steps (--no-golden opts out for diagnostics). The deterministic
composition still runs for its projections/DRAFT; the golden press failure
fails the job loudly. Served via /report?path= (existing route).

## LIVE PRESS IN FLIGHT
Sanctioned path: POST /api/run {Riverbed, candidate_review} -> job
f9e10b14fa1c (log: data/state/job_logs/f9e10b14fa1c.log). Checkpoint 2
deliverables on completion: report file, critique transcript per loop,
validator output, recall score. STOP for Tyler's external diff.

## CHECKPOINT 2: DELIVERED 2026-07-24 16:47 PT (press take 4, job 55c018602a5e)

REPORT SHIPPED: data/state/candidate_review_v1/riverbed/riverbed.golden_report.html
(462,816 bytes; nine bands in exact golden order incl. the sanctioned calendar;
sentinel present; skeleton inherited verbatim: hero, ticker, 29 identity assets).
Command Center link: /report?path=data/state/candidate_review_v1/riverbed/riverbed.golden_report.html

Press arc (loud-fail machinery earned its keep):
- take 1 FAILED: validator measurement bugs (id-in-href dock counts, APFS
  asterisk id forms) + real find (composer renamed bands) -> band-grammar
  check + visible-text/href measurement.
- take 2 FAILED: one-item fix list drew a 1,501-char patch reply that
  replaced the intact draft; critique judged a fragment and diverged ->
  acceptance gate (nine bands + length floor, one reminded retry, rejected
  revisions never replace intact drafts), affinity-tier dollar aggregates,
  US-format pack dates, per-stage draft persistence.
- take 3 FAILED at compose: full region exceeds one response chunk; the CLI
  auto-continues and --output-format json returns only the FINAL message
  (head lost mid-attribute, twice, gate-caught) -> compose-local
  stream-json transport concatenating every assistant chunk (8e98f75).
- take 4 SHIPPED: compose attempt 1 complete at 110,686 chars; critique
  loop 1 PASS; validator 21 violations (corridor-core cluster sums outside
  the aggregate families + future-only calendar) -> single revision
  (115,461 chars) -> VALIDATION CLEAN (ok=true, zero residual).

RECALL (in-press, approved-packet vocabulary): 15/18. Misses:
1. SSA Gigamon 28321326FDX030101 - adjudicated research gap (never surfaced,
   never queried; fresh intake re-rolls).
2. FAA Dynatrace 697DCK25F00873 - adjudicated live-delta (three newer larger
   Dynatrace positions outrank it under the disclosed band).
3. APFS 70806 CBP Cyber - the APPROVED PACKET's keyword vocabulary (not the
   dev research artifact's) drives L4 IDF scoring; 70806 lands one slot
   below the value window under it. Tonight's fresh intake regenerates the
   vocabulary; the dev-run vocabulary kept it (16/18 on the dev pack).

Entity bridge ACTIVE and disclosed in pack.research.entities_source (packet
predates Phase 1); goes inert on fresh intake. Deferred per speed directive:
full-suite re-sweep; SAM quota resets 2026-07-25 00:00 UTC (L1 rejoins).
Branch fable/golden-build through 8e98f75; UI workstream isolated to its own
worktree per operator.

## RED HAT FULL PRESS: SHIPPED 2026-07-24 ~17:58 MDT (first fresh client end to end)

Operator directives executed: all_federal scope artifact
(clients/red_hat/engagement_scope.json, preset confirmed all_federal); fresh
live intake with NATIVE entity persistence (19 entities: 7 products, 7
competitors, 5 resellers; packet data/review/red_hat.review.json); gate
auto-approved per explicit operator authorization (reviewer note carries the
directive provenance; applied via agents.review.decide directly so the
one-gate auto-press hook never double-ran); ENTITY BRIDGE INERT
(pack.research.entities_source = "packet").

Client-identity press change (operator item 3, commit f4b9913): hero, ticker,
signal strip, and the Next-steps aside are COMPOSED client surfaces now
(content region = sb-hero through </main>); hero_missing is a validator
violation and an acceptance-gate condition; non-golden slugs get an
exact-count deterministic skeleton branding pass (title, meta, report id,
download name, brand word; header logo slot neutralized for the operator's
mark). Golden-day chrome retired for all future clients.

Press: retrieval 1,437 screened -> 46 packed (36 L2 + 10 L4, 35 clocks,
sufficiency MET, 0 off-scope under all_federal); compose attempt 1 complete
(streamed transport); critique loop 1 = 3 fixes, loop 2 = PASS; validator 22
violations (12 dollar-aggregate shapes, 7 repeated ticker/calendar label
pairs, 2 dates, 1 calendar omission - ALL inside the composed region, zero
in skeleton chrome) -> single revision -> VALIDATION CLEAN (ok=true, 0
residual). Report: data/state/candidate_review_v1/red_hat/
red_hat.golden_report.html (478,696 bytes; Red Hat title/brand; only
Riverbed strings remaining are two dead JS fallbacks overridden by branded
attributes). L1 degraded-disclosed (pressed before 00:00 UTC quota reset; no
sweep artifact exists for a fresh client; quota now fresh for tomorrow's
sweep). Standing fix authorization: branches untriggered (validation
cleared); the spare sanctioned press was not needed.

Deferred: full-suite re-sweep; live-L1 assembly for fresh clients; CC step
wiring for fresh-client presses (candidate_review chain requires profile/
sweep/watch substrate a new client lacks; tonight ran via session driver
under explicit operator directive).
