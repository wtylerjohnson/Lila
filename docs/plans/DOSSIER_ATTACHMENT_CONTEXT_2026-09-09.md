# Dossier attachment research continuity

## Pre-patch report

September 9, 2026. Base remains `ab1056f`; the attachment-error repair is
uncommitted on `codex/source-completeness-20260908`. Operating main is unchanged.

### Reproduction and cause

An offline discovery -> dossier -> Assess -> LeadRow reproduction added one
attachment-only candidate. The sweep retained the sentence "The contractor shall
provide enterprise packet capture for network operations" and its file hash.
The fake dossier engine received neither. The parent remained unscreened and
its lead stayed HOLD, with no requirement span.

- `run_searches.py:233-275` retains extracted text, original file URLs/hashes/
  timestamps, bundle fingerprint and relevance excerpt.
- `agents/decisions/triage.py:604-615` consumes the attachment excerpt and binding.
- `agents/decisions/dossier.py:85-97` drops all that research from its whitelisted
  analysis context, passing only notice metadata, description and file inventory.
- `run_dossiers.py:102-119` preserves the authoritative source depth separately.
- `agents/assess/ledger.py:823-827,928,978-1003` intentionally uses verified
  NOTICE description plus exact human review, not unreviewed attachment text.
- `agents/leadgen/from_assess.py:285,929-939` preserves the missing span/hold.

These citations describe the pre-patch files in this development worktree.
The dossier omission is repairable without changing strict evidence ownership.
The downstream hold is not itself a bug to remove in this tranche.

### Minimal patch and acceptance

Extend the existing dossier analysis context only, with an optional bounded
`attachment_research` object. Preserve the current model output schema and the
`full_description` field. The raw sweep already retains the underlying research;
do not rewrite it or create another approval surface.

Require the captured text/file-ID/file-hash fingerprint to match the existing
producer calculation. Preserve original file metadata and extraction times,
not today's time. Bundle-level text must not pretend to identify its individual
source file. Show truncation and original text length. Include a retained
relevance excerpt only when its constituent passages occur in the bound text.
Reject mismatched notice or known-current inventory identity from usable context;
an unreconfirmed inventory must be explicitly labeled as such.

The system prompt must identify this as unreviewed source material, not
instructions or approved requirements. No contract, human review, coverage,
classification, eligibility, priority or release setting changes.

Tests must prove:

1. The fake engine receives valid file research with original provenance.
2. Tampered text/hash, mismatched notice/inventory and malformed metadata are
   diagnostic-only, with no usable passage.
3. A long document is bounded honestly and a valid later relevance excerpt
   remains available; an unbound excerpt is omitted.
4. Missing research preserves the old prompt context; inputs stay unchanged.
5. Strict Assess and LeadRow do not acquire approval, a requirement span or a
   ready tier merely because the dossier now sees attachment research.

## Proof boundary

This is research continuity, not attachment-only BID_NOW support. The contract
at `docs/CONTRACT_SURFACES.md:437-440` still requires reviewed current NOTICE
evidence and a reviewed attachment inventory. Supporting attachment-only
requirements there needs a separate, explicit contract proposal. No live
research, client press, outreach or operating-data change is part of this patch.

## Pre-merge verification checkpoint

At this checkpoint, implemented locally on `codex/source-completeness-20260908`,
not yet committed, merged or released. The production change is confined to
`agents/decisions/dossier.py`; the prior resource-error repair remains separate
in `run_searches.py`. No change to the external product or strict Assess contract.

- Before implementation, the new regression tests produced **26 failed,
  8 passed**. The failures reproduced missing attachment context and diagnostics.
- The dossier group now has **35 passing tests**, including the original tests,
  a sorted two-file producer fingerprint, tampering and inventory identity,
  truncation, original dates, and input immutability.
- The attachment, dossier, strict Assess and LeadRow group passed **109 tests**
  before the final two-file-order regression was added. That additional test
  passed in the subsequent 35-test dossier run.
- The end-to-end synthetic boundary test supplies a `strong_fit` dossier but
  verifies identical Assess state before and after: no approved requirement,
  no approved inventory, no requirement span, and no promoted lead tier.
- Two independent read-only reviews found no material issue in this diff.
- Changed-file Ruff reports **28 pre-existing findings**, all reproduced against
  the base file bytes, with none introduced: 19 in `run_searches.py`, 4 in
  `tests/test_sam_attachment_text.py`, 3 in `agents/decisions/dossier.py`, and
  2 in `tests/test_dossiers.py`. These are not a clean-lint claim.

- Full offline-strict suite: **5,317 passed, 40 skipped, 18 warnings**, zero
  failures or errors, in 134.62 seconds, after restoring exact saved assets
  described in [the source-completeness report](SOURCE_COMPLETENESS_2026-09-08.md).
  Existing expectations and skip markers were not changed.
- `git diff --check` passed. Operating main and its protected existing
  `unresolved.log` modification remained unchanged.

These checks prove research transport and the unchanged qualification boundary,
not live retrieval, model quality, real Arista/Apex yield, or seller readiness.

### Replay command

From this development checkout, with permission for temporary headless Chrome:

```sh
LILA_SUITE_OFFLINE=1 LILA_SUITE_STRICT=1 /Users/wtjohnson/Lila/.venv/bin/python -m pytest tests/ -q --tb=short
```

## Integration, September 9

The operator subsequently authorized merging source commit `44ed2a6` into
operating main. This integration changes no approved scope, requirement source
rule, human review, report design or real-client readiness status. The test
results above are the pre-merge receipt, not a new client release.
