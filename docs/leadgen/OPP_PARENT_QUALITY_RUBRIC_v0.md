# Opportunity-parent quality rubric v0

Skeptic-grade checks on an **OpportunityAssessment** parent. A parent
can be valid with zero LeadRow children. Passing this rubric does not
mint a lead. Failing it does not delete the Assess subject.

Authority: `agents/leadgen/contracts.py` `OpportunityAssessment`.
Executable owner: `agents/leadgen/eval/`. Desktop board parent
dimensions are mirrored here as code checks.

## Product grain

- Solicitation / thesis / partner link = parent subject, not a lead.
- Children are scored by
  [LEADROW_RELEVANCE_RUBRIC_v0.md](LEADROW_RELEVANCE_RUBRIC_v0.md).
- Parent checks never alias targeting `T1`/`T2` or Assess
  `LiveRecommendation` onto `LeadTier`.
- No quota fill. A thin market is a thin market.

## How a skeptic runs one pack

Same CLI as the child rubric. Parent rows emit as `scope=parent` in
the CSV and as a Parent section in the Markdown scorecard.

```
python -m agents.leadgen.eval.score \
  --pack path/to/<slug>.leadgen.json \
  --md /tmp/parent.scorecard.md \
  --csv /tmp/parent.scorecard.csv
```

Optional `--overlays` may include parent keys (assessment ids) for
funding, contestability, demand, need, and blocker cites.

## Parent checks

| ID | Dimension | Pass when | Fail when |
|---|---|---|---|
| P.DEMAND | demand reality | live classification, lifecycle, or overlay `demand_cites` | subject with no demand signal |
| P.BUYER | buyer identity | `agency` is a named buyer | blank or "unknown" |
| P.NEED | need | `requirement_span` or overlay `need_cites` | no stated need |
| P.FUNDING | funding | overlay `funding_cites` or lifecycle `funded_intent` | notice-only or title-only funding claim |
| P.TIMING | timing | `lifecycle` is set | no lifecycle / window on the parent |
| P.FIT | fit | `requirement_span` or overlay `product_fit_cites` | no capability-fit cite |
| P.MECHANISM | mechanism | `notice_id`, `solicitation_number`, or partner/thesis subject | no acquisition mechanism |
| P.CONTESTABILITY | contestability | overlay `contestability_cites` | assumed contestable because a notice exists |
| P.BLOCKERS | blockers | named overlay `blocker_cites` when children are HOLD/REJECT; else none required | HOLD/REJECT children with no named blocker |
| P.EVIDENCE | evidence family | live `notice_id`, child pathway evidence, or overlay cites | parent with no evidence family |

## Overlay fields (eval.v0, not OpportunityAssessment v1)

Keyed by `assessment_id` (or mixed into the same overlays object):

- `demand_cites`
- `need_cites`
- `funding_cites`
- `product_fit_cites`
- `contestability_cites`
- `blocker_cites`

A live SAM notice is evidence that a **solicitation** exists. It is
not, by itself, proof of funding or contestability. That is why
P.FUNDING and P.CONTESTABILITY fail closed without a cite.

## Relationship to children

- A strong parent with zero children is an honest census row.
- A T1/T2 child still needs A-F on the LeadRow rubric. Parent PASS
  cannot promote a solicitation-only row.
- REJECT children must appear on the scorecard with their receipt.
  The parent P.BLOCKERS check looks for a named blocker; it does not
  drop the child.

## Out of scope

Step 1 intake extract, Assess ledger mutation, Market Map release,
and any change to `LiveSolicitation` / `AssessRun`.
