# Step7 merge repair054

The assigned repair is ready for root integration at source commit `8e2bac4f97dec9f166b3881b4226aa36c86c85c3`, based on reviewed `7415686783b9de7a2d0c59187fe294b6afbf2eba`. All 278 focused checks passed on that exact source commit. The source commit includes four production files, three test files, and the two contract documents. This review directory is a separate documentation commit. Root owns the combined regression, independent review, operating-cache migration and authorized cutover.

The main defect was that a depth request could adopt a different notice's description after a redirect, then persist it with a trusted-looking checked flag. Both depth acquisition legs now require retained bytes, a complete successful capture, HTTPS SAM origin, and positive same-notice route identity throughout the observed chain. The description must match its retained response. Legacy descriptions without this custody are withheld, even if the old cache says `description_checked=true`. Reacquisition or a named gap is required.

## Assigned finding map

| Findings | Repair | Evidence and remaining boundary |
| --- | --- | --- |
| N05 | Retain and validate both description and resources captures; reject foreign, off-host, id-less, unrelated-route and HTTP results; redact credentials from custody URLs. | Twelve actual mocked acquisition-to-production-projector-to-Assess/LeadRow cases preserve the same-notice positive and withhold invalid acquisition. Original redirect probes refuse foreign adoption. Post-response custody checks do not prevent initial redirect contact. |
| N02, N09/N13 typed migration scope | Validate depth identity, typed fields, clocks, count/hash, captures and freshness. Reconcile inventory without a SAM key. Retain original legacy JSON bytes before index replacement. Never copy old approvals. | Actual base writer plus 21 saved bodies; eight typed corruptions; valid, unknown and unavailable refresh controls. No-key legacy descriptions remain untrusted. N09/N13 scope does not imply arbitrary malicious-cache immunity. |
| N01, N12 | Document that every one of 17 recognized inventories changes hash, including 13 without withdrawals. Persist withdrawn history, capture references and before/after migration/re-review metadata through `run_dossiers._source_depth_record`. | All 21 saved-body migrations checked through the production projector. Inventory hashes do not carry requirement approval forward. |
| N03 | Read-only custody reads use an existing readable lock or verify bytes without creating a lock. | Original critic read-only bytes and offline inventory probes pass without refetch. |
| N04, N14 diagnostic scope | Treat unencodable extraction as unreadable, preserve acquired raw bytes, and cap safe diagnostic text at 1,000 characters. | Whole native sweep receipt succeeds through the real atomic JSON writer. No raw surrogate enters diagnostic text. |
| N06, N07 | Casefold notice IDs, reject foreign named query/fragment IDs, distinguish resource IDs, record malformed links as uninspectable gaps. | Original uppercase/malformed/lowercase controls retain their row; original wrong-query/fragment collector returns no adopted row. Positive origin capture remains required. |
| N08 | Preserve validated stale inventory after a failed refresh retention; quarantine conflicting files/directories; bound exclusive-create races. | Fault-injected fallback, directory and race tests pass. Stale inventory cannot become current trusted depth. Cooperative locking is not protection from an arbitrary continuous external writer. |

The final053 critic described three regressions: read-only cache access, surrogate sweep persistence, and uppercase same-notice yield. All three are addressed here. The original finding register, scripts and residuals remain intact.

## Validation

- Exact source commit: 278 focused tests passed in 2.67 seconds. `focused-commit.xml` identifies the cases. No full suite was run for054; root requested one full combined regression after integration.
- Six isolated copies of original critic scripts exited zero. Their original `PASS`/`INFO` labels and raw outputs are preserved without rewriting the review verdicts.
- Saved IHS native replay retained the one candidate, four active files and one withdrawn file; loaded the same two retained PDFs; zero added rows, zero attachment-relevant rows, zero metered calls. This is saved-source reproduction, not a new authentic acquisition or recovered lead.
- The unchanged locator driver preserved 150,000 collector characters, the 14,000-character dossier ceiling and exact Unicode offsets, with zero metered calls.
- All 21 saved inventory bodies were written through the actual base depth writer and reconciled. Seventeen were recognized; all seventeen changed hash, including thirteen without withdrawn rows. Individual before/after records are in `migration-commit/RESULT.json`.
- Root's copied operating cache contains six inventory JSONs and fourteen text JSONs, with zero depth JSONs. The offline shadow audit preserved the input hashes and refused unsupported current authority from all twenty. The 21 legacy depth controls therefore demonstrate a separate base-written migration path, not twenty-one operating depth migrations. No approvals were copied. Source-unavailable outcomes require fresh acquisition and requirement re-review.
- All 19,263 regular files in the original053 review manifest are byte-identical. The accepted052 and reviewed053 checkouts retain their exact clean heads. A first membership check accidentally included 166 symlinks excluded from the regular-file start manifest; the corrected check and original failed check are both retained. No symlink-target preservation claim is made.

The first focused attempt had 10 failures and 146 passes because older test doubles bypassed the response observer, used non-SAM short IDs, or assumed no key meant no inventory refresh. Fixtures now exercise observed HTTP responses and 32-hex notice IDs. Later attempts passed 187 and 276 cases; two unrelated-route controls were added before the final exact-commit 278 pass. All attempt logs are retained.

## Original wrong-notice probe limitation

`probe_wrong_notice_attribution.py` parts A and B now refuse the actual wrong-notice acquisitions. Part C still prints `bid_now`: it assigns the acquired result to `adopted`, then never uses that result. It separately hardcodes `DESC_TEXT` into a trusted `_depth_record()` fixture. That output is preserved as evidence of the fixture's scope; it is not a result of the repaired fetch or production projector. The new twelve acquisition cases exercise the actual returned depth through the production projector and native Assess/LeadRow path, including positive controls. Root's independent reviewer owns confirmation on the combined candidate.

## Retained limits and root handoff

This package does not authenticate a coherently forged cache, prove text entailment, add a pre-contact redirect firewall, or establish source freshness from saved bytes. The saved IHS replay retains original retrieval clocks and the existing synthesized request-start clock limitation. The migration audit uses a synthetic acquisition clock and synthetic legacy descriptions. Prior Step1 R1-R7 and canonical Step3/4/5/6/7 residuals remain; only the assigned seams above are repaired with bounded local evidence.

Root should cherry-pick the source commit independently of this documentation commit, review the combined source and renderer changes, run the single full combined regression, reconcile actual operating caches with named gaps and re-review, and perform the authorized operating cutover. Coordinator did not publish, merge, change frozen calibration004/005, run a paid client build, or start another reviewer. NETSCOUT/APFS integration and preservation proof are root-owned.

`LOCAL_VERIFICATION.json` records exact commands, file hashes and artifact custody. `EVIDENCE.tar.gz` contains the bounded output receipts and attempt logs; source replay inputs remain in the previously published053 source-proof package. The original full review is preserved at `/Users/wtjohnson/lila-step7-053-review/FABLE-STEP7-FOLLOWUPS-053/`.
