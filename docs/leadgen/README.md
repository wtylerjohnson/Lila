# Lead-gen contracts (WS0)

Additive schema package plus a thin real Press Lead Gen path.
Not a UI. Not a Market Map change. Not full automated universe
discovery.

## Ruling (locked)

- Lead gen is primary; solicitation / opportunity identification stays.
- **Opportunity assessment is PARENT. LeadRow is CHILD.**
- `LeadRow = BuyingMotion x ActionableExternalPathway x
  SellerTransactionPath x CurrentNextAction`
- Federal Market Map stays the external deliverable / projection.
  `lila_release` is untouched.
- Targeting `rule_id` T1/T2 is **not** a lead tier.
- Step 1 `CompanyDossier` remains the ontology front door (PR #1). This
  package cites it; it does not redesign intake.

## Authority for today's types

[docs/spikes/ws0_today_to_leadrow_map.md](../spikes/ws0_today_to_leadrow_map.md)
maps existing Assess, motion, targeting, and Market Map types onto these
contracts. Implement adapters from that map. Do not guess parent vs
child grain.

## Package

`agents/leadgen/` is a sibling of `agents/assess/`. Assess models are
not deleted, moved, or given lead fields.

| Type | Role |
|---|---|
| `OpportunityAssessment` | Parent pointer at one Assess subject (`AssessRun` identity + live / thesis / partner id). Valid with zero children. |
| `LeadRow` | Child. Four factors plus `lead_tier` / readiness. |
| `DecisionTrace` | Minimal why-record a child can cite. |
| `LeadTier` | `LEAD_T1\|LEAD_T2\|WATCH\|HOLD\|REJECT` (field `lead_tier`) |
| `TargetingRuleId` | `T1\|T2\|T3` (field `targeting_rule_id`) |

JSON Schema export:

```
python -m agents.leadgen.schema_export
```

Writes `agents/leadgen/schemas/*.schema.json`.

## Assess / target_actions draft mapper

Pure function. No persistence, no Command Center step, no outreach.

In-process:

```
from agents.leadgen import draft_lead_rows

batch = draft_lead_rows(assess_run, target_actions)
```

`assess_run` is an `AssessRun` or an AssessRun-shaped dict (a golden
pack may wrap the run under `assess_run`). `target_actions` is the
already-built projection dict from `build_target_actions`, a list of
its rows, or omitted.

Diagnostic CLI (reads JSON, prints the draft batch, writes nothing):

```
python -m agents.leadgen \
  --assess path/to/assess_run.json \
  --target-actions path/to/target_actions.json
```

Rules this mapper encodes:

- Every child cites `parent_assessment_id` and `assess_run_id` on an
  existing Assess subject. Solicitation stays the parent; it is not a
  lead.
- Drafts from `draft_lead_rows` are `WATCH` or `HOLD` only. The press
  qualifier may promote a small evidenced subset.
- Targeting `rule_id` `T1` stays on `buying_motion.targeting_rule_id`.
  It is never stored as `lead_tier`.
- Assess evidence refs, notice ids, and requirement spans are copied
  as pointers. The mapper does not invent a pathway kind, a prime
  route, or a communication permission.
- Missing pathway, seller route, clock, or permission holds the row
  (or the parent, when a pathway cannot be formed without invention)
  with `next_action.blocked_by` set to the promotion condition.

## Press Lead Gen thin real path vs full Press Lead Gen

Full Press Lead Gen is the governed pipeline: load company / product
ontology, discover, assess, mint atomic LeadRows, then keep active
`LEAD_T1` / `LEAD_T2` lists plus WATCH / HOLD / REJECT receipts.

`agents.leadgen.press` is the **thin real path**. It documents Build
Plan steps 1-9 and executes them without rewriting discovery or Assess.
`stub` is False when the qualifier ran. HOLD remains the fail-closed
default when four-leg receipts are missing.

| Step | Real-path behavior |
|---|---|
| 1 | Load client profile / optional intake dossier path. Cite only. Do not redesign Step 1. |
| 2-5 | Hand off to the existing search, qualify, Assess, and `target_actions` path. Require an `AssessRun`. Do not reimplement discovery. |
| 6-7 | Call `draft_lead_rows`, then `qualify_drafts`. Parents stay `OpportunityAssessment`. Children stay HOLD unless all four legs plus a promotion receipt are present. |
| 8-9 | Write branded HTML (primary when `review_dir` is set) plus a JSON sidecar listing rows by `LeadTier`. WATCH / LEAD_T2 may appear; at most one LEAD_T1. REJECT only with a real receipt. No quota fill. |

In-process:

```
from agents.leadgen import run_press

receipt = run_press(
    assess=assess_run,
    target_actions=target_actions,
    review_dir="data/review",
    write_markdown=True,
)
```

CLI:

```
python -m agents.leadgen.press \
  --assess path/to/assess_run.json \
  --target-actions path/to/target_actions.json \
  --dossier path/to/dossier.json \
  --review-dir data/review \
  --markdown
```

Produce the AssessRun file from an already completed assessment
(read-only; does not run AssessmentChain and does not invent leads):

```
python -m agents.leadgen.export_assess --client "Arista Networks"
python -m agents.leadgen.export_assess --client "Arista Networks" \
  --output /tmp/arista.assess_run.json
```

If no current pointer exists, the exporter fails closed and names
the operator cutover (`python -m tools.assess_refresh --client
"Arista Networks" --activate`). Market Map packs, worksheets,
notice lists, `source_records` dumps, and Testco-shaped demo
fixtures (`arista_demo.assess_run.json`) are refused.

Artifact: `data/review/<slug>.leadgen.json` (optional
`<slug>.leadgen.md`). Same human-gate family as
`<slug>.qualify.json` / `<slug>.horizon.json`. Not written into
`data/state/assess_runs/`.

Failure rules the real path encodes:

- Missing assess input fails closed with an explicit error.
- Notice-only input (a notice list or a sweep without an AssessRun)
  is refused. Press will not invent leads from notices alone.
- Promotion never quota-fills. Solicitation-only rows cannot green
  `LEAD_T1` or `LEAD_T2`.
- HOLD remains the default when a pathway, seller route, clock, or
  approaching-decision receipt is missing.

Federal Market Map remains the separate external deliverable.
`lila_release` is untouched. Press Lead Gen is not a release door.

## Skeptic eval (objective scoring)

Dossier hygiene does not prove opportunity or lead relevance. The
eval harness scores **LeadRows and their parents** against checked-in
rubrics:

- [LEADROW_RELEVANCE_RUBRIC_v0.md](LEADROW_RELEVANCE_RUBRIC_v0.md)
- [OPP_PARENT_QUALITY_RUBRIC_v0.md](OPP_PARENT_QUALITY_RUBRIC_v0.md)

```
python -m agents.leadgen.eval.score --help
python -m agents.leadgen.eval.score \
  --pack agents/leadgen/eval/fixtures/tiny_pack.json \
  --md /tmp/leadrow.scorecard.md \
  --csv /tmp/leadrow.scorecard.csv
```

A frozen Arista (or any client) press receipt is the same `--pack`
path. Optional `--overlays` adds C3 email-status and D3 auth-only
facts. The scorer never invents rows, never auto-promotes, and never
drops REJECT.

## AssessRun for press (Arista, operator Mac)

Press Lead Gen needs a real `AssessRun`, not a Market Map pack and
not the Testco-shaped Desktop demo `arista_demo.assess_run.json`.
`python3 run_assessment.py` / AssessmentChain do not write that file.

1. Confirm a current pointer (read-only):

```
python -m agents.leadgen.export_assess --client "Arista Networks"
```

2. If that fails closed with `no current AssessRun`, materialize from
   the existing completed sweep / qualify / horizon artifacts (this
   writes the current pointer; it is not a new SAM extract):

```
python -m tools.assess_refresh --client "Arista Networks" --activate
python -m agents.leadgen.export_assess --client "Arista Networks" \
  --output ~/Desktop/Arista/arista.assess_run.json
```

   If there is no sweep yet, run search through the Command Center
   (not ad hoc bash, and not Step 1 intake extract). Then activate.

3. Press the real path (HTML is primary when `--review-dir` is set;
   JSON and optional Markdown are sidecars):

```
python -m agents.leadgen.press \
  --assess ~/Desktop/Arista/arista.assess_run.json \
  --review-dir data/review \
  --markdown
```

   Open the dated
   `data/review/arista_networks_Press_Lead_Gen_CLIENT_DELIVERABLE_*.html`
   file. Confirm `stub` is `false` on the JSON sidecar. A small number
   of WATCH / LEAD_T2 rows may appear when a parent already has all
   four LeadRow legs plus a renewal-decision or prime-recompete
   receipt. Thin parents stay HOLD.

4. Skeptic-score the same pack (no TypeError; promoted rows can PASS
   families A-F when overlays/receipts supply the required facts):

```
python -m agents.leadgen.eval.score \
  --pack data/review/arista_networks.leadgen.json \
  --md /tmp/arista.scorecard.md \
  --csv /tmp/arista.scorecard.csv
```

   Hermetic fixture for the same scorer:

```
python -m agents.leadgen.eval.score \
  --pack agents/leadgen/eval/fixtures/promoted_pack.json \
  --md /tmp/promoted.scorecard.md \
  --csv /tmp/promoted.scorecard.csv
```

`--from-file` on the exporter will refuse notice lists, Market Map /
worksheet packs, `source_records` dumps, and a Testco demo labeled as
Arista. It will not invent SAM notices.

## Out of scope here

Full source-universe discovery rewrite, communication-permission /
outreach, Command Center Market Map UI, Step 1 website-deep work
(PR #1), auto-promotion quotas, rewriting skeptic rubrics.
