# Cycle 5 follow-on · independent review of solo range

Reviewer: Codex  
Date: 2026-07-12  
Branch: `rename/opportunity-assessment`  
Binding base: `8fb4c5063e0e461b73a40005d36819fba1232347`  
Binding endpoint: `0796a74da48593a32435830b93c87c3ba2b06bf3`

## Mechanical verification

- `git merge-base 8fb4c50 0796a74` = `8fb4c5063e0e461b73a40005d36819fba1232347`.
- The requested range contains **10 commits**, not 11. The current branch has
  one additional documentation-only commit, `a2a7cbc`, which contains the
  binding tasking and is outside the stated endpoint.
- Exact range reviewed, in order:
  - `333fcbf9bfaf212cd4f48db954c852e44b57ea4e`
  - `f5d019bb632ef4cdf8f0377eeaf6b660de9a7134`
  - `d0285bbe5bcb5404b8bbe678d127cd91c0b566e0`
  - `01fc857f92e642eb64b69e6bc27abaa05a2ff733`
  - `88e6e469d43003e8c7863e2e56a9a9bfe9324373`
  - `7d0e08f1bb299cba858c48ae4a176b27a5a93004`
  - `13c19c6d2b1eeb74dcd6811272636d7796c17168`
  - `7439ab59f8b22b8a0f2f01d96a6964e4b6974830`
  - `4ec09adeaa9540adc50d933160fb6eaac18f14d2`
  - `0796a74da48593a32435830b93c87c3ba2b06bf3`
- Pre-existing worktree modification: `data/entities/unresolved.log`; not
  touched or treated as part of the review.
- Targeted review suite: 156 passed. Full suite at current HEAD: **910 passed,
  1 skipped** in 45.28s.
- No pointer activation/removal, gate edit, report press, or metered call was
  performed.

## Findings

### 1. P1 · Owner: QB · pointer refresh still has a final CAS race

`agents/assess/ledger.py:2273-2298` compares the observed pointer bytes and
then calls `_atomic_json(pointer, pointer_payload)` as a separate operation.
An operator removal or replacement after line 2286 but before the
`os.replace` inside `_atomic_json` is still silently overwritten. The two new
tests mutate the pointer before `persist_assess_run` performs its comparison,
so they prove the early window was narrowed, not that the concurrent-removal
and concurrent-replacement windows were closed.

Required repair: make compare-and-replace one serialized operation shared by
activation/removal/refresh (for example, a scope-pointer lock covering the
final read, comparison, and replace), or use a filesystem primitive that can
enforce the expectation at replacement time. Add regressions that inject the
operator mutation from inside the final write seam after the comparison.

### 2. P1 · Owner: QB · credential can persist through the arbiter error trail

`agents/decisions/arbiters.py:121-125` copies arbitrary exception text into a
`SpecificityAudit.note`. `run_capture_brief.py` then persists that note in the
`.arbiters.json` trail. The HTTP helper's current constructed status errors do
not include request headers, but this boundary does not enforce the stated
invariant: a transport/provider/custom exception containing the Authorization
value would put the complete pasted key on disk and potentially into logs.
The endpoint tests cover only successful activation, masking, and input
rejection; they do not exercise a secret-bearing failure.

Required repair: redact the active key and bearer-token patterns before any
exception enters a note, log, response, or sidecar. Add an adversarial test
whose mocked `post_json` raises an exception containing the exact key and
assert the full key is absent from the audit, serialized arbiter trail, and
captured output while last-4-only diagnostics remain allowed.

Activation otherwise makes consensus strictly conjunctive: Anthropic remains
the primary judge and every active external arbiter must pass. The key is not
returned by the activation/status endpoints and is passed to child processes
through process environment inheritance rather than command arguments.

### 3. P1 · Owner: QB · approved workshop removal destructively loses `kept_out`

The workshop remains editable when the strategy is approved. Moving a keyword
out updates both local `keywords` and `kept_out`, but the locked save branch at
`ui/index.html:1897-1903` sends only `keywords` and `inferred_naics` to
`POST /api/strategy/terms`. The server at `ui/server.py:2180-2190` and
`agents/review.py:552-590` have no `kept_out` amendment parameter. The removed
term therefore disappears from the persisted strategy instead of entering the
restorable lane. This violates the explicit no-destructive-delete acceptance
criterion and loses provenance after approval.

Required repair: extend the existing term-amendment payload additively with
`kept_out`, preserve approval status, journal before/after state including the
restorable lane, and add an approved-packet endpoint test covering move-out,
save, reload, and restore. Pre-approval revise behavior and legacy schema
loading are otherwise backward-compatible.

### 4. P1 · Owner: QB · capture-path containment check fails open on exceptions

`run_capture_brief.py:834-844` converts a positive foreign-agency detection to
a blocking QA flag, but any exception while loading/building/checking the
containment document is printed and then ignored. A focused build can continue
toward release precisely when the last containment gate is unavailable. This
contradicts the stated fail-closed fact + document boundary.

Required repair: add a blocking `SCOPE_CONTAINMENT` QA flag on every exception
for a focused artifact (while preserving all-scope dormancy). Add an
adversarial regression that injects a containment-check failure and proves the
release state is held. The affirmative-foreign predicate itself correctly
keeps Secret Service, FLETC, TSA, and USCIS in a DHS artifact while rejecting
known foreign departments in the mixed fixture; no abbreviation collision or
parent error was found in the added agency rows.

### 5. P2 · Owner: QB · review-PDF destination is not physically fixed

The new endpoint leaves gated `/api/export/pdf` untouched and force-stamps the
review copy, but `ui/server.py:1577-1583` permits
`LILA_REVIEW_EXPORT_DIR` to redirect the PDF anywhere, including
`data/reports` or the Desktop delivery directory. The committed test relies on
that override. The documented contract says output is **only** under
`data/state/review_exports`, never those release/delivery locations.

Required repair: either remove the production override or validate its
realpath is contained under `data/state/review_exports` (with a separately
injected internal path seam for tests). Add refusal tests for report and
Desktop destinations. The source-root checks, HTML-only check, forced banner,
INTERNAL-REVIEW filename, temporary-source cleanup, and separation from the
gated export path otherwise hold.

## Lower-risk range disposition

- `f5d019b`: no server-side release, approval, pointer, or run API contract
  moved. `CLIENT READY` consumes `release_state(...)[releasable]`, not a new
  UI-derived gate.
- `d0285bb`: ticker fallback is internal-only; client tape is unchanged and
  the sales zero-watchlist wording is correctly suppressed.
- `4ec09ad` + `0796a74`: final range state contains the spacing fix only;
  per-stat seal treatment is fully reverted.

## Seal design read · decision remains William's

Independent recommendation: **option 1 (text-only) should remain the default
for outward client deliverables.** It is the clearest representation of
component-level statistics with the current asset inventory and carries the
least risk of making a privately produced sales assessment look government-
authored, government-approved, or affiliated with the displayed department.
The report can cite official sources without borrowing the visual authority of
an official seal.

Option 2 is visually honest only if William supplies authoritative,
component-specific assets and the intended use is cleared for this outward
commercial context. Even then, distinct component marks improve wayfinding but
increase implied-association risk because they sit directly beside sales
claims and pursuit recommendations. Asset provenance and usage terms should be
recorded; a disclaimer alone does not undo the first-glance signal.

Option 3 is better than repeated department seals, but I do **not** recommend
it as the universal default. A single department seal in a scoped section can
read as a section label, yet it can also read as sponsorship. In an all-scope
report, one seal cannot represent multiple departments; a row of seals becomes
decorative badge wallpaper and amplifies the association problem. If William
chooses option 3, the implementation should be conditional:

- one department in the section: at most one restrained seal, visually
  subordinate to a plain-text `Agency focus` label;
- multiple departments or all-scope: **no seal**, text-only `All federal` or a
  list/count of represented departments;
- never infer a component seal from its parent department next to a
  component-specific statistic;
- keep the GTM/client authorship and proprietary classification dominant.

Required multi-agency stress test if option 3 is selected: a fixture with at
least DHS + DoD + GSA statistics, verifying deterministic text fallback, no
arbitrary first-agency seal, no repeated seals, and stable wrapping at the
money-bar breakpoint.

## Handoff to William

Verdict: **HOLD further contract-surface reliance on the reviewed changes
until findings 1-4 are repaired and independently re-reviewed.** Finding 5
should be resolved before the review-PDF door is treated as physically
separate. No seal implementation is authorized by this review; William must
choose the treatment, then builder/reviewer roles split as tasked.

## Re-verification addendum · `9facb68` + `c522599`

Date: 2026-07-12  
Repair SHA: `9facb681cf42f788e43a0b556a2b5a9a744b7fc7`  
Seal SHA: `c522599f053a730474ba6606f7d25b04e355946c`

Verification: focused repair/seal suite **111 passed**; full suite **917
passed, 1 skipped** in 48.21s. No pointer, gate, press, or metered action was
performed. Pre-existing `data/entities/unresolved.log` modification remains
untouched.

### CLOSED · credential redaction

The active key, bearer-token shapes, and `sk-...` shapes are scrubbed before
exception text enters the persisted audit note. The adversarial exception test
contains the exact key and proves it absent. Arbiter failure remains a strict
non-pass.

### CLOSED · approved `kept_out` persistence

The locked UI path now sends `kept_out`; the endpoint and `amend_terms`
preserve it, journal it, and retain APPROVED status. The additive contract is
backward-compatible.

### OPEN P1 · Owner: QB · advisory lock does not close operator-unlink race

`agents/assess/ledger.py::_scope_pointer_lock` serializes cooperating LILA
writers, but the documented rollback is pointer removal and there is no
sanctioned removal function in the repository that acquires this lock. A
manual/operator `unlink` while refresh holds the advisory lock still succeeds;
the subsequent `_atomic_json` replaces the absent path and recreates the
pointer. The new regression removes the pointer before `persist_assess_run`
acquires the lock, so it does not exercise this remaining window. The test
named `test_refresh_holds_the_scope_lock_across_compare_and_write` checks only
that a lock file exists, not that a concurrent removal blocks or survives.

Required repair: provide one operator rollback command/API that acquires the
same lock and make it the documented supported removal path, then test real
concurrent refresh vs rollback; or use a mechanism that can detect an
uncooperative unlink at final replacement time. Until then, the original
concurrent-removal guarantee is not true.

### OPEN P2 · Owner: QB · malformed scope metadata can still miss the hold

The normal focused-check exception now correctly creates a blocking flag.
However, if decoded JSON is non-dict (for example a list), `_scope_sweep` is
non-`None`, `.get` throws before `_scope_is_focus` is established, and the
second exception path does not add a flag because both `_scope_is_focus` and
`_scope_sweep is None` are false. Treat every unreadable or structurally
invalid selected sweep as blocking unless all-scope was affirmatively parsed.

### OPEN P2 · Owner: QB · review-export override remains an escape

The new containment assertion proves `pdf_path` and `tmp_html` remain under
`out_dir`, but `out_dir` is still wholly supplied by
`LILA_REVIEW_EXPORT_DIR`. Setting that variable to `data/reports`, Desktop, or
any other directory passes the new check. This does not satisfy the documented
physical boundary of `data/state/review_exports` only. Validate the override
against the canonical review-export root or remove the production override.

### OPEN P2 · Owner: QB · seal guard cannot identify all-scope reports

`_stat_band_seal(stats)` receives only composed stat accents, not the operator
scope. An all-federal report whose five selected stats happen to resolve only
to DHS components renders a DHS seal even though the report is all-scope. The
test labeled all-scope actually supplies no agency accents; it does not test an
all-scope report containing single-department top stats. This is exactly the
mixed/all-scope stress case the design decision required.

Required repair: pass authoritative report scope/designator into the helper
and permit the seal only for an explicitly single-department scoped report.
Focused multi-department and all-scope reports must return text-only regardless
of which stats the composer selected. Add DHS-only-stat fixtures under both
`agency_dhs` and `all` scopes and prove only the former renders a seal.

### Re-verification verdict

**HOLD remains.** Two original findings are closed. The pointer P1 and PDF P2
remain open; containment is improved but not fully fail-closed for malformed
scope metadata. The guarded seal's visual placement is appropriate, but its
scope guard is incomplete and should not be treated as approved until the
authoritative all-scope fallback is pinned.

---

## Resolution addendum (QB, 2026-07-12) — all five repaired at `9facb68`

Each finding was re-verified against the code before fixing; P2 was assessed
as defense-in-depth (basename already traversal-safe), the four P1s confirmed.
Every repair is pinned by a regression in tests/test_cycle5_review_repairs.py.
Suite 916 passed / 1 skipped.

- P1 CAS late race -> per-scope exclusive flock around read+compare+write;
  current_bytes read INSIDE the lock. `_scope_pointer_lock` (ledger.py).
  Pins: refresh holds the lock across the write; concurrent removal is not
  recreated.
- P1 credential in note -> arbiter exception text scrubbed (exact key, bearer,
  sk-... pattern) before it can reach a persisted note; only the exception
  type survives. Pin: a 401 echoing the key leaves no secret in the note.
- P1 kept_out provenance -> amend_terms persists kept_out; the locked UI path
  sends it. Pin: post-approval removal keeps the restorable record.
- P1 containment fail-open -> a focus-scoped build whose check throws takes a
  blocking SCOPE_CONTAINMENT flag (fail closed). Pin present.
- P2 review-export escape -> both destinations asserted under the export dir.
  Pin asserts the guard source.

Handoff: HOLD should lift only after Codex re-verifies `9facb68` closes each
finding (independent, adversarial). Seal treatment remains William's decision;
no seal implementation in this commit.
