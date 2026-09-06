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

## Out of scope here

Assess-to-LeadRow mapper, Command Center Lead Gen orchestration, Market
Map / `lila_release` edits, Step 1 website-deep work, auto-outreach.
