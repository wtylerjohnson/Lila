# Step7 pre-patch baseline

Work REPAIR-STEP7-BASELINE-052. Coordinator began from accepted Step6
2d522f0362e656e029986344d47e2e06b86a58f8 in the isolated
`work/step7-evidence-collection`, branch `codex/step7-evidence-collection`.
The canonical weekend plan hash is 71a4c7c11ee575596357e5e0fcfb66a75d2c855b4761040042732c8cad26bfc5.
Root acceptance and FOLLOWUPS.json remain controlling. No product code changed
before this report. Existing operator failed on usage limit; coordinator owns this
bounded investigation. Old dirty coordinator and accepted root checkouts are preserved.

## Local and supplemental evidence

The local September12 daily export contains 85,345 rows and 249,460,472 bytes,
SHA256 6f2f50e3a405483bfb2ac9d012179afe7871248cddcfc8bb0ec87672d92e4ee7.
The existing native consumed-reader completed its independent content check.
Original bytes and complete selected rows are retained in local-source-002/.
The read-only operating-store opening failed in the sandbox; attempt001 is
preserved. An owned byte snapshot allowed the read-only lookup: 50 matching
full-solicitation records. No operating database/gate or backup was changed.
The filename date is export vintage, not acquisition or upstream publication.

COLLECTION_PROTOCOL.v1.json was written before supplemental requests, SHA256
28ba62eb3cfd16ed77a9bf0fd34064fbd13234513d5866f55ff969a48456b6b1.
It applies the same 120-second per-family, three-public-file, 5MiB/file limit and
stop rule to 17 LILA and two GovTribe selected families. A shared local scan and
21 bounded inventory requests cover all 19 families, including tied latest
members. HTTP200 yielded 17 recognized nonempty inventories and four unfamiliar
schemas. The latter remain unknown; none is called confirmed empty.
Responses, requested/returned identities, original bytes/hash and clocks are
preserved in public-inventory-001/. No new search, frozen rescore or blind review.
File allowance is a ceiling, not a quota. This baseline's specific unresolved
scope question was IHS; other file lists remain explicitly not fetched, and no
new product-wide relevance adjudication is claimed.

## IHS question resolved to substantive scope evidence

Frozen selected notice571de8be090841b7b6c9bfdc7f76bb8d / solicitation75H70926R00002
references an absent SOW. The local export contains amendment
0caab6e53d8b47d7850692b7684610bf from the same full agency/office/solicitation,
posted September10, metadata deadline September17 13:00 -06:00. It names SOWv2
and Q&A while retaining old September11 prose. Keep the amendment, old member,
precise metadata/prose discrepancy and history; do not assume a current actionable
clock or substitute it into frozen005.

The public SAM inventory binds the amendment ID and five public files. Retrieved
SOWv2 resource9170a0e92059434a808821358dabe89f and Q&A
b38d5c05511a4ff7bb8c54baf9361acb completed by 73.59seconds from IHS inventory start.
Original PDFs, extracted text, per-page text, response receipt and visual checks
are in ihs-files-001/. PDF raw hashes are respectively
b5bbb5a92e71a355f1976fbd7d6eb34259d839ca0b9b25bdb6b63ea766a1730e and
c5fb396add6fce5d3594ffdecf73a6e1275d1f59e2d8e3a5c61315076912503e.

SOW pages3-4, sections2.1/2.3/2.5, establish medical coding, billing, accounts
receivable and healthcare revenue-cycle services. Q&A page7,43-47, retains both
Government RPMS/TPB/AR use and contractor-owned revenue-cycle software/platforms.
This resolves the missing document question. Against the frozen Apex brief,
it does not establish a supplier/payee-control or accounts-payable recovery
requirement. Medical coding alone is explicitly outside that brief's qualifying
scope. Keep the software statement; do not misstate this as no software purchase.
No source-backed Apex match or ISBEE access route was established. This is a
custodian scope assessment on newly collected sources, not a frozen score or
independent domain verdict. Acquisition today is not proof of availability at
an earlier cutoff. Human/native requirement approval remains separate.

## Reproduced code defects before patch

1. S7-RAW-001: `sam_attachment_text.extract_public_attachment_text` stores only
extracted text and a hash; the downloaded original bytes are discarded. Parse
failure discards even the acquired hash/clock and becomes an undifferentiated
error. Native synthetic result and written cache inventory are preserved in
prepatch-probes-001/.
2. S7-IDENTITY-002: public inventory for requested notice a*32 accepts a response
explicitly identifying b*32, setting resources_checked=true. This must not
establish attachment support for the requested notice. Same directory contains
actual native-function result.
3. S7-LOCATOR-003: the native enrichment joins up to three file texts without
per-file positions; only successful file summaries survive. Unsupported,
restricted, unreadable and not-fetched files collapse into TEXT_UNAVAILABLE or
error strings. Dossier context cannot attribute an excerpt to one file.
4. S7-CACHE-004: attachment cache hits return unchecked JSON keyed only by resource
ID, with no retained-byte/text binding or changed-inventory binding. A historic
cached text cannot demonstrate the file bytes collected for current review.

## Smallest authorized repair and acceptance

Extend the existing HTTP optional capture seam, public SAM inventory/attachment
collector, native search enrichment receipt and dossier research context. Preserve
original bytes in content-addressed internal artifacts; bind bytes, extracted
text, resource identity, inventory and exact Unicode character offsets. Keep
file and inventory states, truncation and collection clocks explicit. Revalidate
cached evidence; changed inventory/bytes must not borrow a prior receipt.

This is a separately documented discovery-collection contract extension, not an
Assess identity/schema migration or a trust upgrade. Exact attachment passages
remain discovery_only. Existing reviewed NOTICE evidence, clocks, coverage and
release gates continue to govern Assess/LeadRow. No APFS mapper/ResearchSubject
schema3/NETSCOUT component changes are planned.

Verify original-ID positive and mismatch negative, confirmed empty vs unfamiliar/
failed inventory, raw-byte retention after parse failure, restricted/unfetched/
unsupported states, cache tamper and changed metadata, precise multi-file offsets,
native search-to-dossier preservation and strict attachment-only refusal. Re-run
Step6 authority/family tests and required full offline strict regression on a
committed candidate. Keep authentic saved replay, live acquisition, fixture tests,
full suite and seller-ready yield separate. One exact-candidate Fable review is
required before Step7 acceptance or root integration.
