# JTG evidence pack v2 · renderer contract (Codex-facing)

Pack path:
`data/state/candidate_review_v1/jtg_inc/jtg_inc.evidence_pack.v2.json`
Schema version: `2.0` (field `schema_version`). Upstream owner:
`agents/golden_press/evidence_pack_v2.py` (assembly) and
`agents/golden_press/evidence_route.py` (classification). The renderer
consumes these fields as-is and performs NO reclassification.

## The two dimensions (every record)

- `evidence_class`: `client_historical | competitive_historical |
  current_opportunity | forecast | event | excluded | ambiguous`
- `evidence_basis`: one sentence of traceable evidence for the class.
- `route_relationship`: `direct | incumbent | named_partner_teaming |
  possible_subcontracting | unknown`
- `route_basis`: one sentence for the route.

Laws the renderer must respect (all upstream-enforced, test-pinned in
`tests/test_evidence_route.py`):

1. The competitor surface renders ONLY `evidence_class ==
   "competitive_historical"`. `client_historical` rows are the client's
   own paper and never competition. `ambiguous` rows never enter
   competitive totals or competitor visuals; they may render only in an
   explicitly-labeled unresolved/receipts surface.
2. Route classification remains independent of evidence classification, but
   the external eight-slot projection is stricter than this internal pack
   surface. Slot 6 may render a graph route only when it is direct-fit,
   eligible, and neither `ambiguous` nor `excluded`. An unresolved partner
   identity remains an internal research lead until separate evidence promotes
   a qualified route. MAXIMUS, InDyne and CAE may still arrive as
   `named_partner_teaming` classifications, never as competitors, without
   thereby becoming external recommendations.
3. Live pursuits render ONLY `evidence_class == "current_opportunity"`
   (a live response window is upstream-guaranteed). `excluded` closed
   notices never render as live.
4. For each current opportunity consume the `qualified` block:
   `buyer{agency, sub_agency, office}`, `instrument`, `access_rule`,
   `response_due`, `published_value`, `eligibility{direct, access_rule,
   basis}`, `technical_fit{fit, basis}`, `next_route`. Where
   `eligibility.direct` is false, present the partner/ineligible-direct
   framing from `route_basis`; never promote to a direct pursuit.
5. `incumbent_name`/`incumbent_basis` render only when present
   (text-supported), never inferred.

## Requirement families and targets

- Every L1 record carries `requirement_family` (canonical dedupe:
  one record per family survives assembly).
- `target_groups` is keyed by `requirement_family`. Rows carry `role`
  (`contracting_officer_or_specialist`,
  `agency_small_business_or_industry_engagement`,
  `incumbent_or_likely_prime_capture_lead`), contact fields where
  public, exact `provenance` (`sam_notice:<id>:<field>`), and
  `source_record_id`. Rows flagged `enrichment_candidate: true` carry
  identity and role only; render them as research targets, never as
  contacts with reachable details.
- Never render JTG employees as external targets (upstream-guaranteed:
  target rows derive only from government notice contacts and named
  incumbents).

## Bookkeeping surfaces

- `counts`: authoritative per-class totals; render totals from here.
- `moved_records`: prior rendered seat vs corrected class with evidence;
  internal-facing QA surface, never client copy.
- `research_gaps`: structured gaps `{gap, blocking, next_step}`;
  internal-facing.
- `classification_context`: aliases, validated competitor bases, named
  partners, scope terms, as-of date. Render the as-of date; the rest is
  provenance for review.
- `deep_sweep_receipt`: retrieval receipts for the expansion sweep.
