# Cycle 5 decision memo: scoreboard items 9 and 10

**Decision owner:** William

**Status:** Decision requested; this memo authorizes no code.
**Evidence:** The no-override Cycle 5 packet for NETSCOUT's 2026-07-12
`agency_dhs` sweep, plus the Cycle 4 diagnostic packet for Osprey Flight
Solutions (`all`). Osprey's figures remain comparison evidence, not cutover
authority, because no fresh explicit-scope sweep exists.

## Decision

Choose one policy for the frozen `verdict_totals` and
`counts()["monitor_notices"]` fields:

- **P - Preserve:** keep their established posting-level triage meanings.
  Strict ledger recommendations continue to own the board, watchlist,
  partnering joins, and release gate, but receive separately named internal
  metrics.
- **A - Amend:** redefine the existing fields as strict-ledger recommendation
  counts through a joint labeled contract change, consumer migration, and
  client-copy update.

## Semantic collision

- `verdict_totals["discard"]` formerly meant that metadata triage put a
  posting outside the capability lane. Ledger mode uses it for strict
  `NO_BID`, generally an inactive, expired, awarded, or closed posting;
  metadata-only discards become `research`.
- `monitor_notices` formerly counted every posting with triage verdict
  `monitor`. Ledger mode counts postings in strict families whose current
  record earns `LiveRecommendation.MONITOR`, generally qualifying early or
  market-research notices.

The strict ontology is appropriately more cautious. The defect is reusing
frozen legacy names for different facts.

## Exact measured effect

| Client/scope | Legacy/frozen | Current strict | Preserve delta from current | Monitor effect |
|---|---|---|---|---|
| NETSCOUT · DHS | `discard 17`; `monitor 0`; `research 0` | `discard 2`; `monitor 0`; `research 15` | `discard +15`; `research -15` | `0 -> 0` |
| Osprey · all federal | `discard 291`; `monitor 9`; `research 0` | `discard 69`; `monitor 7`; `research 224` | `discard +222`; `monitor +2`; `research -224` | `7 -> 9` |

Totals remain 17 NETSCOUT and 300 Osprey postings: these are classification
changes, not missing records. Osprey has six deduplicated strict watchlist
families; nine triage-monitor postings must therefore be labeled a screening
population, never nine current watchlist rows.

## Cost and risk

**P - Preserve (small-to-medium):** reconstruct the two frozen fields from the
exact joined posting-level triage. Keep strict `PURSUE / MONITOR / NO_BID /
RESEARCH` totals under a new internal name. Add amendment-family parity tests
and reconcile copy so triage monitors are not equated with strict watchlist
families. Eligibility, board rows, watchlist rows, joins, `can_release()`, count
keys, and count tokens do not change. Risk is limited to naming two distinct
populations clearly.

**A - Amend (medium-to-high):** formally ratify the strict meanings and update
the contract, every consumer, client copy, parity explanations, and historical
comparison guidance. Reusing `discard` for `NO_BID` remains easy to misread;
versioning or adding a `no_bid` field is cleaner but expands scope. The main
risk is silent ambiguity across pre- and post-cutover artifacts.

## Recommendation and ruling

Select **P - Preserve**. It protects historical comparisons and the frozen
surface without weakening a single strict eligibility leg. The product can
then show both truths: what the broad screen called each posting and what the
verified ledger supports now.

**William’s ruling:** `P / A / DEFER`

**Notes:** _Leave blank until decided._
