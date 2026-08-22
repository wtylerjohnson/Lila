# Step 1 — Intake → Profile → Approve → Search

The front of the system: capture the client, understand them, get **your** sign-off on
the strategy and keywords, then launch the three searches.

```
(1a) intake form ─┐
(1b) site scrape ─┴─▶ Claude profiling ─▶ keywords + pursuit strategy + 3 search specs
                                              │
                                  ⛔ REVIEW GATE — you approve (alerted)
                                              │ approved
                              ┌───────────────┼───────────────┐
                              ▼               ▼               ▼
                          SAM.gov        USAspending     general web
                                                         (Claude web_search)
```

## 1a. Intake form

[`intake/form.html`](../intake/form.html) — a self-contained form the client fills out
(services, differentiators, past performance, certifications, known NAICS, target
agencies). "Download submission JSON" produces an `IntakeSubmission`
([schema](../agents/schemas.py)). Host it anywhere or send the file; no backend needed.

## 1b. Website / public-data scrape

[`tools/scrape/site.py`](../tools/scrape/site.py) — `scrape_site(url)` does a bounded,
same-domain crawl (homepage + about/services/capabilities/past-performance pages),
strips scripts to visible text, and returns a `ScrapeBundle` with every source URL for
traceability. Stdlib parsing, no new deps. **Verified live** against a real site.

## Profiling (Claude)

[`agents/decisions/intake.py`](../agents/decisions/intake.py) — Claude reads the form +
scrape and returns a structured [`IntakeStrategy`](../agents/decisions/schemas.py):
pursuit strategy, **categorized keywords**, inferred NAICS, target agencies, set-aside
angles, and **at minimum one `SearchSpec` each for sam.gov / usaspending.gov / web**.
Grounded in the inputs; `sources_reviewed` records what it used.

## Review Gate (you approve) — enforced in code

[`agents/review.py`](../agents/review.py) persists the strategy as **PENDING**, raises an
alert, and `load_approved()` **raises `PermissionError` until you approve** — so
`run_searches.py` literally cannot launch searches first.

```bash
python3 run_intake.py --submission acme_submission.json    # scrape + profile + ALERT
#   ... review data/review/<client>.review.md ...
python3 approve.py "Acme Federal Solutions LLC" --approve   # or --reject --note "..."
python3 run_searches.py --client "Acme Federal Solutions LLC" \
    --posted-from 09/01/2024 --posted-to 09/30/2024
```

The alert is **pluggable** (`request_approval(strategy, alert_fn=...)`). `run_intake.py`
uses `agents/alerts.py:desktop_alert` — a **macOS desktop/push notification** (with a
banner fallback) plus the markdown review packet. Swap in email/Slack later without
touching the gate.

## The cataloged source sweep (post-approval)

[`run_searches.py`](../run_searches.py), driven by the approved strategy/keywords:

1. **Procurement and awards** — SAM.gov opportunity census, USAspending market,
   award, subaward, incumbent, and period-end evidence.
2. **Official forecasts** — the default contract covers GSA Acquisition
   Gateway, Army, NASA, and HHS; each child emits its own retrieval receipt.
3. **Program and budget signals** — official rulemaking, legislation, SBIR,
   DSIP, budget, oversight, cybersecurity, and agency-specific sources.
4. **Bounded public-web context** — [`tools/api/web_search.py`](../tools/api/web_search.py)
   surfaces forecast, RFI, recompete, event, and trade-press context. It is
   explicitly bounded corroboration, not represented as a census.

The authoritative list, boundaries, and required/advisory classifications live
in [`tools/api/source_catalog.py`](../tools/api/source_catalog.py). Every sweep
stores `source_coverage_verdict` and `decision_coverage_verdict`. Required lanes
that are partial, stale, failed, or not run remain usable evidence, but prevent
the artifact from claiming comprehensive coverage.

## Keys / what runs where

| Piece | Runs now | Needs |
|-------|----------|-------|
| Intake form | ✅ static file | — |
| Site scrape | ✅ live | — |
| Claude profiling | tested offline (fake client) | `ANTHROPIC_API_KEY` |
| Review gate | ✅ live | — |
| SAM.gov search | parser tested vs real-shape fixture | `SAM_GOV_API_KEY` |
| USAspending search | ✅ live | — |
| Web search | tested offline (fake client) | `ANTHROPIC_API_KEY` |
