# Candidate Review v1 Chunk 4 checkpoint

Recorded: 2026-07-22 17:18 MDT

## Decision

**COMPLETE; FINAL GATE 3 GO.** The optional OpenAI intake and inference
collaborator is implemented as an independent, bounded advisory lane. It
cannot modify the approved Analyst Layer, alter the Candidate Review document,
or turn model disagreement into an automatic pursue, no-bid, rejection, or
disqualification decision.

No live OpenAI provider call was made during implementation, tests, or release
verification. Claude was used only for the named, read-only Gate 3 review.

## Implemented behavior

- The collaboration contract supports `off`, `advisory`, and `required`
  modes. Advisory failure continues with an explicit degraded state; required
  failure pauses cleanly.
- Claude and OpenAI receive separately reconstructed copies of the same
  sanitized input. Neither provider receives the other provider's answer.
- Intake suggestions cover capability and problem terms, keywords, NAICS,
  PSC concepts, agencies, competitors or incumbents, adjacent concepts, near
  misses, counterarguments, assumptions, and evidence gaps.
- Inference suggestions require an evidence-bound chain, counterevidence,
  falsifier, trigger, and next validation step. An OpenAI-only hypothesis can
  surface only after deterministic evidence and link validation.
- Comparison is deterministic and produces agreements, provider-only
  suggestions, classification conflicts, unsupported assumptions, and Analyst
  questions. Comparison never mutates the approved packet.
- Provider outputs, comparison artifacts, receipts, and failures use strict
  immutable contracts. Exact cache identities include the input or evidence
  hash, exact dated model, actual prompt hash, actual schema hash, and contract
  versions.
- The default OpenAI model is the dated snapshot `gpt-5-2025-08-07`.
  Floating aliases can run but are not treated as stable cache identities.
- OpenAI usage must be present, nonnegative, exact-integer typed, and
  internally reconciled. Requests and replies have aggregate byte and item
  ceilings, and only one structured-output repair is allowed.
- The API key remains in process memory, is masked without revealing a suffix,
  is omitted from browser payloads and persisted artifacts, and is removed
  from Claude child-process environments.
- Claude Max runs from a temporary working directory under a positive
  environment allowlist with empty MCP, tool, slash-command, browser, session,
  auto-memory, telemetry, updater, and error-reporting surfaces.
- The existing report collaborator control is not activated by this chunk.
  Runtime status is honest when the mode is off or no session key exists.

## Privacy and evidence boundary

The provider projection is an explicit positive allowlist. Internal client,
run, scope, profile, artifact-binding, and evidence-record hashes remain in the
local provenance fingerprint but are omitted from provider input. Unknown
fields cannot add themselves to the projection.

The sanitizer normalizes and repeatedly decodes Unicode, percent-encoded, and
HTML-entity text through a bounded fixed-point loop. It redacts credentials,
API keys, tokens, PEM material, email addresses, and domestic or international
telephone numbers. URLs are stripped of query strings and fragments and must
resolve to public, externally routable hosts. Internal, private, reserved,
single-label, ambiguous numeric, sensitive-host, and sensitive-path URLs fail
closed.

Trusted evidence flags survive only on an exact `EvidenceRecord` that is
rebuilt and revalidated. Raw mappings cannot self-assert official or primary
source status, and subclasses or invalid constructed objects are rejected.

## Findings closed before Gate 3

The first serious independent reviews returned HOLD. Those findings were not
waived or hidden. The implementation was refrozen only after regression tests
closed each class:

1. Credential, contact, encoded-text, and internal-URL disclosure paths.
2. Ambient Claude environment, global instruction, browser, MCP, memory,
   telemetry, updater, and error-reporting inheritance.
3. Cache identity that did not yet bind the actual prompt and schema bytes.
4. Weak exact-type and trusted-evidence reconstruction boundaries.
5. Shared provider request objects and incomplete internal mutation
   fingerprinting.
6. Missing aggregate request, reply, suggestion, token-usage, and repair
   limits.
7. Decoder convergence and hidden-encoding performance edge cases.
8. Error-stream, response-ID, and cached-failure redaction boundaries.

An early superficial Claude GO reviewed a stale snapshot and was explicitly
superseded. Only the final review of the refrozen tree counts as Gate 3.

## Verification evidence

- Focused collaboration suite: **428 passed**.
- Full Candidate Review v1 suite: **755 passed**.
- Adjacent assessment, refresh, report, UI, intake, and arbiter suite:
  **342 passed**.
- Ruff: passed.
- `git diff --check`: passed.
- Independent final privacy and contract audit: **GO**.
- Independent implementation-consistency audit: **GO**.
- Claude Code Max final read-only Gate 3 audit: **GO**.
- No live OpenAI, federal-source, browser, or web call was made by the tests.

The final independent audit additionally reran focused credential-boundary,
evidence-trust, mode, deterministic-comparison, and Analyst-protection probes.
It reproduced no credential leak, silent canonical mutation, or
disagreement-driven disqualification.

## Next step

Begin Chunk 5. Promote the locked eight-section Candidate Review structure
into a deterministic, standalone, editable HTML renderer while preserving the
ticker, scoreboard, source links, drag/drop seals and logos, text and image
resizing, candidate reorder/delete/undo, calendar, print, save/reset, and
download behavior.
