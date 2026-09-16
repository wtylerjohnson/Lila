# UPSTREAM-LEADS-060: implementation and handoff

Status: local development repairs; production cutover and ordinary-run business
proof are pending. No commit, merge, push, paid enrichment or client release.

This is the original development receipt. Subsequent operator approval and
integration/run results are recorded in [INTEGRATION.md](INTEGRATION.md).

## Baseline and protected work

- Operating repository: `/Users/wtjohnson/Lila`, main
  `8f19f7f5abaae7a425267bbbb2c45ee99c32f95f`.
- Development: `/Users/wtjohnson/lila-upstream-leads-20260916`,
  `codex/upstream-leads-20260916` at the same baseline, with the local patch.
- Plan: `LILA-WEEKEND-2026-09-11-v1`, SHA-256
  `71a4c7c11ee575596357e5e0fcfb66a75d2c855b4761040042732c8cad26bfc5`.
- Existing coordinator steps were accepted/merged; no competing Step 2,
  frozen calibration 005, Salesforce, SWIPE, BD Radar or JTG repair work.
- Operating protected NETSCOUT profile, taxonomy, engagement scope and entity
  log retained their original hashes and git status at the preservation check.
  Existing untracked/ignored material remains untouched in the operating repo.

## Prioritized gaps and delivered repair

| Priority | Concrete gap and responsible stage | Repair / remaining boundary |
|---|---|---|
| 1 | Oversight text truncated to 400 characters before relevance matching | `tools/api/watchdog_rss.py:43` retains full parsed RSS body, complete pre-cap inventory and per-feed failures. Full report fetching/agency attribution still needs separate collection evidence. |
| 1 | PROGRAM sources collected but absent from native research parents | `agents/assess/upstream.py:50` and `research_subjects.py:157` screen existing provenance-complete PROGRAM records into source-bound research. No new scraper, funding requirement or live-opportunity promotion. |
| 1 | Final product would label new PROGRAM research as existing awards | `agents/golden_press/external_product_projection.py:374` owns them in existing slot 7; `external_product_render.py:251` labels emerging-need research. Eight locked headings remain unchanged. |
| 2 | Filtered forecast inputs could not explain discarded evidence | `run_searches.py:1921` persists the scoped pre-relevance inventory. Exact original fields remain inspectable, including rejected matches. |
| 2 | Adapter/source success confused with process coverage | `upstream.py:131` emits an internal agency/component x seven-family stage matrix. Unknowns remain null, source attempts/failures travel with it, and unsearched MDA is not zero. |
| 2 | Withdrawn planning record could still read as a planned purchase | `tools/api/forecasts/posture.py:5` recognizes narrow source-literal withdrawal evidence. Native/Golden summaries and new first questions no longer imply the withdrawn window remains available. |
| Open | MDA budget portfolio and deep oversight archives absent/inaccessible | Collection failure/scope gap, not a negative relevance finding. No seven-family Veeam ordinary-run proof yet. |
| Open | Multi-source buying-event connection, current need/owner, permitted route and actionability | Existing graph/forecast-history/contact/review facilities remain the owners. Cross-publisher deduplication and current-need investigation are not completed by this patch. |
| Open | Final-stage production coverage persistence | The ordinary materialization sidecar runs through Assess. The helper can count source-bound reviewed parents when supplied, and the diagnostic replay exercises that path; ordinary Press/release does not yet persist those later-stage coverage counts. Actionable-lead adjudication remains unmeasured. |

## Contract change requiring separate review

ResearchSubject v2 introduces `source_kind=program` and
`source_posture=published_program_signal`. Exact source payload/hash, buyer-only
context, cutoff checks, source-bound reviewed overlays and no automatic buying
children are preserved. Existing v1 award/forecast serialization stays intact.
Schemas were regenerated using the existing schema exporters.

The new contract is explicitly documented in `docs/CONTRACT_SURFACES.md`.
Repository policy requires operator approval before integrating this versioned
contract. New populations change run identities and need ordinary review;
existing approvals/releases are not migrated or silently reapproved.

## Verification receipt

- New upstream plus native projection tests: **44 passed**. Includes the
  final eight-slot PROGRAM ownership/HTML regression.
- Earlier strict full pass, before that final added projection test:
  **6,218 passed, 40 skipped, 26 subtests passed**. Existing warnings recorded
  in `tmp/upstream-060-final-suite.xml`.
- Final exact-code strict full suite: **6,219 passed, 40 skipped, 26 subtests
  passed, 22 warnings**, in 134.61 seconds. Receipt:
  `tmp/upstream-060-complete-suite.xml`. No assertions were relaxed.
- Real frozen DHS replay: identical bytes across two independent invocations
  for all six output/receipt files. See `EVALUATION.md` and
  `tmp/upstream-060-replay-e/receipt.json`.
- `git diff --check`: passed.

The strict command is:

```sh
LILA_SUITE_OFFLINE=1 LILA_SUITE_STRICT=1 /Users/wtjohnson/Lila/.venv/bin/python -m pytest tests/ -q --tb=short --junitxml=tmp/upstream-060-complete-suite.xml
```

Existing regression fixtures missing from a fresh worktree were copied unchanged
from operating local data: sample SAM response, the Insignary saved sweep, three
saved evidence packs, and required client marks. They are untracked/ignored test
inputs, not new evaluations or client releases. Local browser tests required
the usual permission to launch local Chromium. Initial fixture/permission
failures were not fixed by weakening assertions. One workstation UI timeout
passed on targeted rerun and the next full suite; no UI timing assertion changed.

## Real proof boundary and next executable step

989 fresh DHS forecast rows mapped and screened; the same four research parents
survive baseline and repaired code. Four have published named contact channels.
Useful-lead yield, independent recall and actual lead-time improvement remain
unmeasured. See the source-linked candidate questions and failure ledger in
`EVALUATION.md`. Test success does not fill those business-proof gaps.

The next ordinary run requires an approved Veeam capability/search strategy for
DHS/MDA and approval of the isolated PROGRAM contract extension. No approved
Veeam strategy/workstation was found. `run_searches.py:1174` requires human
strategy approval; this task did not create one or change any gate. Once those
inputs are approved, integrate through the operating workflow, execute the
actual sweep/Assess/LeadGen path, adjudicate the primary-source comparison set,
and report before/after useful leads with contact-role evidence and actual
solicitation dates. Do not substitute this diagnostic replay for that run.
