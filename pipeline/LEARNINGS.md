# Pipeline Learnings

Institutional memory with teeth. Every entry is PROPOSED until a test or
collector enforces it, then ENCODED. The lint suite fails any entry left
PROPOSED for more than 30 days: an unenforced learning is a wish.

Format: date · status · insight · evidence · encoded-as.

---

## L1 · 2026-07-09 · ENCODED
**NAICS is a filing location, not a capability.** Lane-filtered evidence is
client-invariant: the same primes and dollars surface for any vendor.
Evidence: the Osprey GDIT teaming thesis ($893.7M, ranked #2 of 8) fell to
zero when subaward descriptions were capability-matched; 0 of 500 lane
records matched the client's vocabulary.
Encoded as: evidence inversion in `agents/reports/facts.py`
(`_teaming_facts`, `_recompete_facts`), profile gate in `run_searches.py`,
client-invariance check on every build (`client_invariance_check` +
`tests/test_capability_inversion.py`).

## L2 · 2026-07-09 · ENCODED
**Named incumbents are the highest-yield search key.** Searching product
names (ForeFlight, Jeppeson variants, Compusult, Dataminr) in award and
notice text finds buyers no capability keyword reaches.
Evidence: the CBP Air and Marine sole-source cluster and the NGA/Compusult
IGW4 J&A both surfaced only through named-product search; the buyer map
found 50 buying offices and 12+ displacement windows in one pass.
Encoded as: `tools/api/incumbent_buyers.py` (standing collector) +
`_buyer_map_facts` / `_incumbent_product_facts` + the Incumbent Buyer Map
report section (deterministic render).

## L3 · 2026-07-09 · ENCODED
**Verified negatives are billable findings.** "Zero capability-matched
teaming flow" and "do not pursue" are conclusions a client pays for, not
gaps to paper over.
Evidence: the unclaimed-space finding replaced a false GDIT thesis; the
IGW4 sole-source J&A (Compusult, proprietary Web Enterprise Suite, PoP
through 2030) is a resolved DO-NOT-PURSUE.
Encoded as: FINDING-style facts in `_teaming_facts`/`_recompete_facts`
(zero-match emits a fact, never silence); monitor facts carry verdicts;
the composer's zero-is-never-the-story rule.

## L4 · 2026-07-09 · ENCODED
**Every claim carries its evidence level.** A reader must always know
whether a line is capability-verified, component-matched, lane-level, or
manually compiled.
Evidence: three failed builds traced to unlabeled lane evidence wearing
verified framing; the $361M "government-wide median" was a top-50 slice.
Encoded as: mandatory un-suppressible labels (LANE-LEVEL EVIDENCE ONLY),
population labels on every statistic (R4), provenance tags on compiled
sections (R5/vehicles), DRAFT/RELEASE states with the QA appendix.

## L5 · 2026-07-09 · ENCODED
**Funded-demand dollar lines need full-text extraction.** The govinfo
search returns document-level citations only; program dollar lines require
downloading and parsing the full text of budget justifications.
Evidence: 1C scan found 4 core-term hits (incl. the Airspace Location and
Enhanced Risk Transparency Act of 2026) but could not quote dollar lines.
Encoded as (2026-08-18): the figure-labeling law the press already obeys.
A funded-demand hit renders as a document-level citation and never states
a dollar line the system did not parse: every quantitative claim must
cite a fact (`agents/reports/facts.py` provenance stamping, `lint_text`
citation validation), and the funded_demand lane's catalog boundary
declares document-level coverage. A full-text dollar-line parser remains
a welcome extension, not a precondition for honesty.

## L6 · 2026-07-09 · ENCODED
**Buyer-map spend needs award-portion attribution.** Keyword-matched awards
are counted at gross obligated value; a $7.5B vehicle mentioning one Garmin
line item inflates the buyer total. The population label discloses this.
Encoded as (2026-08-18): the disclosure law the press already obeys.
Buyer-map figures render under mandatory population labels naming the
gross-obligated basis (R4 population labels, L13 scoreboard populations,
`lint` battery enforcement), so the basis is stated on the figure and
never wears verified framing. Transaction-level attribution in
`tools/api/incumbent_buyers.py` remains a welcome refinement of the
number, not of the honesty.

## L7 · 2026-07-09 · ENCODED
**Chart elements at viewBox edges clip in rendering.** Glyph side-bearing
extends slightly negative, so text placed flush at x=0 (or any element at
the edge) loses pixels at render.
Evidence: the shipped Osprey money chart clipped end-anchored axis labels
(HII label bbox reached x=-173 against a 0-origin viewBox); the R13 scan
found 7 such breaches in the shipped report.
Encoded as: R13 safe-margin standard (`agents/reports/svg_safety.py`:
inset-by-convention viewBoxes, width-checked end labels, width:100%
overflow guard), deterministic viewBox auto-fix on every render,
`lint_svg_geometry` in the gate suite, and the exact failure as a
regression fixture (`tests/test_svg_safety.py`).

## L8 · 2026-07-10 · ENCODED
**A silent agency scope can manufacture a zero-opportunity cycle.** The
scope screen runs before triage; when it removes most of the haul, the
sweep reads as "no live demand." The correct response is a LOUD WARNING to
the operator, never an agent-side change: the NETSCOUT DHS scope that
looked like a configuration miss was in fact the deliberate engagement
scope (see L11 for the override failure).
Encoded as: scope-cut warning in `run_searches.py` (loud stderr + carried
in the artifact) and a SCOPE flag on any report built from a gutted sweep
(`run_capture_brief.py` early flags).

## L9 · 2026-07-10 · ENCODED
**SAM is the wrong primary pond for COTS reseller-channel OEMs.** Product
OEMs transact through vehicle task orders and quote channels (DoD ESI BPA
orders, SEWP RFQs, GSA eBuy, DIBBS) that never post to SAM.gov; their SAM
layer is structurally thin regardless of demand. The buyer map (93 offices,
66 displacement windows for NETSCOUT) and forecast/funded-demand layers
carry the real signal for this client class.
Encoded as (2026-08-18): the machinery now exists on both legs named.
The OEM-mode emphasis rule is `report_mode=vendor_new_business` (vendor
editorial mode, 2026-08-17). The vehicle rails are the award-schema store
(`tools/api/award_schema.py`: parent IDV identity and description,
prefix-classified vehicle, renewal clock) and the rival-paper store by
agency and office; the channel layer is the `esi_reseller_catalogs` mesh
lane with the USASpending IDV fallback (the Varonis ESI BPA
N6600122A0080 was confirmed that way while esi.mil stayed walled).
Tests: `tests/test_award_schema.py`, `tests/test_source_sweep_roster.py`.
The eBuy quote channel stays a receipted gap (credentials never present),
named in every sweep rather than silently absent.

## L10 · 2026-07-10 · ENCODED
**The client file never grades our own prior work, and a negative never
renders as a negative.** Unverified findings get no chip, row, caveat, or
footnote in the client file: they are deleted there and live in the
internal file. Verified negatives convert to forward actions (four notices
with no record become "stand up a live watch on those postings").
Evidence: operator doctrine ruling, 2026-07-10, after client files carried
verification caveats, pending-verification rows, and screen-credential
language.
Encoded as: composer + editor CLIENT-FILE DOCTRINE blocks, R14 deterministic
backstop in `agents/reports/remediation.py` (self-grading sentences deleted,
wholly-negative elements dropped, stats flagged), R11 lane claims deleted
rather than caveated, template trims (standing-watch phrasing, pending list
internal-only), and `agents/reports/internal_file.py` writing
`<slug>.federal_opportunity_assessment.internal.md` with every removed item
and its forward action.

## L11 · 2026-07-10 · ENCODED
**Gate choices are operator decisions; the agent surfaces evidence and
asks, it never overrides.** Scope, approvals, and release switches encode
what the ENGAGEMENT is, which data cannot determine: a strategy naming DoD
does not mean the meeting is a DoD meeting.
Evidence: the NETSCOUT DHS-only scope was overridden agent-side as a
"configuration miss"; the resulting all-scope assessment led with DISA/DoD
pursuits and could not be shown in a DHS-scoped engagement. The principal
was, correctly, angry.
Encoded as: standing feedback memory (never change search_scope or any
gate setting agent-side; warn and ask), scope restored via the operator
API, and the agency-report path (`run_agency_report.py`) as the correct
tool when a scoped artifact is needed from a broad sweep. All scope_change
events remain journaled for audit.

## L12 · 2026-07-10 · ENCODED
**Scope is a lens, not a filter.** The sweep and the scoreboard always hold
the whole market; an agency focus is recorded at the gate and applied at
report time as a projection (scoped breakdown, component-level headline,
scoped buyer map). A focused engagement loses nothing.
Evidence: the gate scope's pre-triage cut turned NETSCOUT's 300-notice haul
into 10 and manufactured a zero-opportunity cycle; separately, the main
sweep's market evidence was silently scoped to the first target agency.
Encoded as: `run_searches.py` records focus without filtering (notices and
market evidence collect government-wide); `run_agency_report.py` projects
the focus (component breakdowns, scoped addressable, scoped buyer map);
the scoreboard renders the full picture with its derivation.

## L13 · 2026-07-10 · ENCODED
**Scoreboard lines must draw count and examples from the same population;
cross-population arithmetic is forbidden.** A count from one population
rendered beside names from another produces a self-contradicting line, and
subtraction across populations produces silent negatives.
Evidence: the internal scoreboard printed "0 competitors and incumbents
identified" (the product-gated counts() figure) then named Booz Allen, SAIC,
and Dell (the first rows of the raw 33-prime lane population); the same line
computed "and X more" as 0 - 3 and hid it behind an if-positive guard.
Encoded as: `_pop_figure` / `_internal_competitor_block` in
`agents/reports/views.py` (each figure's count IS its population length;
one label per company, incumbent > product > lane), the buyer-map incumbent
resolver + shared `company_matches` in `agents/reports/document.py` (one
matcher for tagger and resolver, so the paths cannot drift),
`lint_scoreboard_populations` in `agents/reports/lint.py` (render-time check,
warn-only at the banner endpoint), and `tests/test_scoreboard_populations.py`.
counts()["competitors"] stays product-gated: client copy never counts lane
furniture as competitors.

## L14 · 2026-07-10 · ENCODED
**Corporate name variants, M&A lineages, and DBAs resolve to one canonical
door before any report renders.** USAspending names entities as of the
contract, so a trailing window lists a legacy name and its acquirer as two
primes; presented unmerged, one corporate door renders as two teaming
targets with split dollars and wrong ranks.
Evidence: the 2026-07-09 Osprey brief listed Perspecta ($938.7M) and Peraton
($559.6M) as separate targets; Perspecta has been Peraton since May 2021.
The 2026-07-10 sweep inventory carried live variants: PERSPECTA ENTERPRISE
SOLUTIONS beside three Peraton entities, two Booz Allen punctuation
variants, CSRA beside GDIT, ENGILITY beside SAIC, L3 TECHNOLOGIES beside
L3HARRIS.
Encoded as: `data/entities/crosswalk.json` (54 canonical doors, every alias
row source-cited, seeded 2026-07-10), `tools/entity_lineage.py`
(`resolve_entity`/`door_key`/`normalize_company`/`company_matches`: one
matcher and resolver for every company-name path; unresolved names pass
through and log to `data/entities/unresolved.log` so the crosswalk grows
from real traffic), door-keyed merges in `_build_competitive` and
`_capability_prime_ranking` (dollars summed, lineage note rendered once),
`lint_entity_lineage` in the report lint battery (two rows resolving to one
door without a merge FAIL the build), and `tests/test_entity_crosswalk.py`.

## L15 · 2026-07-10 · ENCODED
**Agency acquisition forecasts are a first-class, PROGRAM-tier fact source;
the revision is itself a signal.** Forecast records predate SAM notices by
6-24 months but are agency-stated intent: they never render as live
opportunities, and a line whose timing moves closer, whose value band grows,
or which disappears (often = went to solicitation) is a citable change signal.
Evidence: the 745-record DHS APFS pull for Osprey (72 clearing the NAICS
screen) proved the pre-RFP layer's yield; the pull was one-source and
hardcoded until now.
Encoded as: multi-source adapter loop in run_searches (`forecast_sources()`;
adding an agency = one file + one decorator), the global store with
record_hash/first_seen/last_seen + signal-event classification
(`tools/api/forecasts/store.py`), the coverage ledger with honest gap logging
(`coverage.py`; DoD/State/FAA/NASA gaps standing), three-bucket capability
screening + the screening-stats line (`matching.py`), `Fact.tier="program"`,
horizon bank consumption of change events, `lint_forecast_context`
(pre-existing) + `tests/test_forecast_recompete.py`. The Acquisition Gateway
adapter ships wired but OFF: its backend is network-gated (findings in the
adapter docstring).

## L16 · 2026-07-10 · ENCODED
**The recompete calendar is a standing product, not an ad-hoc pull: expiring
awards ranked by DECOMPOSABLE attack value, dollars labeled, notices gated.**
USASpending PoP end dates are the recompete schedule; every score must say
why in citable terms; obligated and ceiling are never conflated; the client's
own paper segregates to DEFEND; and expiry math NEVER implies a notice
posted — that claim is NOTICE-tier and needs the live SAM gate.
Evidence: the Osprey GDIT "expires Aug 31, 2026" finding was produced by
hand; NETSCOUT's first standing calendar carries 104 expiring awards inside
24 months with per-factor decompositions.
Encoded as: `tools/api/recompete.py` (pull/score/store; crosswalk-resolved
incumbents; weights in `data/reference/recompete_weights.json`, never code),
`run_recompete.py` + dashboard step "recompete" + internal calendar view,
top-N feed into `_recompete_facts` (tier=program), `lint_notice_tier_claims`
in all three report batteries, and `tests/test_forecast_recompete.py`.

## L17 · 2026-07-10 · ENCODED
**A screening count that never varies is a ceiling, not a census;
screened-N claims must reconcile against totalRecords.** Every NETSCOUT-era
sweep reported exactly 300 notices: MAX_RESULTS=300 in the extract screen
truncated matches mid-file in CSV order (born in the initial commit, no
dedicated rationale), and the live-API fallback fetched one page at
limit=25 with totalRecords never read.
Evidence: the pre-fix NETSCOUT artifact holds exactly 300 notices (282
discard / 16 monitor / 2 pursue); the same query un-capped returns the true
lane census, and the live API reports totalRecords the old code ignored.
Encoded as: extract census (`last_census`, no result cap), live-API
pagination to totalRecords with the per-key daily budget guard
(`sam_quota.guard`, warn at 80%), INCOMPLETE pulls recorded in the coverage
ledger and reconciled in copy as "screened X of Y" with the QA-appendix
flag, `lint_screen_census` (partial screen rendering as complete fails the
build, wired into all three batteries), and `tests/test_sam_census.py`.

## L18 · 2026-07-10 · ENCODED
**Filter-first, free context (operator decision): every metered or
LLM-priced call honors the gate focus; whole-market context lives only in
the free layers.** Supersedes L12's collect-everything implementation while
keeping its lesson: the census states the whole market (free extract
screen), so a focused slice can never silently read as a dead market — but
triage, SAM-metered calls, and downstream composition pay ONLY for the
agencies the operator chose. A department focus captures its components.
Re-focusing an engagement means a new sweep, not a free projection; that is
the accepted trade.
Evidence: 36 hours of whipsaw between the two extremes — the July 9 DHS
pre-triage cut manufactured a zero-opportunity cycle (L11/L12); the
collect-everything correction then produced 619-notice full-market triage
runs and a synthesis blind to the operator's focus, at unsustainable credit
burn. The operator called the shape: "search the APIs with the scope in
mind."
Encoded as: SourceQuery.agencies threaded from the gate before the fan-out
(run_searches), focus filtering in the extract screen (slice to triage,
market in last_census) and the live-API path (tools/api/sam_extract.py,
tools/api/sam_gov.py), the census log line stating "focus slice X of Y
market-wide", and tests/test_sam_extract.py::
test_focus_filters_paid_flow_census_keeps_market.

## L19 · 2026-07-10 · ENCODED
**A non-all scope carries the agency designator through every artifact it
touches; the unqualified Federal Opportunity Assessment name is reserved
for scope=all.** Per-agency runs coexist as separate artifact families
(sweep, draft, QA sidecars, report, internal file, Desktop delivery,
snapshots), so a DHS run produces the DHS assessment and can never
masquerade as, or overwrite, the all-market one. Resolution is gate-driven
and LOUD: a scoped gate whose artifact is missing refuses with the command
that produces it, never a silent fallback to the wrong universe.
Evidence: post-L18 the first scoped sweep would have overwritten
searches_<slug>.json and every downstream reader would have consumed a DHS
slice as the whole market; the discovery map (4-agent fan-out) found 36
sweep readers, 57 output-name sites, 33 dashboard globs, and the snapshot
stores a scoped run would have cross-contaminated.
Encoded as: scope_designator/gate_designator/sweep_artifact_path/
artifact_stem in agents/review.py (grammar 'agency_<abbr>' in dot-segment
2, the existing shelf-parsed convention); designator minting in
run_searches; gate resolution in every step reader and the fact-pack/
document default loads; stem-derived output families in run_capture_brief
and run_views; scoped watchlist snapshot lineage in document.py;
agency-report direct-consume convergence; dashboard gate-following
(_gate_designator/_sweep_name/_stem in ui/server.py); and
tests/test_scope_designator.py.

## L20 · 2026-07-11 · ENCODED
**An official publisher is not automatically the procurement buyer, and an
official signal is never a live posting.** Horizon discovery keeps
Federal Register, Regulations.gov, GAO/IG, CISA KEV, budget, and forecast-store
changes in a separate PROGRAM-only registry. Each pull records coverage, but
only the unchanged Horizon evidence gate can promote a row into an analyst
thesis. CISA KEV remains government-wide context unless an affected buyer is
named, and a disappeared forecast line is described only as a source change.
Encoded as: `agents/reports/horizon_discovery/`, PROGRAM mappings in
`agents/assess/ledger.py`, and `tests/test_horizon_discovery_factory.py`.

## L21 · 2026-07-11 · ENCODED
**A partner recommendation is actionable only when it travels with the exact
pursuit, while candidate identity remains paid content.** Pursuit and dossier
cards now reuse the existing partnering renderer for direction,
evidence-graded candidates, and cited rows. The sales copy retains the path
shape and verifier line, but candidate identities are removed from the entire
gated model rather than hidden by template conditionals.
Encoded as: opportunity-plus-rank joins in `agents/reports/views.py`, physical
candidate redaction in `gate_for_sales`, and all-view/standalone/leak coverage
in `tests/test_partnering_board.py`.

## L22 · 2026-07-11 · ENCODED
**A report cutover must be evidence-bound, reversible, and loud on drift.**
The presence of one scope-specific current Assess pointer activates strict Live
SAM truth only for that exact client, scope, sweep, profile, and projection
input set. Absence keeps the legacy path; an invalid or expired pointer holds
the live lane closed; explicit pointer removal rolls back. Posting-level
verdict provenance and solicitation-level pursuit dedupe remain distinct.
Required-source gaps can be released only by an operator approval bound to the
exact visible blocker manifest, so evidence changes revoke the exception.
Encoded as: `agents/assess/live_report.py`, the shared approval release leg,
evidence-bound partial-release UI/API, offline parity reporting, and unstubbed
coverage in `tests/test_live_report_truth.py`.
The same boundary rejects stale displayed blocker/run pairs, uses one UTC
deadline cutoff, discloses accepted-posting census shortfalls, and treats HTML
not newer than the current strict pointer as a pre-cutover artifact that must
be rebuilt and QA-certified. Identical run persists do not advance the pointer.

## L23 · 2026-07-12 · ENCODED
**A cutover decision is made row by row, not from aggregate parity alone.**
Every legacy board, watchlist, and monitor exit or reclassification now carries
the strict eligibility leg and the row's identifying facts. Solicitation-family
identity distinguishes an older amendment superseded by the current posting
from a real eligibility failure. Posting-level verdict transitions reconcile
the totals. A generic or missing explanation, or any unreconciled delta, holds
cutover eligibility closed. The packet remains offline and never activates a
pointer.
Encoded as: the embedded JSON decision packet and Markdown renderer in
`agents/assess/parity.py`, family-aware amendment reconciliation, and
unstubbed packet coverage in `tests/test_cutover_decision_packet.py`.

## L24 · 2026-07-12 · ENCODED
**A recurring intelligence digest needs a source-bound baseline for each lane,
not a single remembered run.** A source that appears for the first time is
baselined without fabricated entries; a scope change rebaselines every lane;
and a temporarily unavailable source retains its last comparable state. SAM is
census context, forecasts and recompetes are PROGRAM planning context, and
Horizon motion comes from the complete validated PROGRAM fact bank rather than
the current analyst narrative. None of those changes establishes a live
posting.
Encoded as: `agents/change_digest.py`, exact gate/sweep binding, read-only
forecast-store and recompete provenance cursors, stable Horizon evidence
identity, the internal Control Room surface, and adversarial coverage in
`tests/test_change_digest.py`.

## L25 · 2026-07-12 · ENCODED
**Downstream QA may share a run identity, but it must not create a second
release authority.** One fail-closed read seam now validates the exact current
pointer, immutable run, mutable binding, and projection inputs before returning
the run id, pointer digest, blocker-manifest digest, and pointer persistence
time. Absence is a quiet `None`; invalid or drifting state is logged and also
returns `None`. The result carries identity, never approval or release state.
Encoded as: `agents.assess.current_run_identity`, its frozen four-key contract,
and unstubbed pointer, drift, corruption, and race coverage in
`tests/test_assess_run_identity.py`.

## L26 · 2026-07-12 · ENCODED
**A report date and a deadline cutoff are different clocks.** The assessment
date tells the reader which local operator day produced the document; federal
date-only eligibility remains conservative on the shared UTC day. Conflating
them advanced the cover and FactPack provenance stamp for every pointer-less
evening build. Deadline grades, rankings, and relative sales-window copy remain
on UTC by deliberate operational doctrine; they no longer supply the report's
display date. An explicit `as_of` remains one exact replay date for both
meanings.
Encoded as: the two-clock defaults in `build_document` and `build_fact_pack`,
pursuit-grade cutoff propagation into sales gating, host-local normalization
of aware sweep timestamps in internal QA, and evening local-versus-UTC
ABSENT/CURRENT regressions in `tests/test_live_report_truth.py` and
`tests/test_views.py`.

## L27 · 2026-08-04 · ENCODED
**When the free daily extract goes dark, the departures are still
recoverable; the days are not.** GSA's nightly
ContractOpportunitiesFullCSV.csv published a header-only 685-byte object from
2026-07-29 through at least 2026-08-04. Diagnosis proved the URL, naming, and
location did not change (Data Services tree, FSD KB, data.gov, and the
mediated download endpoint all still name the same key; no date-stamped daily
snapshot exists on falextracts; prior S3 versions are not anonymously
readable), so a floor refusal means "empty at the source", never "go hunt for
a moved file". The weekly FY archive carries the identical 47-column schema
with full descriptions, so every notice that DEPARTED during a gap is
recoverable, bucketed on its own departure day so first_seen/last_seen keep
meaning what they say; notices posted in the gap and still active arrive
whole with the first healed daily. The size floor stays exactly as it is:
it is the thing that turned this outage into staleness instead of a
fabricated mass closure.
Encoded as: `tools/sam_archive_backfill.py` (fetch floor, departure-day
slicing, per-day as_of ingest, explicit floor_bytes for slices only), the
unchanged one-owner floor in `tools/notice_store.py`, and hermetic coverage
in `tests/test_sam_archive_backfill.py`.
