# Architecture — Extensibility & Decision Layers

> Internal name: capture brief. Client-facing name: **Federal Opportunity Assessment**.

This document covers two cross-cutting concerns added on top of the Assess → Target →
Execute pipeline: **how to scale to more data sources** and **where/how Claude makes
decisions**.

---

## 1. Pluggable data sources (scale the inputs)

The system must absorb new APIs as the business grows (GovWin, FPDS, state portals,
a CRM feed) without rewriting the engines. The seam is a small adapter contract in
[`tools/api/base.py`](../tools/api/base.py):

```
SourceQuery ─▶ DataSource.search() ─▶ RawOpportunity[]
                   ▲
        @register_source  (auto-adds the adapter to REGISTRY at import)
```

- **`SourceQuery`** is a normalized query. Callers write it once; each adapter
  translates it into its own API's parameters and ignores what it can't express.
- **`DataSource`** is the ABC every source implements. `kind` is `DISCOVERY`
  (finds opportunities) or `ENRICHMENT` (adds context to known ones).
- **`REGISTRY` + `@register_source`** mean adding a source is a *one-file, one-decorator*
  change. `discover(query)` fans the query across all enabled discovery sources and
  merges results, deduping per `(source, source_id)`.

**Adding a source** (the whole change):

```python
# tools/api/govwin.py
from tools.api.base import DataSource, SourceKind, SourceQuery, register_source
from agents.schemas import RawOpportunity

@register_source
class GovWinSource(DataSource):
    name = "govwin"
    kind = SourceKind.DISCOVERY
    def search(self, query: SourceQuery) -> list[RawOpportunity]:
        ...  # call GovWin, map to RawOpportunity
```

…then import it in `tools/api/__init__.py`. Nothing in Assess/Target/Execute changes —
they consume `RawOpportunity`/`FusedOpportunity`, never vendor shapes. `enabled = False`
keeps an adapter registered but out of the default fan-out (feature-flag a source on/off).

---

## 2. Claude decision layers (contextualize the research)

Deterministic scoring answers *"how well does this match?"*. It cannot answer
*"so what should we do?"* — that needs judgment over messy, document-level context.
That is where Claude plugs in, as **decision layers**.

### The seam: one engine, one contract

[`agents/decisions/engine.py`](../agents/decisions/engine.py) is the **only** place
that calls the Anthropic API. Every layer calls `DecisionEngine.deliberate()` with a
system prompt, a context dict, and a Pydantic schema, and gets back a **validated
`DecisionReport`** — never free-form text. Defaults: `claude-opus-4-8`, adaptive
thinking, `effort="high"`.

The output contract ([`schemas.py`](../agents/decisions/schemas.py)) is built to honor
the same operating constraints as the rest of the system:

| Constraint | How the decision layer enforces it |
|------------|-----------------------------------|
| **Deterministic output** | Claude is forced into the `DecisionReport` schema via structured outputs — no prose to parse. |
| **Decision points** | `DecisionPoint[]` — each fork (bid/no-bid, prime vs. teaming) with options, a recommendation, and cited reasoning. |
| **Artifacts** | `Artifact[]` — pursuit briefs, capability statements, email hooks, drafted with `status="draft"`. |
| **Human-in-the-loop** | Every report carries `requires_human_review=True` and a one-line `review_gate` stating exactly what a human must approve. |
| **Traceability** | The engine feeds source provenance into the context and the system prompt *requires* every claim be cited back; `evidence`/`citations`/`traceability` fields carry the refs. |

### Where the layers sit in the pipeline

```
[ASSESS]  deterministic scores ─┐
                                ▼
                    ┌────────────────────────────┐
                    │ DECISION LAYER (Claude)     │
                    │  contextualize  ← built     │
                    │  target-strategy  (next)    │
                    │  execute-drafting (next)    │
                    └────────────┬───────────────┘
                                 ▼  DecisionReport
                            Review Gate (human)
                                 ▼
                    [TARGET] ──▶ [EXECUTE]
```

The **contextualization layer** ([`contextualize.py`](../agents/decisions/contextualize.py))
is built: it takes ranked `AssessmentResult[]`, asks Claude to think through portfolio
strategy, emit bid/no-bid decision points, and draft a pursuit brief per top target.
Crucially, it **does not re-score** — the deterministic `match_score` is authoritative;
Claude explains and prioritizes it. This keeps the objective and judgment layers
cleanly separated and auditable.

### Adding the next layer

Reuse the engine; write a layer module like `contextualize.py` with (a) a system prompt
encoding that layer's job + the four constraints above, (b) a function that shapes its
inputs into a context dict, and (c) a call to `engine.deliberate(...)`. The two planned
next layers:

- **target-strategy** — win-probability narrative + competitive positioning, fed by
  USAspending award history (the `ENRICHMENT` source).
- **execute-drafting** — turn an approved pursuit brief into finished capability
  statements and personalized email hooks (still `status="draft"`).

### Testing

The engine takes an **injected client**, so layers are fully unit-testable offline with
a fake Claude client (see [`tests/test_decisions.py`](../tests/test_decisions.py)) — no
API key, no `anthropic` install. Tests assert the model/thinking/effort/schema wiring
and that the HITL + traceability guardrails are present, without spending tokens.
