# Step 1 · Name-only company mastery → Review → (optional auto-approve)

LILA Step 1 starts from a **company name only**. The intake form is optional
enrichment. Before any opportunity identification or lead generation, LILA
resolves the correct company, ingests the official site plus structured web
research, writes an evidence-backed dossier, screens vocabulary against the
notice store, and runs an adversarial challenge. Downstream search still
requires an approved packet **and** an operator-owned engagement scope.

```
company name (+ optional form fields)
        │
        ▼
 identity resolution (bind official domain, or abstain / ask one question)
        │ bound
        ▼
 identity-bound website ingest (scrape + capability_ingest)
 + structured web probes (offerings, federal footprint, channels, rivals, boundaries)
        │
        ▼
 dossier (claims with states) + retrieval units + yield sidecar
        │
        ▼
 intake adversarial (fail-closed; incomplete review is not a pass)
        │
        ▼
 review packet + E1-E8 readiness receipts
        │
        ▼
 decide()  ·  default: auto-passthrough (LILA_ENABLE_INTAKE_AUTO_APPROVE=on)
             restore the human click with =off or --no-auto-approve
        │
        ⛔ load_approved() still required before run_searches.py
        ⛔ scope.preset stays empty until the operator sets it
        ⛔ no outreach is sent
```

## Name-only entry

```bash
python3 run_intake.py --client "Acme Federal Solutions LLC"
python3 run_intake.py --client "Acme Federal Solutions LLC" --website https://acmefederal.com
python3 run_intake.py --submission acme_submission.json   # optional enrichment
```

Command Center:

- `POST /api/intake` with `{ "client_name": "..." }` (form fields optional)
- `POST /api/run` with `{ "client_name": "...", "step": "intake" }` when no
  review packet exists yet (name-only bootstrap)

`--client` and `--submission` are mutually exclusive. The job command uses
`--client` when no submission path is supplied.

## Identity gate

`agents/intake/identity.py` runs **before** deep parallel research.

- Candidates from the form website (if any) and from web search
- Bind only when one official domain clears the confidence floor (0.72)
- Two high-confidence domains → **abstain** with one question
- A low-confidence website guess is **not scraped** (wrong-company guard)
- Abstain/block does **not** auto-approve, even when the passthrough toggle is on
- If `do_web` is on and no research engine was injected, `run_step1`
  constructs `research_engine()`. A forgotten engine is not an offline
  abstain. `--no-websearch` still skips live identity search on purpose.

## Dossier, yield, adversarial, readiness

Sidecars next to `data/review/<slug>.review.json`:

| Sidecar | Contents |
|---------|----------|
| `.dossier.json` | Identity, evidence ledger, offerings/boundaries/channels, keywords, NAICS with rationales, capability statements, unknowns. Claim states: `company_asserted`, `corroborated`, `inferred`, `disputed`, `unknown`. |
| `.intake_yield.json` | `term_yield` against the notice store, plus a store census. Empty/unreadable store is named as such; it is not a list of zero counts. No health verdict. |
| `.intake_adversarial.json` | Challenges against citations and yield titles, not dossier prose. Incomplete review ≠ pass. One bounded repair may drop unsupported claims. |
| `.intake_readiness.json` | E1-E8 receipts (identity, ingest, probes, company model, retrieval units, yield, adversarial complete, adversarial not fail-closed). |
| `.intake_retrieval.json` | Capability statements plus `hybrid.frame_lanes` query sets for later opp retrieval. No retrieve is run at intake. |

The review markdown appendix prints identity, E1-E8, yield titles, and
adversarial findings.

## Approval passthrough

The gate **mechanism** is unchanged: `request_approval` writes PENDING,
`approve.py` / `decide()` / `load_approved()` still exist.

Default behavior is an **explicit** auto-passthrough:

```
LILA_ENABLE_INTAKE_AUTO_APPROVE=on    # default when unset
LILA_ENABLE_INTAKE_AUTO_APPROVE=off   # restore the human click
python3 run_intake.py --client "Name" --no-auto-approve
```

Passthrough only calls `decide()`. It still generates client files on
approval, still leaves `scope.preset` empty, and still does **not** launch
`run_searches.py`. Identity abstain/block never auto-approves.

## After approval

```bash
# only after load_approved() succeeds, and only after the operator sets scope
python3 run_searches.py --client "Acme Federal Solutions LLC"
```

The cataloged source sweep, review workshops, and release gates are unchanged.
See [CONVENTIONS.md](CONVENTIONS.md) for the storage map and gate doctrine.

## Keys / what runs where

| Piece | Runs now | Needs |
|-------|----------|-------|
| Name-only CLI / API | yes | company name |
| Identity bind | yes (offline form URL; live uses web search) | optional engine |
| Site scrape + capability ingest | yes, after bind | network |
| Structured probes | yes, after bind | engine / Max plan |
| Dossier / yield / adversarial / E1-E8 | yes, offline-capable | notice store optional |
| Review packet | yes | none |
| Auto-passthrough | default on | `decide()` |
| SAM / USAspending / web sweep | after approval + operator scope | keys / quota |
