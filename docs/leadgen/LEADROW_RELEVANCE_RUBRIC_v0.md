# LeadRow relevance rubric v0

Skeptic-grade, reproducible checks on a **LeadRow**. This is not
dossier hygiene and not Arista Step 1 intake extract. A solicitation
is not a lead.

Authority: `agents/leadgen/contracts.py` plus this rubric. The
executable owner is `agents/leadgen/eval/`. Desktop board families
A-F are mirrored here as code checks.

## Product grain

- Parent = `OpportunityAssessment`. Child = `LeadRow`.
- `LeadRow = BuyingMotion x ActionableExternalPathway x
  SellerTransactionPath x CurrentNextAction`
- Tiers `LEAD_T1 | LEAD_T2 | WATCH | HOLD | REJECT` are not targeting
  `T1 | T2 | T3`.
- `LEAD_T1` / `LEAD_T2` require families **A-F all PASS**.
- `WATCH` / `HOLD` / `REJECT` may fail A-F. They must carry a
  justification receipt. REJECT is never silently dropped.
- Zero solicitation-only T1/T2. The scorer never fills a quota.

## How a skeptic runs one pack

```
python -m agents.leadgen.eval.score --help
python -m agents.leadgen.eval.score \
  --pack agents/leadgen/eval/fixtures/tiny_pack.json \
  --md /tmp/leadrow.scorecard.md \
  --csv /tmp/leadrow.scorecard.csv
```

A frozen Arista (or any client) press receipt is the same command with
`--pack path/to/<slug>.leadgen.json`. Optional `--overlays` adds
email-status, auth-only, funding, and reject-receipt facts that the
v1 LeadRow object does not yet store.

## Family A · Buying motion

| ID | Pass when | Fail when |
|---|---|---|
| A1 | `buying_motion.buyer_agency` is a named buyer | blank, "unknown", or missing |
| A2 | `kind` is a closed `CommercialMotionKind` | missing or not a motion |
| A3 | a `clock` or `window` is present | neither clock nor window |
| A4 | `parent_subject_id` matches the parent `subject_id` | unbound or mismatched |

## Family B · Product fit

| ID | Pass when | Fail when |
|---|---|---|
| B1 | parent `requirement_span` or overlay `product_fit_cites` exists | no capability-fit cite |
| B2 | fit cite is not the parent title alone | title-only or NAICS-only claim |
| B3 | parent `identity_status` is absent or `bound` | `blocked` or `abstain` |

## Family C · Pathway + communication permission

| ID | Pass when | Fail when |
|---|---|---|
| C1 | pathway has HTTPS `source_url` and at least one evidence ref | missing URL or evidence |
| C2 | `communication_permission` is a closed state; `outreach_authorized` only with overlay doors | invented send / unauthorized outreach |
| C3 | no contact email is claimed, or the claimed email is `VERIFIED` | **`UNVERIFIED` email cannot PASS C3** |

C3 does not invent a person. An absent email is `NA`, not a pass that
hides an unverified address.

## Family D · Transaction path (authorization is not intent)

| ID | Pass when | Fail when |
|---|---|---|
| D1 | `seller_path.kind` is a known route, or HOLD/WATCH names the missing route | silent `path_unknown` |
| D2 | path cites a holder, vehicle, dossier, or prime-posture | route with no cite |
| D3 | dated buying intent (live bid / renewal / displacement clock or window) | **auth-only cannot PASS D3** |

D3 is the skeptic split: a vehicle seat, schedule, or reseller
authorization is not buyer intent. Overlay `auth_only: true` or a
vehicle/adjacency path with no demand clock fails D3.

## Family E · Next action + clock

| ID | Pass when | Fail when |
|---|---|---|
| E1 | `next_action.verb` is a closed `NextActionVerb` | missing verb |
| E2 | `due` is set when the motion has a clock, or `blocked_by` names the missing clock | clocked motion, undated action, no hold |
| E3 | `owner` is `operator` | invented-person owner |

## Family F · Audit / provenance / trace

| ID | Pass when | Fail when |
|---|---|---|
| F1 | `decision_trace_id` resolves to a `DecisionTrace` in the pack | missing or dangling trace |
| F2 | trace has `evidence_ids` or an honest gap note | empty trace with no note |
| F3 | `parent_assessment_id` resolves to a parent in the pack | orphan child |

## Tier and pack checks

| ID | Rule |
|---|---|
| TIER.T1T2_REQUIRES_AF | `LEAD_T1` / `LEAD_T2` FAIL unless families A-F all PASS |
| TIER.SOLICITATION_ONLY | a T1/T2 that is only a SAM notice (no holder/vehicle/contacts/fit cite) FAILS |
| TIER.RECEIPT_JUSTIFIED | WATCH / HOLD / REJECT must carry `blocked_by`, overlay `receipt`, or trace notes |
| PACK.SOLICITATION_ONLY_T1T2 | pack FAIL when any T1/T2 is solicitation-only (target: zero) |
| PACK.NO_QUOTA_FILL | pack FAIL when `quota` / `fill_to` / `min_t1` / `min_t2` is present; scorer invents zero rows |
| PACK.REJECT_NOT_DROPPED | every input REJECT lead_id appears on the scorecard. Null or blank `DecisionTrace.lead_id` is not a declared lead (parent traces with no child); those traces do not fail this check. Missing real string ids still FAIL. |

## Overlay fields (eval.v0, not LeadRow v1)

The frozen LeadRow forbids extra keys. Skeptic facts that the contract
does not yet store ride `overlays[lead_id]`:

- `email_status`: `VERIFIED` / `UNVERIFIED` / `ABSENT`
- `email`: optional address (status still governs C3)
- `auth_only`: boolean; true fails D3
- `solicitation_only`: boolean; true fails TIER.SOLICITATION_ONLY
- `receipt`: justification text for WATCH / HOLD / REJECT
- `product_fit_cites`: capability-fit evidence ids
- `outreach_doors_open`: boolean; required for C2 when permission is
  `outreach_authorized`

## Out of scope

Step 1 `CompanyDossier` extract, Market Map / `lila_release`, targeting
persona T1/T2, auto-promotion, and any rewrite of `draft_lead_rows`.
