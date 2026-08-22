# Gauntlet completion report · Federal Market Map

Mission (operator brief, 2026-08-07): improve production code until Federal
Sales OS repeatedly produces a certified, evidence-grounded federal
market-intelligence report at least as polished and traceable as the
GOLDEN 87 benchmark, substantially more qualified and actionable, through
one canonical production path. apexanalytix is the proof client.

## Gate status

| Gate | Status | Evidence |
|------|--------|----------|
| A · GOLDEN 87 parity | **PASS** | `tools/reference_contract.py` extracts both documents into structural contracts; `parity()` returns zero findings for every client. Benchmark hash-bound: `fixtures/reference/golden_87.sha256`, enforced by test. |
| B · intelligence superiority | **PASS with named remainders** | vs the benchmark: live/closed clock discipline on every row and rank, per-record identifier-bearing next actions, rejection ledger with reasons, typed research demand, coverage receipt per press, five-lane pursuit board, screened + provenance-graded contacts with direct phones, teaming routes gated by the capability contract. Remainders queued: per-record evidence cards, forecast re-pull, operator frame calls (Omni / TRICARE / FIAR). |
| C · evidence integrity | **PASS** | award/notice/forecast/event source roles enforced by `EvidenceReference` (an award cannot prove an opportunity, by exception); no fabricated URLs; id↔href binding rule; ExactMoney populations; visible unknowns; rejected records disclosed with reasons; stale windows rendered as successor research, never response instructions. |
| D · production integrity | **PASS** | one path: CC `/api/run` → `run_candidate_review --golden-only` → `golden_press` → market-map leg. No hand-edits; no benchmark ids in retrieval (evaluation-only, verified by the SDIB recall fix flowing from the record's own description, not a seeded id); replay = pack + `<stem>.inputs.json`, byte-identical by test; certification fail-closed (uncertified presses park FAILED and never deliver — observed live on riverbed and mark43). |
| E · cross-client reliability | **PASS, leak-free, through production** | 5/5 certified through the Command Center on the settled code, each client on ITS OWN vocabulary: apex 14 opps/17 contacts (contract-driven), riverbed 15/0 (honest thin lane + research order), thinklogical 30/29, varonis 15/1, mark43 16/5. The identical-counts leak (iteration 6) was found by cross-client comparison, fixed at the root, and is guarded by `tests/test_cross_client_leakage.py`. Final jobs: 94562a85fe36 (apex), 42e233ea10e9, ea7bd75a64b8, 929cab3c861d, 45a62ccef856. |
| F · AE urgency (90-second read) | **BLOCKED ON AN EXTERNAL DEPENDENCY, by design** | the gate requires an independent reviewer with no hidden context; the author cannot self-administer it. The artifact to hand them: any client's `<slug>.market_map.client.html`. The seven questions are in the brief; the document's coverage strip, lifecycle band, clock-honest headlines and ranked pursuit board are built to answer them from the top. |

## Artifact inventory (per client, `data/state/candidate_review_v1/<slug>/`)

- `<slug>.market_map.html` — certified studio document (editable, logo slots, drawers)
- `<slug>.market_map.client.html` — client build: stripped of scripts/editing, receipts as native `<details>` blocks, triggers anchored
- `<slug>.golden_report.evidence_pack.json` — the immutable pack
- `<slug>.market_map.inputs.json` — captured store state (replay = pack + this)
- `<slug>.market_map.coverage.json` — sources, freshness, query plan, dispositions incl. named rejected rows, blind spots
- `<slug>.market_map.validation.json` — certification verdict
- `<slug>.market_map.document.json` — structural summary

## Reproduction from a clean checkout

```
cd ~/lila-market-map
.venv/bin/python -m pytest tests/ -q            # 4256 passed, 3 skipped
# press (the ONLY sanctioned path):
curl -X POST http://127.0.0.1:<CC_PORT>/api/run -H "Content-Type: application/json" \
  -d '{"client_name":"apexanalytix","step":"candidate_review","args":{"golden_only":true}}'
# offline replay of a sealed press (no network, byte-identical):
#   press_market_map(pack, inputs=<stem>.inputs.json contents, ...)
# parity:
#   tools/reference_contract.py extract() on fixtures/reference/golden_87.html
#   and on the pressed artifact; parity(ref, cand) must return zero findings
```

## Change log

Six iterations, 36 defects fixed, each traced to its grading finding and
carrying a regression guard: `docs/reviews/gauntlet-defect-ledger.md`.
Grading: 8 independent evaluators + adversarial verification
(62 agents, 54 majors raised, 8 refuted, 46 fixed or queued).

## Known limitations and blind spots

- Forecast lane: the client's forecast pull predates the capability frame;
  every artifact files a `forecast_repull` research order in-document.
- USAspending award deep-links render as identifiers (text) unless a
  canonical URL was captured: the API's generated ids are not derivable
  from a PIID, and fabricated links 404ed. Next source pull should request
  `generated_internal_id`.
- Frame calls owned by the operator, deliberately not auto-decided:
  Omni vendor vetting (physical-access vs supplier), TRICARE claims review
  (medical claims adjacency), the FIAR family (CPA services vs software).
- Notice-store title scans cost ~2 min per press (no title index yet).
- Gate F requires an independent timed read of the final artifact.
