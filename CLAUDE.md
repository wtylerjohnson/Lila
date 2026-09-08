# CLAUDE.md · LILA session contract

LILA reconstructs client-specific federal markets across its governed source
mesh. The `lila_release` package contains the operator-locked eight-slot
Federal Market Map (`docs/MARKET_MAP_CONTRACT.md`) and its assessment-bound
lead report, source ledger and evidence receipts. Other report families are
internal feedstock or compatibility surfaces. Read this file, then
[docs/CONVENTIONS.md](docs/CONVENTIONS.md) and
[docs/CONTRACT_SURFACES.md](docs/CONTRACT_SURFACES.md), before writing code.
Deep background: [README.md](README.md), [pipeline/LEARNINGS.md](pipeline/LEARNINGS.md),
[docs/capture_brief_playbook.md](docs/capture_brief_playbook.md).
Reality in the code wins over any doc that contradicts it; fix the doc in the
same session you notice the drift.

## Session protocol (operator-mandated, 2026-07-10)

1. BEFORE ANY CODE: read this file, docs/CONVENTIONS.md,
   docs/CONTRACT_SURFACES.md. If one is missing or stale, run the convention
   audit inline (discover storage, fact-registry, lint, template, and test
   patterns from the repo itself), write or update the doc as the first
   commit of the session, then proceed.
2. State in one short block which existing patterns you are extending.
   If the task needs a pattern that does not exist, create the minimal
   version consistent with what does exist, document it in CONVENTIONS.md
   in the same session, and continue.
3. Contract surfaces (see docs/CONTRACT_SURFACES.md) are never modified
   silently. If the task requires touching one: complete all other work
   fully, implement the contract-surface change as an isolated,
   clearly-labeled diff with its own tests, and present it separately for
   approval. The rest of the work ships regardless.
4. Done means: full test suite green, report lint clean, and any doc the
   session created verified by running the commands it documents. A session
   that ends without green tests delivers a DRAFT-state summary naming
   exactly what failed and why, never silent partial work.

## Hard operator rules (violations burned us; do not relearn)

- External product builds run through the Command Center ONLY: dashboard buttons or
  its API (`POST /api/run {client_name, step, args}`, poll `/api/job/<id>`).
  The only external release step is `lila_release`; Candidate Review, Signal
  Board, capture brief, and target reports are internal or compatibility
  surfaces. Never launch a client release ad hoc in bash. Diagnostic/offline
  scripts with no LLM spend are fine in bash.
- Never change gate settings (search scope, approvals, release switches)
  agent-side. They encode the ENGAGEMENT, which data cannot determine.
  Warn loudly and ask the operator. "The data wins" applies to claims,
  never to operator decisions.
- Never kill processes by name (pkill -f has matched the operator's runs).
  Exact PIDs only, announced first.
- Filter-first, free context (L18, supersedes L12's implementation): every
  metered or LLM-priced call honors the gate focus (SAM queries, triage,
  composition); whole-market context stays in the free layers (extract
  census, USAspending lane totals) so a focused slice never reads as a dead
  market. Re-focusing = a new sweep. Gate values stay operator-owned.
- Client-file doctrine: the client file carries only verified claims and
  forward motion; negatives convert to forward actions or are deleted
  (the internal file carries them). See CONTRACT_SURFACES.md for the
  banned strings.
- No em dashes in any output-reachable string (R9 + lint_emdash enforce).

## Commands

```
python3 run_ui.py                          # Control Room -> http://127.0.0.1:8321
.venv/bin/python -m pytest tests/ -q      # full suite; must be green to ship
python3 -m tools.capability scaffold --client "Name"   # required profile
```

Report press via Command Center API (the only sanctioned build path):

```
curl -X POST http://127.0.0.1:8321/api/run -H "Content-Type: application/json" \
  -d '{"client_name":"<Name>","step":"lila_release","args":{}}'
```

## LLM routing

All LLM calls ride the Claude Max plan via the CLI
(`agents/decisions/maxplan_cli.py`); `LILA_LLM_ROUTE` defaults to `max`.
Zero API tokens in use; the API route is the scale roadmap. Never flip the
route or add API keys agent-side.

## Current operating checkout (operator-authorized, 2026-09-08)

`/Users/wtjohnson/Lila` is the single operating checkout. GitHub `main` is the
integration base. Implement changes on a named development branch, preserve
other worktrees and uncommitted work, and verify before updating operating
main. The September 8 operator instruction authorizes this cutover and the
additive dossier, reviewed-case, target and priority-history workflow repair.
Older FSOS branches are separate historical lanes, not LILA run locations.
Never run `git worktree prune`.

See `docs/OPERATING_RUNBOOK.md` for the run path and verification commands.
