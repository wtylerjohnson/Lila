# Source completeness: attachment failure receipt

## Pre-patch report

Recorded before implementation, September 8, 2026. Investigate -> report ->
patch -> verify. Base: `ab1056f36006eb9b21cde2940316d2302ecae175`.
Development branch: `codex/source-completeness-20260908`.

The operating checkout is `/Users/wtjohnson/Lila`. Its existing modification
to `data/entities/unresolved.log` is protected. This tranche uses a separate
worktree and does not modify operating data, approvals, releases, BD Radar,
the eight-slot press, or the independent benchmark's frozen runtime.

### Reproduced defect and cause

1. `tools/api/sam_notice_detail.py:228-270` reports a failed attachment lookup
   as `resources_checked=False`, `attachments=None`, with an error. An
   unfamiliar HTTP-200 JSON schema follows the same failure path. Confirmed
   zero requires a recognized container (`:107-133`).
2. `run_searches.py:197-233` reads the attachment list but drops the adapter's
   error and checked status. `None` becomes `[]`, and processing continues
   without any diagnostic. In an offline reproduction using the real adapter
   and a mocked resource HTTP failure, its output receipt was identical to a
   confirmed-empty inventory. Original SAM description matches were preserved.
3. The attachment statistics are merged into the SAM census at
   `run_searches.py:1286`, and retained in the search artifact at `:2165`.
   Losing the reason before this handoff hides why file-only requirements were
   not inspected. This does not establish that a particular real lead was lost.

All citations above refer to the base revision, before new lines are added.

### Minimal patch boundary

- Extend the existing `attachment_errors` receipt in `run_searches.py` with
  bounded notice-prefixed errors from the resource adapter.
- Preserve the existing refusal to use stale inventories.
- Explicitly unchecked or absent inventories must not reach extraction. If
  the adapter supplies no error, retain a named verification gap.
- A successfully verified empty inventory stays a true zero with no error.
- Keep notice census completeness, existing matches, limits, relevance rules,
  strict Assess evidence ownership and all external schemas unchanged.
- Add offline regressions in `tests/test_sam_attachment_text.py`. Do not
  rewrite prior expected outcomes to make the suite green.

This extends an existing internal diagnostic field. It does not change a
contract, claim full attachment coverage, create a gate, or promote a lead.

### Acceptance

1. Real resource adapter + mocked HTTP failure: retain notice ID and reason;
   preserve existing rows; no text download or fabricated match.
2. Real adapter + unfamiliar schema: retain a named failure, not a true zero.
3. Real adapter + recognized empty list: preserve clean zero-file receipt.
4. Explicit unchecked/absent inventory without error: record the gap and do
   not extract even if an unchecked response contains file entries.
5. Stale fallback: retain both lookup failure and existing stale-use refusal.
6. Oversized error text remains bounded; serialization preserves diagnostics.
7. Existing extraction/relevance/fingerprint/quota tests retain their behavior.
8. Run focused and full offline-strict tests plus changed-file lint. Report
   any baseline failures separately, without changing unrelated expectations.

### Proof and migration boundary

These are synthetic transport regressions, not live SAM coverage or real
Arista/Apex seller-ready proof. No old artifact is rewritten. Existing search
artifacts cannot recover errors they did not capture; they remain historical
and must not be relabeled as complete file research. A future authorized
native refresh will produce the repaired receipt. No live release is part of
this tranche.

The next real milestone is public-source replay of the reviewed Army,
IRS/Treasury and Ginnie cases, preserving requirements, people, references,
status and routes through to the report. Arista remains a regression client;
the independent benchmark must stay on its own frozen version.

## Verification

September 8 status: **DRAFT for integration**, implemented and focused-tested,
not merged or released. No operating code or client artifact was changed.
The September 9 continuation below supersedes the verification blockers,
not this historical record of the initial results.

- Tests added before the production edit: **8 failed, 15 passed**. The failures
  reproduced the missing diagnostics and extraction of an explicitly unchecked
  inventory. Existing tests and confirmed-empty behavior passed.
- After the 14-line production repair: **93 passed, 1 skipped** across attachment,
  SAM extract/census/lanes/reliability/dual-key, dossier, sweep-receipt and operating
  repair tests. The skip is the real Arista operating-input acceptance test;
  that input is not versioned. No live source calls or new client releases were made.
- Full offline-strict suite: **5,177 passed, 136 skipped, 8 failed, 8 errors** in
  86.08 seconds. The initial collection attempt also required the existing small
  `sample_sam_response.json` test fixture absent from fresh Git checkouts. It was
  copied into this isolated worktree only, with an added terminating newline.
  No ignored research stores or client releases were copied to make tests pass.
- Every remaining failing/error test was rerun against an untouched archive of
  `ab1056f` under `/private/tmp/lila-source-baseline-jQiNOT`: the same **8 failures
  and 8 errors** reproduced. They are not a green-suite waiver.
- Changed-file Ruff check reports **23 pre-existing findings**, also reproduced
  against base file bytes (19 in `run_searches.py`, 4 in the attachment test).
  This diff introduces no additional Ruff findings. Unrelated style/code was
  not rewritten.
- `git diff --check` passed; independent read-only code review found no actionable
  issue. Operating HEAD and the pre-existing `unresolved.log` SHA-256 remained
  unchanged (`30b8b661bb7559fa8eeed0e1e1c00cb0cdfe3946c2c8357074134375a7bde1ca`).

### Remaining suite failures, also reproduced at the base

| Surface | Count | Observed cause |
| --- | --- | --- |
| Capture-brief visual standard and identity template | 2 failures | Recorded Future and Osprey client marks are absent in the clean checkout. |
| Seal coverage measurement | 1 failure | No saved candidate evidence packs exist in the clean checkout; test refuses vacuous coverage. |
| PDF, print pagination and studio browser checks | 5 failures | System Chrome aborts during launch in this execution environment. |
| Insignary golden manifest | 8 setup errors | The real stored Insignary sweep is absent, so replay cannot generate its expected HTML. |

Resolve the verification environment and required saved fixtures before claiming
full-suite certification or integrating. This tranche neither fixes nor masks
those independent prerequisites.

### Commands executed

Run from this development worktree using the existing operating interpreter:

```sh
LILA_SUITE_OFFLINE=1 LILA_SUITE_STRICT=1 /Users/wtjohnson/Lila/.venv/bin/python -m pytest tests/test_sam_attachment_text.py tests/test_sam_extract.py tests/test_sam_census.py tests/test_sam_lanes.py tests/test_sam_reliability.py tests/test_sam_dual_key.py tests/test_dossiers.py tests/test_compose_cache_and_attempts.py tests/test_operating_repair.py -q
LILA_SUITE_OFFLINE=1 LILA_SUITE_STRICT=1 /Users/wtjohnson/Lila/.venv/bin/python -m pytest tests/ -q
/Users/wtjohnson/Lila/.venv/bin/python -m ruff check run_searches.py tests/test_sam_attachment_text.py --output-format concise
git diff --check
```

The second and third commands remain nonzero for the disclosed baseline issues.
Fixture success does not establish source completeness, yield improvement,
current opportunity status, seller readiness or commercial conversion.

## September 9 continuation: verification prerequisites

Main and development anchors remained `ab1056f`. The five browser failures
passed unchanged when run outside the execution sandbox, using isolated
temporary Chrome profiles. No signed-in user tabs were used.

Six exact original operating files were copied to the same relative paths in
the development checkout, solely to restore required test inputs. No test
expectation, fixture figure, source record or approval was edited. The seal
coverage measurement includes both available operating packs, not a selected
subset. Copies were SHA-256 checked against their originals:

| Relative path | SHA-256 |
| --- | --- |
| `data/reference/marks/client/recorded_future.svg` | `820448dea994a3a299d0bf937156c49b8fc9eb2d2d70de1fad18d5cc6d6e2333` |
| `data/reference/marks/client/osprey_flight_solutions.png` | `211635c8f254d8a3f34fed46766baefa2a7b8d691980cae5e40809fd444c6829` |
| `data/reference/marks/client/insignary.png` | `32beb6a3edfc1c576c9f9136795b0bafc3b8d263675dcb604028ce9e919805da` |
| `data/cleaned/searches_insignary.json` | `b8b0309d6400a8e626a36b0d08dfbcdd6c237434c4dc519c9896660ec96caac9` |
| `data/state/candidate_review_v1/arista_networks/arista_networks.golden_report.evidence_pack.json` | `f25c802cbc9bbeae81c8308fb585e4535c4f112365538574ad25076b7f02f4fe` |
| `data/state/candidate_review_v1/apexanalytix/apexanalytix.golden_report.evidence_pack.json` | `8727d339d0f844800906284135467917676dcd7b9aaf5f018e0fb5b42b61301e` |

Total copied: 2,641,781 bytes. The unchanged mark, seal and Insignary checks
then passed: **21 passed**. The Insignary negative test still requires the
missing primary SAM record to block its historical replay; no record was
fabricated. These saved inputs are test prerequisites, not fresh-source proof.

Restoring the complete Apex pack also enabled the saved Market Map press tests.
The first combined suite then reported **5,316 passed, 40 skipped, 1 failed**
in 133.20 seconds. The sole failure required the original Apex client mark;
it was not an attachment or dossier failure. Its exact original 976-byte file,
`data/reference/marks/client/apexanalytix.png`, was copied to the development
checkout and hash-checked:
`51024a59cb0f482f463e865ccd78048880472446fc965ed5163fec6a4679552f`.
The unchanged Market Map test group then passed: **13 passed**. Seven original
operating files have now been restored, totaling 2,642,757 bytes.

### Final combined verification, September 9

The full offline-strict suite, including the dossier-context repair, completed
with **5,317 passed, 40 skipped, 18 warnings, zero failures or errors** in
134.62 seconds. The run used the same full-suite command with `--tb=short`
and temporary headless Chrome profiles outside the sandbox. Restored saved
inputs enabled tests that skipped in the sparse worktree. No skip marker or
existing expected result was changed. The 18 warnings concern existing
deprecations in report markup and multiprocessing.

`git diff --check` passed. Across both production/test edits, Ruff retains
28 findings reproduced on base file bytes, with none introduced; details are
in [the dossier report](DOSSIER_ATTACHMENT_CONTEXT_2026-09-09.md).
At that checkpoint, operating main remained `ab1056f`, and the protected
`unresolved.log` hash remained
`30b8b661bb7559fa8eeed0e1e1c00cb0cdfe3946c2c8357074134375a7bde1ca`.

Both repairs were then uncommitted and unmerged. This completed local code
verification, not real-client evidence completeness or seller-ready validation.
No native client run, current-source refresh or external release was performed.

## Integration, September 9

The operator subsequently authorized merging source commit `44ed2a6` into
operating main. This integrates the tested repairs and updates the roadmap;
it does not certify fresh source completeness or change the client approvals.
The development worktree, ignored test inputs, and existing operating data
modification remain preserved. No client data or cached asset enters the commit.
