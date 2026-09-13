# S2-SOURCE-002 pre-patch report

Written before test or source edits. Owner: root source-integrity follow-up.
Branch: `codex/source-read-integrity-002`, isolated local clone.
Base: accepted Step3 `cb8448e788cf41d63a8fa1049ac1d9eeecdedf87` imported
from the existing coordinator checkout without modifying it.
Canonical plan: `LILA-WEEKEND-2026-09-11-v1`, SHA-256
`71a4c7c11ee575596357e5e0fcfb66a75d2c855b4761040042732c8cad26bfc5`.

## Evidence and diagnosis

The original Step2 source was `e8ef4e0f15173905fd28ad2c2d242e6572f52c31`.
The existing replacement/rewrite/unlink regression passed 3/3 on macOS APFS;
Fable reported the rewrite case failing in its cloud environment. Local success
does not explain or invalidate that failure.

The completed temporary diagnostic hid metadata changes while rewriting a
40-byte buffered fixture at the same length and restoring mtime. The unchanged
reader returned the original bytes and their correct SHA-256, but also reported
`verified / complete / stable` despite different final on-disk content.
This is synthetic metadata invisibility, not a reproduction of the cloud
filesystem. Original proof is retained in root outputs
`S2_SOURCE_002_DIAGNOSTIC_2026-09-12.{md,json}` and
`STEP2_SOURCE_INTEGRITY_PREFLIGHT_2026-09-12.{md,json}`.
Step3's `_HashingReader` and `_read_extract` retain the same gap.

## Bounded repair

Extend the existing reader and internal census receipt. Keep receipt `sha256`
and `bytes_read` bound exclusively to bytes delivered to the CSV decoder.
After full consumption, independently reopen the selected path, check both
descriptors and path identity/metadata before and after a full raw digest pass,
and require equal content digest and byte count before success. Record the
verification digest separately. Disagreement or unreadable/incomplete verification
must fail named and retain evidence. Explicit metadata consistency and endpoint
content comparison remain distinct from any claim about all transient mutations.

Preserve schema version 1 and its existing keys/status values; additive internal
fields explain the stronger success check. No strict receipt model or public
consumer depends on exact internal keys. The public-safe census projection,
source selection, clocks, gates, qualification, frozen evidence, and all Step4
date/authority work remain untouched. Document the additive semantics separately.

This adds one O(file-size) read/hash per completed scan, using bounded memory.
It checks observed endpoint content and metadata, not immutable storage or every
transient rewrite. Changes after the final observation remain possible.

## Verification and stop boundary

Add a deterministic same-length rewrite with hidden timestamp changes; first
prove it fails on old code, then passes after repair. Add unchanged positive,
verification read/open/stat/incomplete/error and verification-time identity
change cases, keeping the original three mutation regressions intact.
Run focused checks and the complete offline strict suite including report checks.
The 31 existing ignored baseline fixtures are copied only after matching the
original exact fixture receipt hashes. No mutable operating data is imported.

Commit explicit source/test/doc paths only. Return a locally verified candidate
and receipts for root review; no publication, production merge, or Fable dispatch.
