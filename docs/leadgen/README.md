# Lead-gen contracts (WS0)

Additive schema package. Not a press step, not a UI, not a Market Map
change.

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
- Drafts are `WATCH` or `HOLD` only. Sales-ready gates do not exist
  yet, so the shim never emits `LEAD_T1` or `LEAD_T2`.
- Targeting `rule_id` `T1` stays on `buying_motion.targeting_rule_id`.
  It is never stored as `lead_tier`.
- Assess evidence refs, notice ids, and requirement spans are copied
  as pointers. The mapper does not invent a pathway kind, a prime
  route, or a communication permission.
- Missing pathway, seller route, clock, or permission holds the row
  (or the parent, when a pathway cannot be formed without invention)
  with `next_action.blocked_by` set to the promotion condition.

## Out of scope here

Command Center Lead Gen orchestration, Market Map / `lila_release`
edits, Step 1 website-deep work, auto-outreach, communication-
permission engine.
