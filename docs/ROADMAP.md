# LILA Lead Gen roadmap

## Operating checkpoint: September 8, 2026

The product is client-specific federal lead generation: understand the company,
identify relevant buying motions, establish product fit and a viable route,
and deliver an evidence-backed person and next action. BD Radar remains paused.
The existing eight-slot report and assessment-bound lead report are the delivery
surfaces; this plan does not replace their contracts or redesign their visuals.

### Landed on operating main

- `29fa382`: integrated company intake and retained the full assessment beside
  its lead projection and skeptic evidence in the release bundle.
- `f8b1667`: carried client-bound dossiers, reviewed research, target detail,
  priorities and next asks forward without clearing qualification holds.
- `ab1056f`: repaired Army Call for Solutions candidate eligibility and recorded
  the original-source replacement sequence in `GOVTRIBE_BENCHMARK.md`.

The operating checkout is `/Users/wtjohnson/Lila`. Develop in named branches,
verify there, and preserve operating data and the independently frozen benchmark.

### What the latest saved outputs prove

The September 8 manifests for Arista (`2026-09-08-53e79f6743cc`) and Apex
(`2026-09-08-4facae4861ba`) report release eligibility and successful rendering.
Arista has 486 parent assessments and 84 held lead rows; Apex has 206 parent
assessments and 51 held lead rows. Neither contains a T1 or T2 lead. All scored
leads have at least one skeptic failure. These saved artifacts do not record an
exact producer commit, so their timestamps alone do not prove current-head replay.

Apex retains three reviewed priorities and their next asks despite the holds.
Arista has no reviewed priorities. Retention and packaging success are useful
progress, but are not proof of automatically seller-ready leads. The recurring
gaps include requirement/attachment evidence, seller route, source screening,
and a substantiated demand clock. Do not solve these by weakening qualification.

### Next execution sequence

1. **Make missing source evidence visible.** The integrated repair addresses SAM resource
   error loss described in [the pre-patch report](plans/SOURCE_COMPLETENESS_2026-09-08.md).
   Distinguish failed file inspection from verified zero attachments while
   preserving the successful notice census. Code verification is complete;
   fresh real-client source coverage remains to be measured.
2. **Complete a small real evidence chain.** Use the known reviewed Army,
   IRS/Treasury and Ginnie cases for calibration and Arista for regression.
   Inventory original notice/file evidence, preserve hashes and retrieval dates,
   and trace the exact requirement, contact, route and clock into Assess and
   LeadRow. Report each missing handoff before patching it. Closed notices stay
   historical; a forecast or proposed follow-on is not a reopened response window.
3. **Prove the automatic product.** Re-run through the native Command Center
   with the operator's actual approved inputs. Require a small evidence-supported
   shortlist with who to call, why now, what to sell, through whom, what to ask,
   and clickable support. Compare full evidence retention and readiness before
   and after; preserve honest holds and never pad to a target count.
4. **Measure generalization.** Use the separate benchmark's frozen campaigns
   and untouched holdouts. Known-case repairs and fixture passes are calibration,
   not independent comparative superiority or commercial validation.

Learning, relationship expansion and prediction build on these evidence-linked
actions and observed outcomes. They do not substitute for the first repeatable,
seller-useful release.

## Integration checkpoint: September 9, 2026

This operator-authorized integration brings source commit `44ed2a6` from
`codex/source-completeness-20260908` into operating main, based on `ab1056f`.
The named development worktree is preserved. The two repairs are:

- Failed or unknown SAM file inventories retain their named error instead of
  appearing equivalent to a confirmed empty inventory.
- Dossier analysis receives the existing captured attachment passages and file
  provenance as bounded, integrity-checked, explicitly unreviewed research.
  The authoritative notice description, human reviews, lead tiers and report
  presentation are unchanged. See the
  [dossier pre-patch and verification report](plans/DOSSIER_ATTACHMENT_CONTEXT_2026-09-09.md).

Combined local verification: **5,317 passed, 40 skipped**, zero failures or
errors. Required saved test inputs were restored byte-for-byte; existing test
expectations were retained. Changed-file lint has 28 reproduced baseline
findings and none introduced. This is code/replay proof, not a fresh client run.

The next real-data milestone remains an evidence-complete small lead shortlist,
not a new layout or a target count. Transport has passed local verification;
next trace original requirements, route, people and timing through the native run.
The current strict Assess contract does not accept attachment-only requirement
spans: expanding that source rule needs its own explicit contract review, while
preserving exact evidence binding and human approval. Neither repair silently
changes that rule or proves that existing real-client holds are resolved.

## Earlier strategic backlog (retained for reconciliation)

The list below predates the September 8 integration. It records longer-horizon
intent, not a verified inventory of capabilities still missing today. Recheck
each item against code and real-run receipts before scheduling it.

Ordered by expected capture value. Items graduate off this list into
API_SETUP.md (sources) or the changelog (features) when shipped.

0. ~~**Deadline-aware depth**~~ — SHIPPED 2026-07-03: pursuit dossiers on the
   pursue-grade shortlist, budgeted deep-fetch (LILA_DEEP_BUDGET, default 8),
   permanent notice cache, most-urgent-deadline-first.
1. **Cross-model verification** (key budget in progress — Tyler) — run the triage verdicts and research-picture
   claims past a second screen (different model family, or same family at a
   different tier) and flag disagreements for human review. Disagreement rate
   becomes a per-run confidence metric printed in the provenance block. The
   deterministic verify_numbers pass stays; this adds judgment-level checking
   on pursue/discard calls, where a wrong discard silently costs a deal.
2. **Cross-run memory (deltas)** — diff each sweep against the previous one:
   NEW notices, verdict changes, approaching deadlines, fresh recompetes.
   "What changed this week" is the report clients renew for.
3. **Dollar joins** — attach USAspending award amounts to every recompete and
   incumbent so the brief anchors dollars without manual lookup.
4. **Congress.gov key** (2-min signup) — appropriations/NDAA line items as
   funding-proof citations.
5. **USAJOBS key** (2-min signup) — agency hiring posts as 6-12-month-early
   buying signals.
6. **Apollo retrieval loop** — close Target stage from handoff file to actual
   contact retrieval inside LILA.
7. **Eval harness** — score research pictures against what actually got
   pursued/won; feeds prompt tuning with ground truth.
8. **Paid-API tier** (Keith case pending head-to-head eval) — GovWin /
   HigherGov / GovTribe for forecast pipelines and pre-RFP intel the free
   stack cannot see.
9. **TODO: Contact extraction from solicitation attachments (SOW PDFs)** —
   phase two of the contact graph; requires download pipeline and quota
   guards. Phase one (shipped on feature/contact-graph-13a) covers structured
   POC fields and notice description text only.
