# One Step7 evidence-collection review

Work FABLE-STEP7-052. Review the exact published candidate supplied in the routing
receipt on `wtylerjohnson/Lila`, branch `codex/step7-evidence-collection`.
The tested code commit is `6b51f005c001d2d99a535e4a302370f9ad80c6ea`; its
publication child adds only files under this review directory. Producer verification
binds all 1046 runtime/test/input files to the tested commit. Check publication
equality before applying its full-regression result to the review candidate.
Accepted parent: `2d522f0362e656e029986344d47e2e06b86a58f8` (Step6).

Read REVIEW.md, PRE_PATCH_REPORT.md, COLLECTION_PROTOCOL.v1.json,
LOCAL_VERIFICATION.json, PRIOR_FOLLOWUPS.json and STEP6_ROOT_ACCEPTANCE.json.
The investigation and equal collection protocol preceded supplemental acquisition
and implementation. Source proof and producer proof archives retain original bytes,
clocks, commands, failed attempts and manifests. No Tyler approval request.

Challenge the full Step7 delta, including optional bounded HTTP response capture,
inventory identity/projection/cache validation, original-byte custody before parse,
file states and cache binding, native enrichment receipts and dossier file passages.
Probe wrong notice, unknown versus confirmed empty inventory, partial/failed HTTP,
restricted/missing/unreadable files, changed raw bytes/text/metadata/cache identity,
Unicode offsets, overlap/gaps, the three-file/150000-character collection limit,
and the 14000-character dossier prompt limit. Attempt to make collected research
grant strict Assess requirement approval; verify mixed original-source positives
and Step6 clocks, companion, material-source, triage, copy and family controls.

Assigned follow-ups are S4-ATTACHMENT-AUTHORITY (per-file provenance and safe research
propagation) and S2-COVERAGE-005 (auditable bounded collection and explicit gaps).
Independent trust upgrade is not granted. Generic semantic entailment, full-market
recall and completed comparative relevance review are outside the closure claim.
All prior canonical residuals remain; preserve them separately from new findings.

Producer tests: focused 201 passed; exact-commit strict/offline full regression
5864 passed, 40 skipped, 20 warnings and 26 subtests passed in 140.41 seconds.
JUnit counts 5930 cases because it includes the 26 subtests and 40 skips.
Thirty-one existing ignored baseline fixtures were staged byte-exact from accepted
Step6; their manifest is included. Missing cloud fixtures must remain explicit.
Do not reconstruct fixtures or assert local/cloud full-suite parity.

For portable evidence inspection, extract SOURCE_PROOF.tar.gz and
PRODUCER_PROOF.tar.gz into two new owned directories. Check their hashes against
LOCAL_VERIFICATION.json and their internal manifests. Both archives have relative
file names. The source archive contains 61 source/protocol/taxonomy files (about
1.3 MB uncompressed), not the entire 249 MB export or SQLite store.

Run from the review checkout, substituting new output paths:

```sh
python docs/verification/step7-052/replay_ihs_portable.py --repo-root . --source-root /path/to/extracted-source-proof --output-dir /path/to/new-native-replay
python docs/verification/step7-052/probe_text_boundaries.py --repo-root . --output-dir /path/to/new-boundary-probe
```

The saved-source driver verifies every packaged input, reconstructs the inventory
from retained original JSON, blocks sockets and uses per-file original acquisition
clocks. It exercises the native enrichment function with one saved IHS family and
the unchanged operating Apex taxonomy. It retains one row, adds zero attachment
matches and grants no requirement approval. The old SOW was not fetched in the
original collection; its replay failure must remain a named uncollected-file gap.

Actual acquisition covered 21 inventory attempts for 19 selected families; every
family has a fetch/stop/pending reason. Seventeen inventories were recognized and
nonempty; four were unrecognized, not empty. IHS SOWv2 and Q&A were acquired within
the declared family budget. They resolve the missing-document question on new
evidence. Contractor-owned software language is present; Apex fit, ISBEE access
and conflicting metadata/prose response dates remain unresolved. Equal ceilings
and inventory coverage do not prove equal completed substantive review.

The native saved-byte replay is not a fresh Step7 live collection, complete UI
release, seller-ready lead, NETSCOUT composite acceptance or operating integration.
Bounded extraction can omit later text. Hash custody is not government-source
authentication or semantic truth. Same-resource edits with unchanged metadata are
not continuously refetched. Preserve frozen004/005, all eight slots, saved history,
Step6 accepted lineage and root-owned NETSCOUT/APFS/ResearchSubject work.

CoS must check live cloud inventory and reuse an active exact-candidate review or
launch ONE Fable. Keep shepherd paused. Export the exact tested SHA, original
transcript, hostile driver, commands, all raw logs/probe results, hashes and final
report to `handoffs/FABLE-STEP7-052/`. Do not replace missing evidence with a summary.
Return findings and residuals for explicit acceptance. No automatic Step7 acceptance,
operating merge, frozen rescore, holdout expansion, client release or Salesforce work.
