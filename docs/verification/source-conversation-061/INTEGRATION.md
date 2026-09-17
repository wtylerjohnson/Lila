# UPSTREAM-SOURCE-CONVERSATION-061 integration receipt

Base: `7f44925b8cf1ea8c01fe8e12eb3037260c106f1b`. Isolated branch:
`codex/source-conversation-20260917`. The initial package was a local patch and
native draft review. The operator subsequently authorized commit and merge;
local operating integration is recorded below. No push, client release,
deployment or Assess/Target approval occurred.

## Daily SAM source configuration

The scheduled producer on this machine is
`~/Library/LaunchAgents/com.fsos.sam-extract.plist`, invoking
`~/bin/fsos-sam-extract.sh` at 06:30 local time. Its daily full extract lands in
`/Users/wtjohnson/federal-sales-os/data/cache/sam_extract`, outside Documents.

The staged checkout uses this existing configuration:

```dotenv
LILA_SAM_EXTRACT_DIR=/Users/wtjohnson/federal-sales-os/data/cache/sam_extract
```

The initial review left operating configuration unchanged. The authorized local
integration subsequently created the ignored operating `.env` with this
producer-path setting. It does not start an independent download or substitute
an API query. The portable module still accepts an explicit installation-specific
path instead of hard-coding a user's home.

Ordinary `run_searches.py` invokes `SamExtractSource(existing_only=True)`.
A missing, stale, unreadable or incomplete extract saves a failed-sweep receipt,
returns exit code 2, and leaves the prior successful sweep intact. No live SAM
API census fallback remains. Independently scheduled extract download/ingest
maintenance and bounded public attachment evidence are different operations.

The integrity guard ignores ctime-only changes in its content-identity comparison
while retaining them in receipts. Device, inode, size, mtime, complete EOF,
consumed-byte SHA-256 and independent post-read SHA-256 remain checked. A controlled
chmod during an 84,052-row scan of a Documents copy passed with both hashes matching
the scheduled producer. This validates the comparison without relocating the file.

## Saved investigation recovery

An ordinary Command Center run captures sources and per-subject attempts before
the broad research picture. `run_picture.py --client Veeam --skip triage picture`
can resume the saved investigations under the same approved workstation binding
without collecting sources again. Source cutoff remains unchanged; the separate
synthesis timestamp records the retry. Two attempts per evidence/policy bundle,
12 calls per invocation, 90 seconds per subject. A policy/evidence/profile/vocabulary
change invalidates cached drafts. Failures never receive replacement sales prose.
The captured store snapshot is reused when its source/profile/cutoff binding
matches. This checkout's read-only historical join uses
`LILA_INVESTIGATION_NOTICE_STORE_PATH=/Users/wtjohnson/Lila/data/state/notice_store/notices.db`;
that historical store does not replace the scheduled daily census.

Model generation selects IDs for exact, consecutive captured source passages.
The binder supplies their verbatim whitespace-normalized text and rejects unknown,
reordered, nonconsecutive or cross-source selections. Existing valid drafts still
pass the same exact-text validator. Explicit Federal Register routing blocks are
parsed into source-bound contacts before synthesis; identities are not hard-coded,
office phones are not labeled mobile, and personnel/recruitment blocks are excluded.

One automatic disposition reversal is permitted per stable subject. Any positive
reinstatement from hold/reject requires the existing operator-owned ReviewedSubject
path with current source SHA, named reviewer, primary evidence and rationale.
Further automatic changes are retained as adjudication-required candidates without
an applied overlay. Source changes and cached older bundles cannot reset the count.

## Native delivery and boundaries

The existing Assess → Press → `agents/leadgen/press_html.py` path renders the actual
saved evidence and validated investigations. No new renderer or qualification
engine is introduced. MD30 without a supported named technical owner remains
research, not a seller-ready call. A published office phone is not a mobile number.
Forecast dates and historic awards do not establish current deadlines or demand.

The separate editable React reference report under Desktop has its own active task,
“01 — LILA BUILD · Prompt Lab”. Its seven initial file drifts and later CSS/export
changes have recorded write/build provenance. This patch does not overwrite them.
The earlier ctime-only writer remains unidentified; content drift is a different
observed event with identifiable content-edit commands.

Final review, exact test results, chronological failures, source receipts, browser
QA and patch manifests are in the task's `outputs/Veeam_Phase2_*2026-09-17*`
artifacts. Test counts are regression evidence, not output-quality or market-yield
evidence. The review retains incomplete source coverage, missing contact ownership,
and the separate intermittent workstation-browser timeout rather than hiding them.

## Authorized local operating integration: September 17, 2026

Operator instruction: `committ and merge to main`.

The exact 25-file reviewed patch was committed as
`7ae5a72bd47662563698db76621494f8284a4e4e` and fast-forwarded into local
`main` in `/Users/wtjohnson/Lila`. The named development branch remains available.
Runtime entity observations, generated reports and unrelated NETSCOUT files were
excluded from the commit. No stashing, cleaning, resets or remote push occurred.

Post-merge operating checks passed:

- 139 targeted tests passed in 4.76 seconds from the operating checkout.
- The configured existing-only SAM reader consumed all 84,052 rows from the
  September 17 scheduled extract. Consumed and independent post-read hashes both
  equal `2f79a429bb90a8ed1c9beabf165720007274cdbf7715cdbdc43d488b3bf0de01`.
- In-process Flask `GET /api/build` returned `/Users/wtjohnson/Lila`, revision
  `7ae5a72`, and `stale=false`. This is an application smoke check, not a claim
  that a live server was restarted or a fresh client release ran.
- The four protected operating dirty files retained their pre-merge byte hashes.
  Entity logging for smoke checks used an isolated temporary snapshot.

The prior exact-code strict result remains 6,253 passed, 40 skipped, 22 warnings
and 26 subtests passed. The post-merge check does not reclassify earlier research
gaps, COSS adjudication, assistant wording corrections or the intermittent UI
timeout as resolved. See `OPERATING_INTEGRATION.json` and the dated task output
`Veeam_Phase2_Operating_Merge_2026-09-17.json` for custody and check details.
