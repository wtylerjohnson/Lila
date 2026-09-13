# Weekend plan source notes: September 11, 2026

These notes make the build plan portable without copying the meeting transcript
or changing any sealed benchmark evidence. They distinguish customer requests,
our implementation choices, presenter claims and observed code.

## Latest scope instruction

After requesting the committed combined plan, Tyler explicitly narrowed it:
"im not worried about sales force delivery yet - just the lila stuff to make our lead gen better".
This supersedes the draft CRM/territory implementation track. Those customer
requests remain useful context below but are deferred, with no weekend build or
acceptance dependency. Core work is LILA requirement fit, evidence completeness,
POCs/targets, routes, company-specific next asks and disciplined prioritization.

## Wayne meeting

Original local transcript:
`/Users/wtjohnson/.codex/attachments/9b048319-80de-4ce8-af23-76b887c42fc5/pasted-text.txt`

SHA-256: `9e7ad59e28c8ccc62581706e2415e0f651382340f01dade5369988630f361857`

Speaker attribution follows the conversational context; the transcript has no
formal speaker labels. No rapport, compliments or speaker enthusiasm is treated
as purchase or adoption validation.

| Evidence locator | Customer substance | Build implication |
| --- | --- | --- |
| Line 27 | Wants customer-driven search/filter changes without returning to the team. | Verify an existing-control edit/save/regenerate round trip. |
| Lines 29-38, 131 | Product selling matters more than large-program capture; GSI teaming is early. | Support product, partner and account-investigation routes with explicit evidence states. These labels are our design choice. |
| Line 50 | New prospect meetings are measured weekly. | Measure observed actions and meetings, not search volume as the business outcome. |
| Line 60 | Wants company-approved vernacular incorporated into the next-action message. | Version approved language and show its use alongside evidence-backed facts. |
| Line 72 | Salesforce is the single source of truth; asks for enough exported elements for existing tracking. | Deferred by latest instruction; preserve as later roadmap context. |
| Lines 84, 86, 97 | Wants territory assignments and visibility into which surfaced items each rep acts on. | Territory-management build deferred; retain agency focus as an existing relevance control. |
| Lines 103-105 | Disconnected federal intent reports are discarded because he cannot act on them. | Prioritize the supported first contact, route and next ask, not a loose contact list. |
| Lines 43-44 | Suggests portfolio access paid by the reseller through margin or MDF. | Commercial distribution hypothesis; no budget or price approval. |
| Lines 145-149 | Endorses mobile interaction. | Later mobile validation; retain feedback/rejection reasons without assuming learning. |

Presenter promises and limits:

- Line 73 promises CRM export readiness and suggests future Salesforce calling
  and automatic logging. Wayne explicitly requested workflow-compatible export;
  native dialing was our proposed extension and is not mandatory weekend scope.
- Deterministic generation, the status taxonomy and graph algorithms are our
  implementation choices. Their existence does not prove corporate approval,
  relevance, factual correctness or improvement.
- The opening 17-to-1 claim is before Wayne's introduction (lines 2-8 versus
  line 21). It must be corrected internally. The transcript does not establish
  that this exact claim was pitched to Wayne.
- The 49-to-0 comparison (39), 10-15 percent semantic improvement (109), every
  report improving (83), and spot-on predictions (144) are presenter assertions
  requiring separate evidence. They are not acceptance baselines.
- The adjacent Forcepoint/FBI example (150-155) does not establish Varonis fit,
  an available transaction route or a qualified lead.

## Coordinator reconciliation and sealed benchmark

Coordinator confirmed on September 11:

- Latest direct user instructions: lock the plan and go 1-7, followed by Step 1.
- Coordinator implements Step 1 in its isolated development checkout; existing
  LILA operator owns source trace 041 and assigned independent code review 042.
- Local disk has the full daily SAM export; LaCie is backup.
- No competing weekend plan, new holdout query or production mutation is part of
  that reconciliation. Coordinator delivers Step 1 completion, then Step 2 ready.

Shared project:
`/Users/wtjohnson/Documents/Codex/LILA-Competitive-Benchmark`

| Source | SHA-256 / identity |
| --- | --- |
| `control/CALIBRATION_005_REPAIR_PLAN.LOCKED.json` | `17c2905d6eaee80f4e58eb39c7cc3cbf662ceae28c364e86ddaca4981edc67aa` |
| `handoffs/CALIBRATION_005_PRIORITIZED_FIXES.md` | `80d02eb190fb9eded30e559064d9e33023a2e207557666b19e009dc99a21e5c2` |
| `cases/apex-discovery-calibration-005/result-v4.json` | `83d1ad79d70083cd6705c38f8b4c632847721754c363cf62d3fed8293ec764ca` |
| `scorecards/apex-discovery-calibration-005.md` | `b8d9742ba3373d95d14e66caf1002731140a1eb4db90ddde59c32107095b43cf` |
| Frozen evaluated code | `b6cba07738935ee5a374e5f3849db6c8ca8fca1e` |
| Live status, mutable | `control/CALIBRATION_005_REPAIR_PLAN.status.json` |
| Step 1 pre-patch scope | `handoffs/STEP1-041/PRE_PATCH_REPORT.md` |

Observed diagnostic findings: all 356 raw records failed the capability prefilter;
14 retrieval phrases and eight literal core taxonomy entries did not align
fully; Research Picture asserted two active VA opportunities without complete
source-bound support; boilerplate generated noise; date parsing lost precise
time/offset; the scorer's unknown precedence obscured supported scope judgments;
the measured surface included discarded raw rows; neutral review packets lacked
attachments/continuation. These are bounded findings at the evaluated revision,
not proof of complete-market recall or defects at every production revision.

Step 1's source operator is independently tracing the VA source/status claims.
Its fresh findings and completion receipt belong to that owned work, rather
than being declared completed by this planning document.

Counts are preserved in the plan exactly as the initial preliminary scorecard.
They are frozen-scorer outputs applied to each independent submission. Both raw
reviews marked 18/20 LILA slots out of scope; unknown-precedence classification
produced the 17 unknown aggregate. Step 5 repairs that distinction prospectively.
The original two AI reviews do not establish human domain validation, a product
winner, novelty or an identical-search comparison. Case 004 remains incomparable
with a null score. Prospective fixes must not overwrite either case.

## Existing operating code: extend these seams

Verified against operating main `3847d5a861d4e254be23052578c0f6786235164f`.
Reinspect before implementation if the branch has advanced.

| Existing seam | Verified scope and constraint |
| --- | --- |
| `agents/leadgen/targets.py:12-47` | LeadTarget already includes person, role, organization, provenance, email/phone, route, reason, ask, authority boundary and evidence. LeadResearch preserves investigation/known/deprioritized/rejected states. |
| `agents/leadgen/next_action.py:14-24`; `agents/leadgen/eval/checks.py:824-830` | CurrentNextAction owner means operator. Rep assignment requires its own projection or an explicit reviewed contract amendment. |
| `agents/golden_press/target_actions.py:305-371` | Existing CSV row-shaping seam. Unit coverage exists; production caller, delivery and Salesforce import were not established in the bounded audit. |
| `agents/review.py:272-274,1471,1635`; `docs/CONVENTIONS.md` keyword/NAICS workshops | Existing editable/restorable targeting and vocabulary paths; preserve operator scope and history. |
| `agents/leadgen/quality_baseline.py`; `tools/reviewed_cases.py` | Reuse assessment and reviewed-case retention; changes to priority do not erase source history. |
| `tools/crm/base.py`; `tools/crm/apollo_handoff.py` | Existing contact-finder/Apollo handoff. Salesforce delivery, territory activity and approved-message library were not established by bounded source search. |
| `docs/OPERATING_RUNBOOK.md`; `CLAUDE.md` | Single operating checkout, Command Center release path, named development branches and preservation of independent work. |
| `docs/ROADMAP.md` September 9 checkpoint | Discovery attachment context is unreviewed; strict Assess does not automatically accept attachment-only requirement spans. |

No production feature audit claim is inferred from a missing keyword alone.
Integration tests and actual native use must establish the final behavior.
