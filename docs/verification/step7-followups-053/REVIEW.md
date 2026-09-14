# Step7 follow-ups 053: review guide

Review the full delta from accepted `67f644bc31bf19eadec628b738855e7b6c152a4e`.
The pre-patch map names the twelve root-assigned findings. ROOT_ACCEPTANCE.json
and ROOT_FOLLOWUPS.json are byte-exact original root records. Original Fable
findings are retained; no new reviewer verdict or operating acceptance is implied.

## Changes and proof

The implementation extends the existing SAM cache and collection owners:
notice-link/origin validation, depth-cache identity, validated stale fallback,
withdrawal history, eligibility before file slots, precise stop receipts,
nonempty passage locators and distinct local retention failure. Storage uses
cooperative per-object locks, hardlink publication with an exclusive-create
fallback, and preservation of conflicting objects in quarantine.

F-02 closes an overstated documentation claim, not the coherent rewrite limit.
Text is checked against its own hash/object and caller metadata, not independently
re-derived from original bytes; cache clocks are local unauthenticated fields.
F-15 adds discriminating strict-Assess negatives and an independently supported
mixed BID_NOW control. No trusted schema, gate or qualification policy changed.

LOCAL_VERIFICATION.json records exact revisions, commands, counts and hashes.
VALIDATION_EVIDENCE.tar.gz preserves logs, junit, attempts and replay receipts.
The tests in tests/test_step7_followups.py are portable reproductions of the
assigned findings; the prior review package retains original probe scripts,
votes and logs. Its hostile filesystem fixtures and original absolute paths
belong to that historical package, not to the current checkout.

## Reproduction

Use Python 3.14.5 and the repository test dependencies. Verify the archive hashes
in LOCAL_VERIFICATION.json before unpacking. In an isolated checkout, unpack
BASELINE_TEST_FIXTURES.tar.gz at the repo root and check each file against
FIXTURE_STAGING.json. It contains only the same 31 byte-matched baseline inputs,
not a new fixture population. Never overwrite a differing existing fixture.

Focused command (with the chosen Python interpreter):

```sh
PYTHONDONTWRITEBYTECODE=1 LILA_SUITE_OFFLINE=1 LILA_SUITE_STRICT=1 python -m pytest tests/test_step7_followups.py tests/test_step7_evidence_collection.py tests/test_sam_attachment_text.py tests/test_dossiers.py tests/test_native_notice_bridge.py tests/test_native_surface_contract.py tests/test_assess_source_clock.py tests/test_assess_ledger.py tests/test_external_notice_authority.py tests/test_step6_material_source.py tests/test_triage_prefilter.py -q -p no:cacheprovider
```

The final regression uses offline_guard on PYTHONPATH to refuse external Python
DNS resolution and IP connect/connect_ex calls while permitting loopback and
local IPC. The existing Chrome invocation renders the self-contained PDF smoke
fixture with link checks disabled and keeps Chrome's own sandbox. The Python
guard does not control native browser networking; no OS-wide network isolation
or zero-network-attempt claim is made. No production setting is changed.

The included offline.sb OS profile was separately verified (loopback bind allowed,
external reserved-IP connect denied), but the first full run could not initialize
Chrome's nested sandbox. Temporary-profile browser wrappers also timed out and
were discarded; the standard Chrome smoke passed. These attempts remain in the
validation archive. Python-source attempts and their refusals are recorded.
Offline flags alone are not network enforcement. This is same-host local proof,
not cloud or container parity.

```sh
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH="$PWD/docs/verification/step7-followups-053/offline_guard" LILA_SUITE_OFFLINE=1 LILA_SUITE_STRICT=1 python -m pytest tests/ -q -rs -p no:cacheprovider
```

Unpack the unchanged docs/verification/step7-052/SOURCE_PROOF.tar.gz into a fresh
source-proof directory, then run the derived replay_ihs.py with --repo-root,
--source-root and a fresh --output-dir. The original retrieved_at values survive;
request_started_at is synthesized as disclosed in F-19. The saved IHS inventory
has four active files and one withdrawn file; only the two previously acquired
PDFs are available to the replay. No new source requests are permitted.
The unchanged step7-052/probe_text_boundaries.py verifies the default native
150000-character bundle and the existing 14000-character passage projection.
The inherited duplicate prompt representation remains the separate F-13 limit.

## Retained limits and release boundary

Other ten findings and all prior residuals remain with root's original disposition.
F-09 empty HAL shapes, F-12 transport edges, F-13 duplicate prompt text, F-14
extractor truncation visibility, F-16 downstream authority labels, F-17 cache
warming, F-18 storage hygiene, F-19 synthesized replay start clock, F-20 remaining
gate/text/clock semantics and F-21 mixed-case notice IDs are not broadly repaired.
The new URL/lifecycle cache fields can require refetch of previous cache entries.
Root owns bounded migration, combined NETSCOUT/APFS validation and operating cutover.

Saved-source and synthetic proof do not establish fresh source authenticity,
better leads, live native yield, comparative superiority or production integration.
No new Fable review is launched by publishing this package.
