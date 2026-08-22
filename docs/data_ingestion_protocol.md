# Enhanced Data Ingestion Protocol (Hybrid Fusion)

> **Reject API Literalism. Prioritize Contextual Truth.**
> API metadata is a lead, not a fact. Nothing is presented to a human until it has
> been reconciled against its original source document.

## The four stages

### 1. Discovery Trigger
APIs (`tools/api/sam_gov.py`, `tools/api/usaspending.py`) identify candidate
opportunities and normalize them into `RawOpportunity` records. Output is written to
`data/raw/`. **No `RawOpportunity` may be promoted past this stage as a deliverable.**

### 2. Contextual Enrichment Gate
For every high-probability match, the agent triggers a **Browser Tool**
(`tools/browser/enrichment_gate.py`) to locate the original source document
(PDF SOW/PWS, RFP, agency page), extract its full text, and perform **Semantic
Mapping** — translating agency-specific jargon into the standardized taxonomy
(`semantic_tags`). Output: a `SourceDocument`.

### 3. Data Hygiene & Sanity Check
`reconcile()` cross-references API fields (amount, dates, scope, NAICS, set-aside)
against the document full text. Any mismatch becomes a `Discrepancy`. A `BLOCKER`
discrepancy sets `requires_human_review=True` and writes a record to
`data/discrepancy_logs/`.

### 4. Data Fusion Output
The final `FusedOpportunity.fused_summary` is synthesized from **both** the API
metadata and the document context, with `provenance` listing every source. This is
the only record type the Assess engine is allowed to consume.

## Cross-cutting constraints
- **Deterministic output** — every stage emits a validated `agents/schemas.py` model.
- **Human-in-the-loop** — each stage ends at a Review Gate before promotion.
- **Traceability** — `provenance` / `traceability` fields carry source URLs end-to-end.
