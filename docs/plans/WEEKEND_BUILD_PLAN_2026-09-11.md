# LILA weekend build plan: September 11-13, 2026

Plan ID: LILA-WEEKEND-2026-09-11-v1
Status: committed direction and acceptance plan; implementation completion requires receipts.
Owner: root LILA build task, reconciled with LILA Benchmark Coordinator on September 11.
Scope: LILA builds and build handoffs. Existing benchmark repair sequence remains authoritative.

## Outcome and authority

Improve the leads LILA generates through its ordinary pipeline: company
understanding, original buyer requirements, evidence-backed product fit, usable
POCs and partner routes, a current reason to engage, and a specific next ask.
Preserve the full opportunity assessment and eight-slot Market Map while
improving the priority view. More hits, more pages, green tests alone,
or unsupported positive claims do not satisfy this outcome.

Tyler asked this task to commit the Wayne meeting lessons so they inform all
weekend builds and reconcile them with the benchmark coordinator's work. He
separately approved the coordinator's repair plan with the exact instruction
"this is the plan - lock it an we will go 1-7", then authorized Step 1. The
seven steps below keep that order. Tyler then narrowed the current scope:
"im not worried about sales force delivery yet - just the lila stuff to make our lead gen better".
Salesforce/CRM delivery, rep activity tracking and territory-management dashboards
are therefore deferred. They are not weekend work or acceptance dependencies.
This document does not restart Step 1,
reassign its active implementation, or replace frozen calibration 005.

The Wayne requirements inform acceptance and the product work that follows the
seven repairs. Read-only inventory, evidence examples and target/lead field mapping can be
prepared alongside the sequence. Do not use parallel preparation to silently
ship later repair steps out of order. Calendar targets never override evidence,
the sequence, current authorization or existing product contracts.

## Required build kickoff and completion record

Before any LILA weekend build:

1. Read this plan, [source notes](WEEKEND_BUILD_PLAN_2026-09-11_SOURCES.md),
   [CLAUDE.md](../../CLAUDE.md), relevant conventions and contract surfaces.
2. Record the plan ID and file hash, work ID, checkout, branch, exact code
   revision, intended scope, owner and acceptance criteria. Separate planning
   documents on current main from the code revision used by a frozen evaluation.
3. Check the existing owner and durable work status before dispatching or
   creating overlapping work. Preserve dirty files and other worktrees.
4. Investigate, report findings, patch an isolated named revision, then verify.
   A plan entry is not proof that the feature exists or permission to change
   an unrelated contract, engagement scope, credentials or release gate.

At completion, record the exact changed revision, input/source hashes and dates,
tests and report checks, authentic run evidence if any, remaining gaps, and next
owner/action. Report separately: documentation, fixture/unit proof, full
regression, native replay, fresh client acceptance, and live external integration.
A rendered report is not a qualified lead; preserved contact details do not
prove opportunity access; a feedback label is not a proven ranking improvement.

## Verified starting point

- Operating checkout: `/Users/wtjohnson/Lila`.
- Local and live GitHub main matched `3847d5a861d4e254be23052578c0f6786235164f`
  during this planning audit. The only observed dirty operating file was
  `data/entities/unresolved.log`; preserve it.
- Benchmark 005 evaluated `b6cba07738935ee5a374e5f3849db6c8ca8fca1e`, not
  operating main. Its repair and source-receipt commits must not be described
  as shipped production until ancestry, integration and acceptance are verified.
- Step 1 is active in the coordinator's
  `work/step1-research-picture` checkout, branch
  `codex/step1-research-picture-evidence`. Coordinator owns code and verification;
  LILA operator owns original-source trace 041 and assigned independent code
  review 042. No duplicate worker.
- The full daily SAM export is on local disk. LaCie is backup, not the normal
  source path or a prerequisite to build. Record the file actually consumed,
  its raw-byte hash, export vintage and coverage. Do not restore or mutate
  backups, invent publication times, or confuse ancillary-source credentials
  with access to the local SAM export.
- Complete target fields, reviewed investigation states, retained assessment
  history and editable vocabulary controls already exist. Extend them.
  A versioned approved-language workflow remains unproven. CRM row-shaping
  exists but delivery/import and rep activity are outside this weekend scope.

The benchmark comparison is preliminary and inconclusive. The frozen scorer,
applied to each independent AI submission, reported LILA: 0 confirmed relevant families, 17 unknown occupied slots, 3
confirmed non-relevant slots; GovTribe: 0 confirmed relevant families, 1 unknown,
1 non-relevant and 18 unfilled slots. Three LILA duplicate-family slots remain
in the denominator. There is one unadjudicated decision disagreement.
Both raw reviews marked 18 of 20 LILA slots out of scope; the frozen scorer
prioritizes unknown fields over many supported negatives, which is the Step 5
repair. Seventeen unknown slots are not seventeen promising leads. Neither a winner
nor a tie is established. The evaluated surface was the diagnostic raw list,
including discarded candidates, not the native qualified recommendation list.

## Track A: approved repair sequence, exactly 1 through 7

Coordinator is accountable for sequence, acceptance and bounded dispatch to the
existing team. A completed step delivers a verified handoff and makes the next
step ready; it does not imply production deployment. The exact detailed scope
and acceptance remain in the coordinator's approved repair brief, identified by
hash in the source notes.

| Order / work | Why it matters to the customer | Minimum acceptance before advancing |
| --- | --- | --- |
| **1. Evidence behind Research Picture claims** | A rep must not act on an invented active opportunity, date, product fit or route. | Canonical source and passage IDs, original identity/URL, dates, retrieval state and gaps accompany each material claim. Invalid IDs/quotes and unsupported active narratives are rejected or withheld. The saved VA assertions are traced to original records. Research signals remain visible as verification tasks; existing client-compose quarantine and qualification stay intact. |
| **2. Align retrieval and capability screening** | Relevant buyer language must not be lost because the gate uses different marketing wording. | Version a reviewed mapping from retrieval terms to supported capabilities, exploratory terms or exclusions. Audit all saved 356 records by term, matched span, coverage and decisive reason. Test genuine AP recovery and supplier-master requirements plus unrelated and historical examples. No blanket threshold reduction or automatic promotion of marketing vocabulary. |
| **3. Match requirements rather than boilerplate** | A projector or guard-services notice is not useful because a generic security clause contains a matching phrase. | Preserve match location and role: actual requested work, evaluation clause, referenced guidance, historical evidence or name collision. A boilerplate-only example is rejected while an original SOW buying the capability survives. Do not solve noise with a global phrase ban or an unjustified hard NAICS restriction. |
| **4. Preserve deadlines and separate clocks** | The seller needs an available next action, not a date that looked current at midnight. | Preserve original timestamp/offset, date precision and parse status. Test before/at/after query time, offsets, midnight, amendments, absent/conflicting dates. Distinguish query time, buying-status reference time, evidence cutoff and source freshness. An expired response may retain a separately evidenced investigation or partner action; missing time stays unknown. |
| **5. Explain scorer states correctly** | Managers must see the difference between clearly irrelevant work and a material unanswered question. | In a new harness version, keep fit, current action, route and evidence sufficiency separate. A supported disqualifier is not hidden by an unrelated unknown field. Unsupported negatives and absent evidence cannot become confirmed rejection. Preserve all 005 original judgments, counts and scorer outputs unchanged. |
| **6. Evaluate the intended product surface** | What matters is the recommendation and action a rep actually receives. | Predeclare retrieval diagnostics, evidence-qualified opportunities and seller-action evaluation separately. Measure the native recommendation surface in native order; zero recommendations remains zero. Verified family identity controls deduplication with lineage retained. Do not backfill from discarded rows or infer recommendation quality from the raw sweep. |
| **7. Collect decision-changing evidence** | Ambiguous requirements need the actual SOW, amendment or status evidence before a call can be justified. | Use the local export/store first and a bounded, comparable collection rule for both products. Resolve the IHS missing-SOW question or retain the precise gap. Distinguish confirmed empty inventory, failed lookup, not fetched and unreadable files. Preserve raw bytes, locators and hashes. Discovery-only attachment text does not silently become trusted Assess/LeadRow evidence. |

No new holdout expansion precedes acceptance of the repairs and prospective
protocol decisions. Keep known Apex, Arista and any development examples out of
independent superiority proof. Do not freeze or re-score a new campaign under
undeclared semantics. Review-source evidence collection is not authority to
silently change strict Assess attachment rules.

## Track B: better native LILA lead generation

Root LILA build task owns this product acceptance plan; the pinned LILA BUILD
Prompt Lab task is its build handoff entry. Assign a named implementation worker
only after a bounded scope is ready. The coordinator and LILA operator retain
Track A ownership. This is not a second implementation of their active repairs.

Wayne's requests sharpen the lead itself. Motion labels and schema choices below
are proposed responses, not claims that Wayne specified them or that they are
already implemented. All changes must use the accepted Track A repairs and the
existing assessment, target, research and release contracts.

| ID / priority | Bounded deliverable | Dependencies and acceptance |
| --- | --- | --- |
| **L1 / core** | Better company-to-requirement fit in the native lead. | Carry the company dossier and reviewed capabilities into screening, assessment and action generation. Every priority candidate cites an original buyer requirement and supported company capability. Label direct-product, partner or account-investigation motion without conflating it with qualification. Adjacent buying, a matching NAICS or a product name alone cannot establish fit. Reuse the accepted Step 2/3 implementation rather than a separate taxonomy. |
| **L2 / core** | Complete POCs, target role and credible route. | Reuse LeadTarget and preserve government-published POCs from the original notice. Deduplicate repeated occurrences of the same person without losing provenance. Distinguish contracting POC, supported program target and partner entry point. Show name, role, organization, available email/phone, source/date, reason to contact, route and authority boundary. Reuse valid previously acquired contacts; a name or phone never proves access. Missing target fields become explicit research gaps, not invented enrichment. |
| **L3 / core** | A specific, company-relevant next ask. | State why now, what capability may fit, through whom and the question that advances qualification. Use a bounded approved-company-language input for the opener/question, preserving source facts. Test one approved phrase change and regenerate; no unsupported product claims or generic elevator pitch. Use existing CurrentNextAction/LeadResearch semantics rather than rep activity or CRM schemas. |
| **L4 / core** | Useful investigations and a disciplined priority list. | Preserve credible unresolved cases with the exact open question, evidence and a next research/qualification action. Keep known IRS/Treasury known; retain technical qualification and investigation states. Remove weak items from priority only with a recorded decisive reason and preserved assessment/history. A zero qualified list must remain honest; it does not satisfy a seller-ready milestone. |
| **L5 / validation** | A repeatable real assessment-to-lead release. | Select a real Varonis candidate through the normal pipeline and regenerate its assessment and lead with fit, POC/target, route, why-now and next ask. Compare against the accepted quality baseline and the known Apex/Arista cases. No hand-edited report or fixed target count substitutes for proof. If material evidence is missing, retain the investigation and report the milestone incomplete. |
| **L6 / supporting** | Existing targeting controls and feedback inform the next review. | Reuse editable/restorable vocabulary and agency scope; demonstrate save, regenerate and inspect a changed result with an explained reason. Preserve reviewed feedback and rejection reasons in current stores. Do not build a new settings platform, territory dashboard or automatic-learning system. Swipe remains isolated and no feedback event itself proves ranking improvement. |

The business outcome from Wayne is more useful prospect conversations and, later,
observed meetings. Weekend acceptance is the quality and reproducibility of the
lead LILA generates. Meeting attribution, CRM integration, rep assignment,
activity sync and manager reporting are explicitly deferred.

### Evidence and non-regression requirements across both tracks

- Preserve company-first inference, full Assess parents, the eight-slot Market
  Map, evidence/target details and immutable history. Narrow the priority view
  with explicit dispositions and reasons rather than deleting assessment history.
- Keep discovery, investigation, historical market evidence, known opportunity
  and qualified seller action distinct. Known IRS/Treasury remains known;
  adjacent buying is a reason to investigate, not proof of Varonis product fit.
- Reuse known Army technical qualification, IRS/Treasury known-opportunity
  retention, Ginnie investigation and Arista retention/release cases. Refresh
  time-sensitive source facts before live use. Add Varonis for the meeting
  workflow and explicit irrelevant, boilerplate, expired and missing-source cases.
- Hold the accepted evidence-backed quality baseline before relevance cuts:
  unsupported claims, false promotions, supported missed requirements, source
  completeness, clock accuracy, target/route validity and next-ask specificity.
  Record population changes and reasons; do not equate all-HOLD with success.
- Extend existing patterns and test the meaningful invariants. Run targeted
  adversarial tests, the required offline strict regression and report checks
  for code changes. Verify fresh client output through Command Center
  `lila_release` when its approved inputs and environment are ready; no
  hand-edited HTML may stand in for ordinary-pipeline proof.
- Every build receipt names exact producer revision and source evidence. Tests,
  old bundles, demos, contracts and current live behavior remain distinct proof.
- Validate migration/rollback and preserved data before updating operating main.
  Never prune worktrees, reset another worker, merge unrelated code, change
  production gates or start a paid/external client build just to claim completion.

## Weekend pacing and cut line

These are work targets, not promises to pass a gate by a clock time.

| Target window | Work and exit evidence |
| --- | --- |
| **Friday September 11** | Commit this shared direction and entrypoint pointers. Record baseline and in-flight branch inventory. Coordinator completes Step 1 investigation, patch, source trace, replay and review if ready. Prepare L1-L6 evidence/target mapping and acceptance examples without parallel competing implementation. |
| **Saturday September 12** | Advance Steps 2, 3 and 4 in their approved order after each prior acceptance. Audit saved positives/negatives and precision, preserving the original comparison. Prepare real Varonis evidence and existing target/control seams. |
| **Sunday September 13** | Advance Steps 5, 6 and 7 in order if their dependencies pass. Integrate a verified repair candidate on a named build branch. L1-L5 native lead-quality demonstration is a stretch objective only after the evidence and integration gates pass. |
| **Weekend close** | Publish passed/failed/pending work IDs, exact revisions, checks, real versus fixture proof, current operating version, remaining blocker and next owner. Carry unpassed steps forward in order; do not lower standards or switch to visual polish to create a completed-looking report. |

The weekend close must report actual repair progress and one shared operating
baseline. The product milestone is a native Varonis assessment-to-lead output
with supported fit, POC/target, route, why-now and next ask while preserving
Apex and Arista quality. Verified progress is not the same as that milestone;
if the native lead remains blocked, name the gap and keep the work incomplete.

## Ownership, continuity and escalation

- **Root LILA build task** (`01a08162-2f14-70a0-bc96-6297c68f4135`):
  canonical plan, main-repo pointers, production-integration decision and
  product acceptance. Updates the plan through versioned commits.
- **LILA Benchmark · Coordinator** (`01a082f7-baad-74d2-9466-dcb649b818ce`):
  Track A sequence and its durable status, bounded assignments and independent
  benchmark evidence. Existing heartbeat remains the sole benchmark scheduler.
- **LILA Benchmark · LILA operator** (`01a082f7-024e-7110-ad1b-4e47786fa72a`):
  its current assigned source trace and later bounded work from coordinator.
  Do not create a duplicate operator or feed results to blind reviewers.
- **01 — LILA BUILD · Prompt Lab** (`01a0278a-e36f-7280-82a8-21a601a5847f`):
  production-build kickoff reference and future bounded integration handoffs.
- **02 — LILA SWIPE · Finish the Game** remains its separate lane. Notify it of
  the L6 boundary when assigned integration work; this plan does not restart it.
- **GovTribe operator and blind reviewers** remain parked until their next
  bounded authorized assignment. Do not distribute product membership,
  scorecards or this findings document into an active blind review.

The plan and repo startup instructions are a required agent workflow, not a newly
implemented runtime or CI enforcement mechanism. Already-running agents receive
the plan pointer explicitly. Historical worktrees must read current plan
directions without replacing their code base or contaminating frozen evidence.

For a future contract change, identify the exact surface and existing authority.
Complete a concrete isolated diff and its checks before seeking any missing
operator decision. A new approval flow must not be invented when the current
session already authorizes the action. Metered enrichment and public comparative claims remain separate action
boundaries. Salesforce delivery is deferred by the latest user instruction.

## Deferred from this weekend's core path

No Salesforce/CRM delivery, rep activity sync, territory-management dashboard,
new report redesign, replacement dashboard platform, native telephony, automatic
model-learning claim, new paid-source dependency, portfolio billing platform or
public "we beat GovTribe" claim. Those do not resolve the
current evidence and seller-workflow gaps. Preserve business hypotheses for a
later tested decision.

See [source notes](WEEKEND_BUILD_PLAN_2026-09-11_SOURCES.md) for customer statements,
presenter promises, code seams and frozen evidence identifiers.
