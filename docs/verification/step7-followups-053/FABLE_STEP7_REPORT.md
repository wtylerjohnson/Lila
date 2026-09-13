# FABLE-STEP7-052 independent adversarial review

Verdict: **PASS_WITH_RESIDUALS (exact tested SHA `67f644bc31bf19eadec628b738855e7b6c152a4e`; no Critical or High finding; acceptance remains root-owned)**

| Item | Value |
| --- | --- |
| Repository | github.com/wtylerjohnson/Lila, branch `codex/step7-evidence-collection` |
| Exact tested SHA | `67f644bc31bf19eadec628b738855e7b6c152a4e` (publication child) |
| Tested code commit | `6b51f005c001d2d99a535e4a302370f9ad80c6ea` (child adds only `docs/verification/step7-052/*`, verified) |
| Accepted Step6 base | `2d522f0362e656e029986344d47e2e06b86a58f8` (verified merge-base) |
| Review checkout | fresh clone `/Users/wtjohnson/lila-step7-052`, working tree clean before, during and after every run |
| Review environment | fresh `uv` venv, Python 3.14.5, httpx 0.28.1, pdfplumber 0.11.10, pytest 9.1.1 (see `ENVIRONMENT.json`) |
| Review only | no merge, no acceptance, no frozen rescore, no seller-ready claim, no live acquisition, no cloud run |

## 1. Custody and reproduction (all true, see `CUSTODY_CHECKS.json`)

- Remote tip equals the candidate; base is the merge-base; three commits in the delta (pre-patch evidence, code, publication).
- 1046 non-doc blob hashes in `LOCAL_VERIFICATION.json` match the checkout; every tracked non-doc file is inventoried.
- `SOURCE_PROOF.tar.gz` (db4f2b12..., 654199 B) and `PRODUCER_PROOF.tar.gz` (69a6e28d..., 138499 B) match the claimed hashes; internal manifests verify 61/61 and 21/21 files with nothing unlisted.
- Every cross-hash inside the verification pack holds (pre-patch report, protocol, fixture manifest, preservation, prior follow-ups register, source manifest, both drivers). The in-repo pack is byte-identical to the Codex handoff copies (read-only comparison).
- Frozen calibration004/005 and the other 15 preserved paths re-hash to the recorded values at review time (read-only).
- Focused suite: 201 passed (`logs/focused-01.log`).
- Full offline strict suite with the 31 staged fixtures: 5864 passed, 40 skipped, 0 failed, 0 errors, 26 subtests, 5930 junit cases. Case-by-case comparison with the producer junit shows zero differences (`results/full_suite_compare.json`).
- Packaged replay driver and boundary probe reproduce on the exact candidate with hash-identical drivers and modules (`results/replay-ihs/`, `results/text-boundaries/`).
- IHS SOWv2 and Q&A PDFs: raw hash and size match; the candidate extractor in the review venv produces text byte-identical to the packaged `.txt` (25483 and 14257 chars); all 19 per-page texts match; all four cited passages slice exactly at the stated page offsets (`results/ihs_pdf_independent_extraction.json`).
- Added lines in the delta contain neither the banned house term nor em-dashes.

## 2. Fixture disclosure (no cloud parity claimed)

The 31 ignored baseline fixtures were staged into the review clone byte-exact from the producer-declared Step6 sources (sha256 verified before and after copy; `results/fixture_staging_receipt.json`). Without them, the eleven referencing modules produce 2 collection errors (`tests/test_contacts.py`, `tests/test_ingest.py` need `data/raw/sample_sam_response.json`), 1 failure (`test_seal_resolution` coverage floor), 8 setup errors (`test_insignary_golden_manifest`), and 21 more skips; under `LILA_SUITE_STRICT=1` the collection errors alone stop the run (`results/fixture_dependency.json`). This is the same shape as the prior cloud runs recorded in S6-FABLE-PROOF. No cloud run was performed here; the parity shown is local-to-local on the same host as the producer, with an independent clone, venv and probes.

## 3. What held under attack

Every property the delta claims was exercised through the real production functions with only the network and clocks mocked (106 probes: 51 positive or claimed-behaviour PASS, 33 FAIL demonstrating a defect or an overstated claim, 22 INFO measuring a disclosed limit). The following held on the exact candidate:

- Original inventory and file bytes are retained before parsing and kept after a parse failure, with hash, size and clock; every error body and every over-cap or timed-out partial body is retained with `complete=false` and never reused.
- A resources body carrying a conflicting `opportunityId`, `noticeId` or `notice_id` at any depth is refused as `identity_mismatch` with the raw bytes retained and no cache written; the all-matching control is `inventory_captured`. Depth reconciliation also refuses explicit conflicts. All 17 recognized real SAM bodies carry the id on every entry.
- Every single-field cache tamper is refused for fresh reuse (text alone, raw object, identity, every caller metadata field, status and authority edits, clock missing/future/naive/non-ISO, path traversal, symlink escape); legacy entries are refetched and their text is never returned on refetch failure. Positive cache hits return the original acquisition clocks, not the current time.
- `confirmed_empty`, `unrecognized_inventory`, `identity_mismatch`, `lookup_failed`, `inventory_captured` and `stale_inventory` stay distinct; nothing failed, partial, non-JSON or unrecognized is ever cached as empty. The 21 real captured bodies reproduce the producer dispositions exactly (17 captured, 4 unrecognized, 0 confirmed empty).
- Restricted, export-controlled, over-size, invalid-id, access-denied, unreadable, unsupported and unfetched files carry the six declared states; the `text_unavailable` fallback is unreachable from the production extractor.
- Combined-text locators are exact Unicode code-point intervals; 46 of 46 rehashed offset, unit, authority, gap, notice and inventory tampers are rejected by the dossier; multi-byte, combining, ZWJ, flag and U+FFFD text survives JSON round trips with hashes intact; a real DOCX with mixed scripts binds exactly (critic probe).
- The 3-file, 150000-character and 14000-character ceilings hold at defaults, with truncation flagged.
- Collected discovery text never reached strict Assess approval or a LeadRow under any hostile case: attachment-only requirement cues with no depth, with a trusted irrelevant depth, with a human review excerpt copied from the attachment, with inventory present, and via triage; the `BID_NOW` positive control stayed live and the normalized records are identical with and without attachment fields. No Step7 `raw_payload` key is read by the Assess ledger or the eight-slot lead layer.
- Step6 controls preserved: the recovered Step6 hostile driver reproduces every original case result (including the disclosed S6-R1-PROBE-DRIFT first-pass FAIL), 791 focused authority tests pass, the full suite matches the producer case by case, and frozen calibration004/005 are untouched.
- Cross-process concurrent publication into the object store is safe (critic probe: 6 processes, 7 digests, readers never saw partial bytes).

The critic's explicit answer to the two blocking questions: discovery text reaching strict Assess approval or LeadRow, **no**; wrong-notice evidence attributed as this notice's research through a production path, **only conditionally and only at discovery_only authority**, requiring a cross-notice body with no explicit id key that no real captured body exhibits, and identical at base (finding F-03).

## 4. Method

Twelve adversarial probe agents, one per demanded dimension, each read the delta and wrote runnable probes against the production functions (network via `httpx.MockTransport` or a patched download seam, sockets blocked, caches redirected to probe-local directories, base versions loaded from `git show 2d522f0:` for comparison). They returned 86 raw findings, which I merged into 22 canonical findings by root cause. Independent refuters then voted on every finding through reproduction, scope and impact lenses (73 votes), and a completeness critic checked coverage, contradictions and merge groups and ran three extra probes. Three lead-reviewer probes confirmed the coherent cache rewrite, the links-only wrong-notice body and the degenerate locator first-hand. Every probe script, log and vote is indexed in `RESULTS.json`; raw finding text is in `results/findings_raw_all.md`.

## 5. Findings, severity-ranked

Kind: new_defect = introduced or left open by the Step7 delta; pre_existing = identical at base 2d522f0; doc_overstatement = code safe, documentation claims more; disclosed_residual = matches a producer or prior residual statement; harness = test or packaging issue.

| Id | Severity | Kind | Finding | Members | Action |
| --- | --- | --- | --- | --- | --- |
| F-04 | Medium | pre_existing | fetch_notice_depth cached branch never checks data[id] against the requested notice; a foreign depth record flows into source_depth under the requested id | WNI-2 | Step8 follow-up: mirror the id check in fetch_notice_depth |
| F-05 | Medium | new_defect | Malformed cache field types raise AttributeError out of both validators and sink the whole notice (FAILED) in run_searches instead of a cache miss | CT-1 | fix recommended before integration |
| F-07 | Medium | new_defect | Per-file receipts misreport stop reasons: after stage-budget exhaustion the unattempted eligible files keep the default reason outside file/type budget; a deadline that fires after a successful download is labelled unreadable; type-filtered and cap-overflow files share one reason | RUF-1, ELD-02, HDP-02, ELD-03, RUF-2, RUF-5, RUF-9, CT-6 | fix recommended before integration |
| F-10 | Medium | pre_existing | Withdrawn attachments (deletedFlag 1, deletedDate set) are projected as live inventory, counted in attachment_inventory_count/hash, and win the 3-file download budget on real notices (3 of 17 captured notices, including the IHS superseded SOW) | FVE-2 | Step8 follow-up |
| F-11 | Medium | pre_existing | Gate-rejected files (restricted, export-controlled, over-size, invalid id) consume the three file slots before any network call; a public readable file ranked fourth is never attempted | RUF-3, ELD-06 | Step8 follow-up |
| F-01 | Low | new_defect | Retention (custody) failure discards acquired bytes and is misreported as a source failure; a poisoned content-addressed blob blocks that notice/file on every sweep; a filesystem without hardlinks fails every collection | BR-1, BR-2, BR-5, CT-8, FVE-7, HCS-01, HDP-01 | fix recommended before integration (see required list) |
| F-02 | Low | doc_overstatement | Text-cache binding is self-referential and cache clocks are unauthenticated: a coherent rewrite (text + text_sha256 + new objects blob, any past aware retrieved_at) passes _text_capture_valid; CONVENTIONS wording overstates the binding | CT-2, BR-3, HDP-04, CT-7, S7C-05, CT-4, CT-5, UO-4 | doc correction and coherent-rewrite test recommended before integration |
| F-03 | Low | doc_overstatement | Identity check narrower than the CONTRACT_SURFACES sentence: only explicit opportunityId/noticeId/notice_id keys are compared (exact case); a body naming the other notice only in _links.self.href or download URL is accepted; raw_capture.source_url records the requested URL not the final redirected URL | WNI-3, FVE-3, HDP-03, WNI-5 | contract-surface correction or identity-walk extension recommended before integration |
| F-06 | Low | pre_existing | Stale-inventory fallback bypasses _resource_capture_valid: tampered/foreign/naive-clock/legacy caches are served as stale_inventory and their unvalidated hash lands in the sweep inventory_receipt; wrong-notice legacy inventory survives 7 days | CT-3, WNI-4, FVE-4, HDP-07, S7C-01, S7C-02, YU-4 | Step8 follow-up |
| F-08 | Low | new_defect | Zero-budget third file produces a degenerate start==end locator recorded as captured_discovery_only and the dossier then drops the WHOLE research context (non-default LILA_SAM_ATTACHMENT_TEXT_CHARS >= 74998) | ELD-05, UO-1, YU-3 | fix recommended before integration |
| F-09 | Low | pre_existing | confirmed_empty is unreachable from any real SAM zero-file shape: the 4 real _links-only bodies and the wrapper-with-empty-attachments shape both classify unrecognized_inventory, so VERIFIED_EMPTY_INVENTORY never occurs in production | FVE-1, HDP-09 | Step8 follow-up; document that confirmed_empty is not reachable from observed live SAM shapes |
| F-12 | Low | new_defect | HTTP capture seam edges: partial bodies retain only whole 64 KiB decoded blocks (small partial bodies retain zero), an oversize 4xx/5xx body surfaces as ValueError losing status/body/retry, a fully received body landing after the stop clock is marked complete=false, observer exceptions mask the in-flight error | BR-4, HCS-02, HCS-03, HCS-04, HCS-07, BR-8 | doc note and observer guard recommended |
| F-13 | Low | doc_overstatement | Dossier prompt carries the research text twice (text field 14000 + file_passages 14000 + relevance_excerpt up to 1200), so about 28.9k research characters reach the model, not at most 14000 | ELD-04, ELD-07 | doc correction or drop the duplicate text field |
| F-14 | Low | new_defect | No per-file signal when the 50000-character extractor cap dropped text; file_text_truncated is false for a 60000-character file because it only measures the bundle cap | ELD-01 | rename or extend the flag |
| F-15 | Low | harness | Harness gaps: the producer strict-Assess negative test is non-discriminating (holds with zero attachment fields), 31 delta branches have no test, one test name overstates, the module restores native _request bypassing the offline observer, and two receipt states (IDENTITY_MISMATCH at run_searches level, text_unavailable) are unreachable dead branches | AAP-1, HDP-05, HDP-06, WNI-7, FVE-5, RUF-7, HDP-08 | make the negative test discriminating before integration |
| F-16 | Low | pre_existing | Pre-existing discovery-text consumers outside the delta: research_evidence labels an attachment-text-only row stored_source_text and can classify a vehicle_route claim as channel_fact; golden_press notice_bridge requirement leg is satisfiable by attachment text alone (admission still blocked by the independent Assess leg) | AAP-2, AAP-3, AAP-4, AAP-5, ELD-08 | Step8 follow-up under S4-ATTACHMENT-AUTHORITY |
| F-17 | Low | disclosed_residual | Upgrade invalidates every legacy inventory and text cache: one-time refetch storm bounded by the 120 s stage budget; if the refetch fails inside that window the sweep yields zero attachment rows where base served cached rows; lowering the text cap later triggers a second storm | YU-1, YU-2, YU-5, YU-6 | warm the caches once outside a press before the first production sweep |
| F-19 | Low | harness | Replay driver synthesizes request_started_at from the previous clock reading while its docstring claims original observed acquisition clocks; retrieved_at values are exact | S7C-03 | narrow the driver docstring or advance the clock before each extract call |
| F-21 | Low | new_defect | Uppercase or mixed-case requested notice id is admitted by the 32-hex regex but always refused as identity_mismatch against SAM lowercase bodies (conservative edge introduced by the new identity check; base accepted it) | WNI-6, FVE-6 | casefold both sides or drop IGNORECASE |
| F-22 | Low | new_defect | raw_capture.source_url records the requested resources URL, not the URL the bytes actually came from; the inventory path follows redirects with no final-host check (the file download path has one) | WNI-1 | record the final URL and chain; check the final host |
| F-18 | Info | disclosed_residual | Object store hygiene: failed-attempt bodies retained as orphan blobs with no on-disk receipt and no garbage collection; unreadable file outcomes not cached; file download error bodies not retained | BR-6, FVE-8, HCS-06, HDP-10, BR-7 | optional reaper for unreferenced objects |
| F-20 | Info | pre_existing | Pre-existing gate and text semantics carried forward: unsupported/extension-less files downloaded before the type check; unapproved redirect host contacted before the host check (its 401/403 now labelled inaccessible); missing access metadata treated as public; binary or UTF-16 bytes named .txt captured as text; code-point cuts can split grapheme clusters; dossier accepts a future attachment retrieved_at | RUF-4, RUF-6, RUF-8, UO-2, UO-3, S7C-04, HCS-05 | none required for Step7 |

Vote summaries per finding (lens, verdict, recommended severity and kind) are in `FINDINGS_AND_RESIDUALS.json`; full refuter reasoning is in `results/verify_merged_reasoning.md` and `results/workflow_verify_partial_raw.json`.

### 5.1 Recommended before operating integration (not gates imposed by this review)

- F-03: correct the CONTRACT_SURFACES sentence (reject any conflicting original notice ID) to the implemented scope (explicit opportunityId/noticeId/notice_id keys) or extend _resource_identity_matches to parse 32-hex ids from _links.*.href and /opportunities/<id>/ paths; all 21 real bodies carry the id in _links.self.href, so the stronger check is cheap.
- F-02: correct the CONVENTIONS sentence (successful caches bind ... raw bytes, text bytes and aware acquisition clock) to state that text is bound to its own object and to caller metadata, not re-derived from raw, and that cache clocks are local unauthenticated fields; add a coherent-rewrite tamper case to tests/test_step7_evidence_collection.py.
- F-05: add AttributeError to the except tuples of _text_capture_valid and _resource_capture_valid (or type-check before .get/.replace) so a malformed local cache entry is a cache miss followed by the bounded refetch, never a notice-level FAILED; add a test with raw_capture set to a non-dict.
- F-07: on the stage-time break stamp every remaining selected file with the true stop reason; map TimeoutError after a successful download to a distinct state rather than unreadable; give type-filtered and cap-overflow files distinct reasons; add tests with a jumped clock.
- F-01: introduce a distinct retention_failed state, keep the parsed inventory or extracted text in the returned dict when only retention failed, fall back from os.link to an exclusive create on OSError other than EEXIST, and quarantine a mismatching objects/<sha>.bin instead of failing forever.
- F-08: do not append a file whose bundle-capped text is empty; mark its receipt not_fetched with a bundle-budget reason so two bound files keep a valid locator chain; add a test at LILA_SAM_ATTACHMENT_TEXT_CHARS=75000.
- F-15: make test_collected_attachment_text_cannot_supply_strict_notice_requirement discriminating (trusted irrelevant depth plus attachment cues; review excerpt copied from attachment; mixed control), and rename or extend the all_inventory_states test.
- F-22: record str(response.url) and the redirect chain in raw_capture and refuse a final host outside sam.gov on the inventory path, mirroring _download_bytes.

### 5.2 Assigned Step7 policy follow-ups

- S4-ATTACHMENT-AUTHORITY: per-file offsets and raw/text custody are implemented and independently verified; no independent trust upgrade was granted or is warranted. Carry F-16 (research_evidence stored_source_text label and notice_bridge requirement leg satisfiable by attachment text) and F-04 (depth cache lacks the id check the delta added to the resources cache) as the remaining safe-propagation items.
- S2-COVERAGE-005: bounded collection is auditable through the new per-file receipts and the 19-family dispositions; explicit-gap fidelity is weakened by F-07 (stop reasons) and by F-10/F-11 (withdrawn and gate-rejected files consume slots), and confirmed_empty is not reachable from any observed real SAM shape (F-09). No full-market recall or comparative relevance claim.
- Step8 candidates outside the delta: F-04 depth-cache id check; F-10 carry deletedFlag/deletedDate/fileExists and exclude withdrawn rows from count, hash and selection; F-11 apply the pure _public_file gate before the [:3] slice; F-09 recognize the real HAL empty shapes only with a matching _links.self.href; F-06 gate the stale fallback on _resource_capture_valid.

### 5.3 Informational observations

- Suite hygiene (pre-existing, outside the delta): during the full offline strict suite one repository test rendered a document carrying the synthetic news link https://n.gov/1; agents/reports/link_integrity.py opens its own httpx client, which the conftest offline observer does not wrap, so the suite attempted a real DNS lookup (failed, nodename not known) and wrote data/cache/links/2026-09-13/<sha>.json into the review clone (gitignored). Same behaviour would occur on the producer machine.
- Fixture staging: the 31 ignored fixtures were staged byte-exact from the producer-declared Step6 sources; the same bytes are present in the operator checkout /Users/wtjohnson/Lila (read-only check). No cloud run; no cloud/local parity claimed.
- Operator legacy cache census (read-only, lead reviewer): 6 inventory caches and 14 text caches without raw_capture in /Users/wtjohnson/Lila/data/cache; all will be refetched once by the candidate (F-17).
- Replay receipt clocks (F-19): in the packaged native-ihs-replay-002 RECEIPT the per-file request_started_at values are synthesized from the previous clock reading; retrieved_at values equal the original observed acquisition clocks. No product path consumes request_started_at.
- Same physical host as the producer; independence is at the clone, interpreter, venv and probe level.


## 6. Retained prior residuals

`PRIOR_FOLLOWUPS.json` (sha256 30ba14ac...) is retained byte-for-byte and is not restated here. All items keep their recorded status, including Step1 R1 to R7, S3-PROOF-008, S4-UI-RELIABILITY, S4-CLOUD-PROOF, S4-BOUNDED-LANGUAGE, S5-PROOF-PARITY, S5-NETSCOUT-REPLAY, S5-PLAN-LAYOUT, S5-RUNTIME-VERSION, S5-AUTHORITY, S6-FABLE-PROOF, S6-FABLE-FIXTURES, S6-FABLE-RUNTIME, S6-NETSCOUT-OBJECT, S6-R1-PROBE-DRIFT and S6-EXPORT-CUSTODY. The two Step7 policy follow-ups S4-ATTACHMENT-AUTHORITY and S2-COVERAGE-005 are addressed in section 5.2. No blanket closure is claimed for any of them.

## 7. Non-claims

No production merge, no automatic Step7 acceptance, no seller-ready lead, no frozen rescore, no NETSCOUT composite acceptance, no live SAM acquisition, no government-source authentication (hash custody only), no cloud/local suite parity, no full-market recall or comparative relevance claim, no independent trust upgrade for discovery text.

## 8. Package layout

```
FABLE-STEP7-052/
  FABLE_STEP7_REPORT.md            this report
  FINDINGS_AND_RESIDUALS.json      machine-readable verdict, findings, residuals
  RESULTS.json                     probe index with statuses and paths
  CUSTODY_CHECKS.json  ENVIRONMENT.json  COMMANDS.json  CONTEXT.md
  logs/        focused-01.log full-01.log full-01.exit replay-ihs.log text-boundaries.log fixture-dependency-*.log
  results/     full-01.xml full_suite_compare.json blob_inventory_check.json preservation_recheck.json
               verification_pack_crosshash.json fixture_staging_receipt.json fixture_dependency.json
               replay-ihs/ text-boundaries/ ihs_pdf_independent_extraction.json
  probes/      lead/ plus one directory per attack dimension, verify/<finding>/<lens>/, critic/
  extracted/   source-proof/ producer-proof/ (hash-verified unpacked archives)
```
