# Producing Verified Data + LLM Fit Logic

The system turns live SAM.gov and USAspending.gov queries into **specific, verified
records**, then has Claude generate the **logic for why each is a fit** for the client.

```
SAM.gov  ──▶ RawOpportunity ──▶ VERIFY ──▶ USAspending evidence ──▶ Claude fit-logic ──▶ Review Gate
(notice)     (mapped fields)   (field      (real incumbents,        (FitRationale:
                                checks)     median $, award URLs)     why it fits + cites)
```

## Run it

```bash
export SAM_GOV_API_KEY=...      # free at https://sam.gov  (or api.data.gov)
export ANTHROPIC_API_KEY=...    # for the fit rationale
pip install -r requirements.txt

python3 run_pipeline.py \
  --profile data/raw/client_capability_profile.example.json \
  --posted-from 09/01/2024 --posted-to 09/30/2024 --limit 20
# add --no-llm to produce verified data only (no Claude call)
```

Output: `data/cleaned/pipeline_<from>_<to>.json` — one record per verified
opportunity with `opportunity`, `verification`, `market_evidence`, and `fit_rationale`.

## Stage 1 — Discovery (SAM.gov, live)

[`tools/api/sam_gov.py`](../tools/api/sam_gov.py) calls the Get Opportunities API v2
and maps each notice to `RawOpportunity`, keeping the full payload and the public
`uiLink` for traceability. NAICS fan-out + dedup are handled per query. `postedFrom`/
`postedTo` are required by SAM (≤ 1-year span). Parsing is verified against a
real-shape fixture ([`data/raw/sample_sam_response.json`](../data/raw/sample_sam_response.json))
so it's testable without a key.

## Stage 2 — Verification ("specific, verified data")

[`tools/api/verification.py`](../tools/api/verification.py) runs deterministic field
checks: identity + a traceable source URL (BLOCKERs), NAICS format, date sanity
(deadline after posted, not closed), value sanity. **Only records with no BLOCKER pass.**
This is the field-level half of the ingestion protocol's hygiene check; document-level
cross-referencing (browser) is Phase 2.

## Stage 3 — Corroboration (USAspending, live, no key)

[`tools/api/usaspending.py`](../tools/api/usaspending.py) → `market_evidence(naics, agency)`
pulls real comparable contract awards: **top incumbents, median/total/max award size,
and citable award URLs**. `normalize_agency_name()` maps SAM's `fullParentPathName`
to USAspending's toptier name (e.g. *"VETERANS AFFAIRS, DEPARTMENT OF…"* → *"Department
of Veterans Affairs"*) so evidence is agency-specific; it falls back to NAICS-wide if
the agency can't be matched, so evidence is never empty.

## Stage 4 — LLM fit logic ("why we think this is a fit")

[`agents/decisions/fit.py`](../agents/decisions/fit.py) feeds the **verified** opportunity
+ market evidence + Capability Profile to Claude (`claude-opus-4-8`, adaptive thinking,
effort high) and gets back a structured [`FitRationale`](../agents/decisions/schemas.py):

- `verdict` (strong/partial/weak/no fit) + `fit_summary` narrative
- `matched_capabilities` — each client capability tied to the **exact** requirement text
- `gaps`, `risks` (incumbent strength, dollar mismatch, deadline, set-aside)
- `market_evidence_note` — how USAspending corroborates
- `recommended_action`, `confidence`, and `citations` (SAM + USAspending URLs)
- `requires_human_review = True`

The system prompt forces Claude to **ground every claim in the provided data and cite
sources** — it explains fit against verified evidence, it does not invent requirements.

## What's verified live vs. key-gated

| Stage | Status |
|-------|--------|
| USAspending corroboration | ✅ runs live now (public API, no key) |
| SAM.gov discovery | needs `SAM_GOV_API_KEY` (parser verified against real-shape fixture) |
| Claude fit logic | needs `ANTHROPIC_API_KEY` (engine tested offline with a fake client) |
