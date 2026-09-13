# Step7 follow-ups: pre-patch map

Work REPAIR-STEP7-FOLLOWUPS-053 starts from accepted
67f644bc31bf19eadec628b738855e7b6c152a4e. Root selected twelve findings;
the original Step7 verdict and other ten findings remain unchanged.

| Finding | Existing function / observed defect | Required regression |
| --- | --- | --- |
| F-03 | sam_notice_detail._resource_identity_matches ignores notice links | nested self/notice links reject foreign ids; resource ids stay distinct |
| F-04 | fetch_notice_depth trusts a foreign cache id | foreign/malformed cache cannot provide depth; bounded refresh or named gap |
| F-05 | resource/text validators assume field types | wrong raw_capture/clock/container types refetch without notice failure |
| F-06 | fetch_notice_resources stale fallback skips validation | failed refresh rejects foreign/legacy/tampered cache; valid stale remains labeled |
| F-22 | inventory capture records requested URL as source | retain request/final/chain; refuse off-host and foreign-notice redirects |
| F-10 | inventory projection drops withdrawal flags | keep history; active count/hash/selection exclude withdrawn files, including saved IHS |
| F-11 | run_searches slices before the public-file gate | three rejected files cannot starve the next eligible public file |
| F-07 | default file reason survives time exhaustion | every selected remainder gets actual stop; parse/download/type/file/text stops distinct |
| F-08 | zero remaining bundle budget appends empty locator | two nonempty bound files survive; third produces explicit budget receipt |
| F-01 | retain_bytes failure is misreported as source failure | local retention failure preserves diagnostics without usable evidence; hardlink fallback and concurrent readers; conflicting bytes quarantined |
| F-02 | conventions imply independently authenticated raw/text/clock binding | document internal consistency and unauthenticated clocks; coherent rewrite remains a passing limitation control |
| F-15 | strict-Assess negative lacks trusted-depth positive prerequisites | irrelevant checked depth plus copied attachment review stays held; independently supported mixed control remains BID_NOW |

Evidence: original FABLE_STEP7_REPORT.md, FINDINGS_AND_RESIDUALS.json,
ROOT_FOLLOWUPS.json and runnable probes in the retained FABLE-STEP7-052
package. The reviewer reproduced these against the accepted exact base.
No fresh acquisition is needed to reproduce them.

Extend existing content-addressed custody, atomic JSON writes, original-id checks,
public-file gating and mocked native collector tests. Storage and additive receipt
changes are explicitly described in CONVENTIONS.md and CONTRACT_SURFACES.md under
root's standing repair authority. Trusted Assess schemas, product gates and
NETSCOUT/APFS surfaces are outside this diff.

Verification: focused positive/negative and saved-source controls, then the full
strict/offline suite with byte-matched fixtures. Offline flags alone are not
network containment. Record actual containment, failures and exact revisions;
do not relabel local reproduction as cloud parity or seller-ready proof.
