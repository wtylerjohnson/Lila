# Native consumed-extract receipts (2026-09-10)

## Pre-patch evidence

`SamExtractSource.search` calls the normal `download_extract`, which selects
`opportunities_<date.today()>.csv`, reuses a credible same-day file, and otherwise
downloads and promotes today's extract. Its census counts rows but identifies no
consumed file. `attachment_candidates` separately calls that downloader and reads
the resulting path twice. A local date rollover can legitimately select a new
file between primary and attachment scans. Neither call resets its last census
before a failure, so a reused adapter can retain the previous successful census.

The native runner preserves the complete primary census at `results.sam_census`
and the complete successful attachment census at its `attachment_candidates`
key. Its exception paths currently omit failed source census detail; that runner
integration is tracked separately. The primary CSV selection is ordinary daily
refresh behavior, not an upstream historical snapshot contract.

## Bounded design before implementation

Extend the established census with `extract_selection` and `extract_receipts`.
Keep the native downloader, query filters, row ordering, amendment selection,
candidate limits, approval controls, and source scope unchanged. Capture UTC and
host-local calendar bounds around the actual downloader call. Label the filename
date as a local cache date, never an upstream publication date.

Use a hashing raw reader beneath the existing UTF-8 replacement decoder and CSV
iterator. Hash and count the exact bytes consumed, without a separate path reopen
for hashing. Verify full EOF, byte count, and file descriptor/path identities and
metadata before/after the scan. Fail named on replacement, mutation, incomplete
read or read failure. The attachment second pass also binds to the first pass's
file identity and digest; no mixed-version amendment/candidate scan can complete.

Reset instance census at each public call before selection. A failed download
retains its selection bounds with no invented file receipt. Failed reads retain
partial-byte hashes and explicit failure state. A skipped attachment call says
it was not scanned. Only completed stable scans can say `status=verified`.

## Offline verification plan

Dedicated controlled-date fixtures exercise the native downloader's cached and
download paths with fake HTTP payloads, midnight rollover, primary and both
attachment reads, exact raw-byte digest including replacement-decoded UTF-8,
concurrent replacement/mutation, interrupted scan, stale-census reset, and JSON
propagation through the existing runner enrichment seam. No actual source query,
network request, LLM, production write, or frozen benchmark case modification.

Focused command (root owns full-suite integration/release proof):

```
LILA_SUITE_OFFLINE=1 LILA_SUITE_STRICT=1 /Users/wtjohnson/Lila/.venv/bin/python -m pytest tests/test_sam_extract_source_receipt.py tests/test_sam_extract.py tests/test_extract_floor.py tests/test_extract_archive.py tests/test_sam_attachment_text.py -q
```

## Focused verification result

The documented command passed: 75 tests, 0 failures, 0 skips in 2.18 seconds.
Twenty dedicated receipt cases include a serialized native-run success and
download/read failures that retain their failed extract evidence through the
unchanged live-API fallback. Attachment download/read failures likewise retain
their exact census through the runner's existing safe enrichment wrapper.
`git diff --check` passed. This is controlled offline proof; no live extract,
benchmark capture, score, full-suite certification or release was performed.
