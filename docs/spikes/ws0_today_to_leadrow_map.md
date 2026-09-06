# WS0 spike: LILA today to LeadRow

**Status:** SPIKE ONLY. No production schema. No Pydantic LeadRow package.
No Market Map release change. No merge of experimental trees.

**Date:** 2026-09-06
**Base:** `main` at `e589fdf` (print pagination / receipts)
**Step 1 authority (unmerged):** PR #1
[`cursor/step1-intake-mastery-da3f`](https://github.com/wtylerjohnson/Lila/pull/1)
@ `681b02bd01f7531804f70be69c9bfef61fd27bbe`

## Patterns this spike extends

Assess frozen contracts (`agents/assess/contracts.py`) remain the
intelligence parent. Golden-press motions and target-action rows remain
the commercial-action layer. Step 1 `CompanyDossier` (PR #1) remains the
seller-ontology front door. Federal Market Map remains an eight-slot
projection and the only external release. This document adds a
`docs/spikes/` map so WS0 schemas can be implemented later without
guessing, and without deleting Assess.

## Locked product ruling (obey)

- Primary focus: public-sector lead gen (Lead Gen Standard v1.2 / Build
  Plan v1.0). Those two named documents are **not in this checkout**
  (repo search, GitHub code search, and issue search returned none).
  Field names below come from this ruling plus types that already exist
  in code.
- Keep solicitation / opportunity identification. Do not weaken or
  delete Assess.
- **Opportunity assessment is PARENT. LeadRow is CHILD.**
- `LeadRow = BuyingMotion × ActionableExternalPathway ×
  SellerTransactionPath × CurrentNextAction`
- Federal Market Map remains a deliverable / projection. Do not rip out
  `lila_release`. Do not let Market Map block LeadRow contracts.
- Targeting rule IDs currently named T1 / T2 are **not** lead tiers.
- Step 1 intake (company mastery) is the ontology front door. Reference
  PR #1. Do not redesign Step 1 here.

WS0 types this map must feed, without inventing runtime code:

| WS0 type | Role | Grain |
|---|---|---|
| `OpportunityAssessment` | Parent judgment that an opportunity exists and is worth classifying | one assessed subject (live notice family, thesis, or linked partner route) |
| `BuyingMotion` | What buying event is in motion | one motion on one buyer / clock |
| `ActionableExternalPathway` | Public, already-published route a seller can actually use | one pathway, evidence-bound |
| `SellerTransactionPath` | How this seller can transact (prime, sub, channel, vehicle) | one path, not a contact |
| `CurrentNextAction` | The single next operator action | one action, dated when a clock exists |
| `LeadRow` | Child product of the four factors above | one actionable lead under one parent assessment |
| `DecisionTrace` | Why the parent and child were produced | append-only, hash-bound |
| `Outcome` | What happened after the lead | later fact; absent today |

Recommended lead-tier literals (WS0, **not** targeting):
`T1 | T2 | WATCH | HOLD | REJECT`.

Until those literals live in their own enum module, **do not** store them
in `targeting_personas.json` `rule_id`, persona `tier`, or
`LiveRecommendation`.

---

## 1. Matrix: existing types to WS0 fields

Legend for **Fit**:
`DIRECT` = copy or wrap the field;
`PARTIAL` = same idea, wrong grain or missing required siblings;
`ABSENT` = WS0 needs it and nothing today owns it;
`PROJECT` = report / Market Map surface only, not a schema owner;
`DO NOT` = reuse would weaken opp ID or collide names.

### 1.1 Assess production contracts (parent spine)

Source: `agents/assess/contracts.py`. Frozen. Nested collections become
tuples after validation. This is the parent intelligence boundary
behind Review. Keep it.

| Existing type / field | Today | WS0 target | Fit | Notes for the implementer |
|---|---|---|---|---|
| `AssessRun` | Immutable envelope: scope, profile version, as-of, three ledgers, coverage, approval | `OpportunityAssessment` run envelope **and** `DecisionTrace.run_id` | PARTIAL | One run holds many parent assessments. Do not 1:1 map `AssessRun` to one `LeadRow`. |
| `AssessRun.run_id` | Content-addressed run identity | `OpportunityAssessment.assess_run_id`, `DecisionTrace.assess_run_id` | DIRECT | Child LeadRows must cite this. Market Map already stamps `assess_run_id` on QA sidecars. |
| `AssessRun.client_name` | Exact engagement client | `OpportunityAssessment.client_name` | DIRECT | Identity still comes from Step 1 / review packet, not from a notice title. |
| `AssessRun.profile_version` | Approved capability profile generation | `OpportunityAssessment.profile_version` | DIRECT | After PR #1 merges, also cite `CompanyDossier` identity, not instead of this. |
| `AssessRun.scope` (`AssessScope`) | Operator-owned collection boundary | `OpportunityAssessment.scope` | DIRECT | Gate value. Never infer `scope.preset` from a lead. |
| `AssessRun.as_of` | Run clock | `OpportunityAssessment.as_of`, `DecisionTrace.as_of` | DIRECT | Keep UTC vs host-local doctrine; do not add a third clock. |
| `AssessRun.coverage` | Required-source census | `DecisionTrace.coverage` | PARTIAL | Coverage is why a parent may be HOLD, not a lead field. |
| `AssessRun.live` / `horizon` / `partners` | Three ledgers | Parent subjects for `OpportunityAssessment` | DIRECT | Live, thesis, and partner are **subjects**, not LeadRows. |
| `AssessRun.approval_status` (`pending` / `approved` / `rejected`) | Human Assess gate | `DecisionTrace.assess_gate` | PARTIAL | `rejected` here is a **gate**, not lead-tier `REJECT`. |
| `AssessRun.can_release()` | Release eligibility | Market Map / Produce only | PROJECT | Must not become a LeadRow validator. A lead can exist on a DRAFT run. |
| `LiveSolicitation` | NOTICE-tier SAM census row | `OpportunityAssessment` subject `kind=live_solicitation` | DIRECT | This is opp ID. Preserve BID_NOW hardness. |
| `LiveSolicitation.record_id` | Stable family / record id | `OpportunityAssessment.subject_id` | DIRECT | Parent key. LeadRow cites it; does not replace it. |
| `LiveSolicitation.notice_id` | SAM notice id | pathway `notice_id` | DIRECT | Required when pathway is a live SAM posting. |
| `LiveSolicitation.solicitation_number` | Solicitation family | parent `solicitation_number` | DIRECT | Family dedupe stays on the parent. |
| `LiveSolicitation.title` / `agency` / `component` / `office` | Buyer + label | `BuyingMotion.buyer_*` | PARTIAL | Buyer fields are necessary but not a motion by themselves. |
| `LiveSolicitation.classification` (`bid_now`, `market_research`, `presolicitation`, `special_notice`, `amendment`, `awarded_or_closed`, `excluded`, `unscreened`) | Notice class | parent `lifecycle` / eligibility | PARTIAL | Do not collapse these into lead tiers. `bid_now` is not automatically Lead T1. |
| `LiveSolicitation.response_deadline` | Actionable clock | `BuyingMotion.clock` and `CurrentNextAction.due` | PARTIAL | Clock is shared; next-action text is not present. |
| `LiveSolicitation.authoritative_evidence` | NOTICE-tier `EvidenceRef` tuple | `DecisionTrace.evidence` + pathway proof | DIRECT | Keep SAM-host and notice-id URL rules. |
| `LiveSolicitation.requirement_excerpt` + review triple | Human-reviewed span | parent `requirement_span` | DIRECT | BID_NOW still requires this. LeadRow must not bypass it. |
| `LiveSolicitation.fit_trace` | Capability fit citations | `DecisionTrace.fit_trace` | PARTIAL | Strings today; WS0 should keep them as evidence ids, not rewrite. |
| `LiveSolicitation.recommendation` (`pursue` / `monitor` / `no_bid` / `research` / `none`) | Assess recommendation | **not** lead tier | DO NOT | Closest cousin to WATCH / HOLD / REJECT, but different owner. Map in an adapter; do not alias the enum. |
| `LiveSolicitation.exclusion_reason` / `attachment_gap` | Why not BID_NOW | `DecisionTrace.holds` | PARTIAL | These justify HOLD / REJECT on the child, they do not delete the parent. |
| `OpportunityThesis` | Falsifiable developing prediction | `OpportunityAssessment` subject `kind=developing_thesis` | DIRECT | Keep falsifier, watch trigger, projected window. |
| `OpportunityThesis.thesis_id` | Thesis identity | parent `subject_id` | DIRECT | |
| `OpportunityThesis.predicted_event` / `lifecycle_stage` / `projected_window` | Predicted buying event | `BuyingMotion` (early) | PARTIAL | A thesis is a motion hypothesis. It is not yet an actionable pathway. |
| `OpportunityThesis.likely_acquisition_path` | Free text | `SellerTransactionPath` hint | PARTIAL | Unstructured. Do not promote to a closed path enum without evidence. |
| `OpportunityThesis.incumbent` | Named holder | `SellerTransactionPath.holder` hint | PARTIAL | Name only; no route role. |
| `OpportunityThesis.evidence` / `counterevidence` / `inference_chain` / `falsifier` / `watch_trigger` | Thesis discipline | `DecisionTrace` + `CurrentNextAction` watch | PARTIAL | `watch_trigger` is the honest next action for WATCH rows. |
| `OpportunityThesis.evidence_strength` / `status` | early/moderate/strong; proposed/approved/retired/invalidated | parent strength / status | PARTIAL | `INVALIDATED` is not lead-tier `REJECT`. |
| `PartnerOpportunity` | Evidence-backed access path linked to Assess ids | `SellerTransactionPath` **and** sometimes `ActionableExternalPathway` | PARTIAL | This is the best existing seller-path object. It is not a LeadRow. |
| `PartnerOpportunity.partner_id` | Partner record id | `SellerTransactionPath.path_id` | DIRECT | |
| `PartnerOpportunity.linked_assess_ids` | Must point at live or thesis ids in the same run | `LeadRow.parent_assessment_id` constraint | DIRECT | Child may not float free of a parent. |
| `PartnerOpportunity.direction` (`prime_to_sub` / `sub_to_prime` / `joint_venture` / `channel_reseller` / `vehicle_access`) | Closed path vocabulary | `SellerTransactionPath.kind` | DIRECT | Reuse these literals. Do not invent a second path enum. |
| `PartnerOpportunity.role_hypothesis` / `client_needs_partner` / `partner_needs_client` | Why the path exists | `DecisionTrace.seller_path_why` | PARTIAL | |
| `PartnerOpportunity.vehicle_or_channel` | Optional vehicle | `SellerTransactionPath.vehicle` | PARTIAL | Fail-closed today when source is missing. Keep that. |
| `PartnerOpportunity.next_validation_step` | Required next check | `CurrentNextAction` for partner-only rows | PARTIAL | Validation, not outreach. |
| `EvidenceRef` | Immutable source-backed observation | Shared evidence atom for parent, child, and trace | DIRECT | WS0 should **import or wrap** this, not fork it. |
| `EvidenceRef.evidence_id` / `tier` / `kind` / `source_url` / `retrieved_at` / `excerpt` / `supports` | Provenance | `DecisionTrace` rows | DIRECT | `WEB_LEAD` kind is **not** a LeadRow. |
| `SourceCoverage` (Assess) | Per-source lane completeness | `DecisionTrace.coverage` | DIRECT | Distinct from Signal Board coverage (see collisions). |
| `SourceCoverage.status` (`complete` / `partial` / `failed` / `not_registered`) | Attempt honesty | HOLD reasons | PARTIAL | Missing attempt is not zero findings. |
| `ProjectedWindow` | Dated or labeled window | `BuyingMotion.window` | DIRECT | |
| `NoticeAttachment` + inventory hash | Attachment census | parent completeness, not a lead | PARTIAL | BID_NOW still needs the human inventory click. |

### 1.2 Legacy assess lineage (ingest, not the parent)

Source: `agents/schemas.py`, `agents/assess/scoring.py`,
`agents/assess/assess_agent.py`, `tools/browser/enrichment_gate.py`,
`docs/assess_agent_plan.md`.

This stack is still imported (qualify, fit, forecast adapters, target
agent). It is **not** the production Review parent. Do not delete it.
Do not let WS0 treat `AssessmentResult` as `OpportunityAssessment`.

| Existing type / field | Today | WS0 target | Fit | Notes |
|---|---|---|---|---|
| `RawOpportunity` | Untouched API record (`sam.gov` / `usaspending.gov`) | Ingest only | PARTIAL | Feeds parent construction. Never a LeadRow. Forecast adapters that emit this type must stay out of live parents. |
| `RawOpportunity.source` / `source_id` / `api_url` | Provenance | `ActionableExternalPathway` seed | PARTIAL | URL + id are necessary; actionability is not proven here. |
| `RawOpportunity.contacts` (`OpportunityContact`) | SAM POC payload | pathway `published_contacts` | PARTIAL | Published only. No Apollo / invented people. |
| `RawOpportunity.raw_payload` | Full API dict | `DecisionTrace.raw_ref` | DIRECT | Keep for replay. |
| `SourceDocument` | Browser enrichment | parent document layer | PARTIAL | Same grain as Assess requirement text, weaker host rules. |
| `FusedOpportunity` | API + document after hygiene | Intermediate toward parent | PARTIAL | `requires_human_review` is a hold, not a tier. |
| `Discrepancy` | API vs document | `DecisionTrace.discrepancies` | DIRECT | `BLOCKER` severity is a HOLD / REJECT reason. |
| `AssessmentResult` | Weighted `MatchSignal[]` vs `CapabilityProfile` | **not** `OpportunityAssessment` | DO NOT | Score-only cousin. No NOTICE-tier proof, no BID_NOW, no coverage ledger. Adapter may copy `match_score` into trace as a diagnostic. |
| `MatchSignal` | One scored dimension | `DecisionTrace.signals` | PARTIAL | Evidence is a string, not `EvidenceRef`. |
| `CapabilityProfile` (`agents/schemas.py`) | Early client object | Seller ontology (legacy) | PARTIAL | Superseded in production by `tools.capability.ClientProfile`. After PR #1, dossier is the front door. |
| `ForecastRecord` | Agency-stated intent | thesis / WATCH subject | DIRECT | Structurally barred from becoming `RawOpportunity`. Keep that bar. |

### 1.3 Report / qualify / grade (compatibility parents)

| Existing type / field | Today | WS0 target | Fit | Notes |
|---|---|---|---|---|
| `AssessmentDocument` | Deterministic join for three-view reports | PROJECT of parents | PROJECT | Do not make this the LeadRow store. |
| `GradedPursuit` | Ranked live card (`source_id`, grade, fit_trace, amendments) | parent view of a live subject | PARTIAL | `source_id` joins to SAM / strict ledger. |
| `PursuitGrade` | A-F rubric (fit, timing, vehicle, incumbency, dollar) | `DecisionTrace.grade` | PARTIAL | Letter grades are not lead tiers. |
| `PartneringPlay` | Teaming play on a pursuit or monitor card | `SellerTransactionPath` view | PARTIAL | Weaker than `PartnerOpportunity` (blockers + candidates, not Assess ids). |
| `WatchlistEntry` | SAM / standing watch row | WATCH child candidate | PARTIAL | No motion × path × action product. |
| `CandidateRecord` / `Step2Report` (`agents/qualify.py`) | Verified SAM list + fit rationale | parent construction input | PARTIAL | Still the qualify artifact. |
| `FitRationale` / `FitVerdict` | LLM fit (`strong_fit` … `no_fit`) | `DecisionTrace.fit` | PARTIAL | `recommended_action` here is free text. Not `CurrentNextAction`. |
| `ClientProfile` (`tools/capability.py`) | Required sweep profile: core/adjacent/excluded terms, NAICS boundary, named rivals | Seller ontology (production) | PARTIAL | Capability terms, not offerings. PR #1 dossier is finer. |

### 1.4 Motions, decision rules, target actions (closest child pieces)

These are the nearest existing factorization of a LeadRow, but they are
**account / outreach** objects, not opportunity-assessment children.
They often fire on awards and forecasts with **no** live solicitation.

| Existing type / field | Today | WS0 target | Fit | Notes |
|---|---|---|---|---|
| Decision rules R1-R4 (`agents/golden_press/decision_rules.py`) | Deterministic account decisions over a pack | `BuyingMotion` evidence + `DecisionTrace.rules` | PARTIAL | R1 head-to-head, R2 displacement, R3 vehicle expiry, R4 forecast adjacency. R4 is labeled qualify, never pipeline. |
| `SEWP_V_FACTS` / vehicle parse | Dated vehicle constant + CONT_AWD parse | `SellerTransactionPath.vehicle` | PARTIAL | Prefix-only classification. |
| Targeting `rule_id` **T1** (`targeting_personas.json`, `targeting_rules.py`) | Renewal spec from R1 when the nearest clock is **client** paper | `BuyingMotion` class `renewal` | PARTIAL | **Name collision.** This is not lead-tier T1. |
| Targeting `rule_id` **T2** | Displacement spec from R1 rival clock or any R2 alert | `BuyingMotion` class `displacement` | PARTIAL | **Name collision.** Not lead-tier T2. |
| Targeting `rule_id` **T3** | Adjacency spec from R4 via paper holder | `BuyingMotion` class `adjacency` | PARTIAL | Routes through holder, never the buying agency. |
| Persona `tier` 1-4 | Title ladder (program owner, IT procurement, technical evaluator, vehicle/channel) | **not** a lead tier | DO NOT | People ranking. |
| `build_motions()` dict | Commercial unit: `motion`, `objective`, `row_class`, `org_kind`, `route_role`, `mission_applicability`, `spec_ids` | `BuyingMotion` | PARTIAL | Best existing motion object. Missing live-solicitation `bid_now` as a first-class motion. Labels: defend renewal, validate displacement, shape future requirement, engage paper holder, partner or channel route. |
| `MOTION_OBJECTIVES` | Outreach objective template | `CurrentNextAction` intent | PARTIAL | Outreach-shaped. Opp-ID next actions (review attachments, confirm deadline) are missing. |
| `build_target_actions()` row | One (person, spec) binding | **not** `LeadRow` | DO NOT | Person-grain. LeadRow is opportunity-grain. May *attach* a published contact to a pathway later. |
| Target-action `motion` / `decision_rule` / `row_class` | Join to rules | `BuyingMotion` + trace | PARTIAL | |
| Target-action `organisation_role` (`buyer` / `paper holder`) + `route_role` | Route | `SellerTransactionPath` | PARTIAL | |
| Target-action `record_ids` / `record_urls` | Cited records | `ActionableExternalPathway` + evidence | PARTIAL | Join law: already-rendered pack ids only. |
| Target-action `why_this_account` / `why_now` / `recommended_action` | Deterministic templates | `DecisionTrace` + `CurrentNextAction` | PARTIAL | Best existing next-action text. Still requires a person row to exist. |
| `ContactPlan` / `ContactSearchSpec` / `KnownPoc` | Step-3 people plan | pathway contacts vs enrichment search | PARTIAL | `known_pocs` may feed a pathway. `searches` must not invent contacts. |
| `TitleStrategy` / `BuyerPersona` | Approved title set | enrichment vocabulary | DO NOT | Not a LeadRow field. Title-approval gate stays independent. |

### 1.5 Source coverage (two owners)

| Existing type / field | Today | WS0 target | Fit | Notes |
|---|---|---|---|---|
| `agents.assess.contracts.SourceCoverage` | Assess ledger census (live / horizon / partner) | `DecisionTrace.assess_coverage` | DIRECT | Mandatory SAM.gov live row. |
| `agents.reports.source_coverage` projection | Signal Board public band from `results._attempts` | `DecisionTrace.sweep_coverage` | PARTIAL | Different vocabulary (`RETURNED`, `PARTIAL RETURN`, `NOT RUN THIS REFRESH`, …). Do not merge enums. |
| Sweep `source_coverage_verdict` / `decision_coverage_verdict` | Artifact-level honesty | HOLD on children when required lanes failed | PARTIAL | A focused slice must not read as a dead market (L18). |

### 1.6 Step 1 intake dossier (ontology front door, PR #1)

Not on `main`. Do not redesign. WS0 schemas should **cite** these types
after merge, not copy them.

| Existing type / field | Today (PR #1) | WS0 target | Fit | Notes |
|---|---|---|---|---|
| `IdentityResolution` | `bound` / `abstain` / `blocked` + official domain | Seller identity precondition | DIRECT | No LeadRow if identity is not bound. |
| `CompanyDossier` | Evidence-backed offerings, boundaries, channels, NAICS, retrieval units | Seller ontology | DIRECT | Front door for fit. Not an opportunity. |
| `Claim` / `ClaimState` | `company_asserted` / `corroborated` / `inferred` / `disputed` / `unknown` | Ontology claim states | DIRECT | Do not reuse as lead tiers. |
| `EvidenceItem` (intake) | Dossier evidence (`E001` ids, source_kind form/website/…) | Ontology evidence | PARTIAL | **Not** Assess `EvidenceRef`. Different id namespace. |
| `DossierNaics` / `DossierKeyword` / `RetrievalUnit` | Search-lane representation | Fit inputs for parent | DIRECT | Wrong core NAICS pulls the wrong notice set. |
| `kept_out` / `kept_out_naics` | Restorable exclusions | Fit negatives | DIRECT | Aviation / records namesakes stay out. |
| `RelatedEntity` | Affiliate / federal-path legal person | Seller identity graph | PARTIAL | Does not replace bound public-company identity. |
| `CoverageCell` | Dossier topic coverage | Ontology completeness | PARTIAL | Not Assess coverage. |
| Readiness E1-E8 | Intake receipts; never invent `scope.preset` | Gate before opp ID | DIRECT | WS0 must not launch search or mint leads from a failed E1-E8. |
| `IntakeStrategy` (main, `agents/decisions/schemas.py`) | Approved keywords, NAICS, search specs | Search boundary after mastery | PARTIAL | Still the strategy packet. Dossier is sidecar, not a silent field on this model. |
| `IntakeSubmission` | Form payload | Ontology input | PARTIAL | Name-only path on PR #1 does not require the form. |

### 1.7 Federal Market Map (projection only)

Source: `docs/MARKET_MAP_CONTRACT.md`,
`agents/golden_press/external_product_projection.py`,
`agents/golden_press/product_bundle.py`.

| Existing slot / field | Today | WS0 target | Fit | Notes |
|---|---|---|---|---|
| Slot 1 `priority-pursuits` | Ranked references to records owned below | PROJECT of Lead T1 / T2 | PROJECT | May display children. Must not mint `lead_id`. |
| Slot 5 `federal-opportunities` | Qualified current opps + nested targets | PROJECT of live parents + pathways | PROJECT | Targets stay nested under the opportunity. Same parent/child idea. |
| Slot 6 `teaming-opportunities` | Partner routes | PROJECT of `SellerTransactionPath` | PROJECT | |
| Slot 7 `future-forecasts` | Forecasts, not postings | PROJECT of thesis / WATCH | PROJECT | |
| Slot 8 `industry-days-events` | Gatherings | Optional pathway kind | PARTIAL | An event can be a pathway, not a solicitation. |
| Graph `held_opportunities` | Did not clear evidence + fit + window + route | HOLD children or parent-only | PARTIAL | Already a hold concept. |
| `lila_release` / `product_bundle.py` | Sole external transaction | Unchanged | DO NOT | LeadRow contracts must not become a release blocker. |

### 1.8 Golden-press evidence objects (role law)

| Existing type / field | Today | WS0 target | Fit | Notes |
|---|---|---|---|---|
| `EvidenceReference` (`evidence_objects.py`) | Dataclass; `claim_roles` prevent an award from proving an open opportunity | Pathway / motion proof roles | PARTIAL | Parallel to Assess `EvidenceRef`. Different module. WS0 adapters must honor **both** role laws. |
| `CoverageState` (`complete` / `partial` / `research_next`) | Figure completeness | HOLD / WATCH reasons | PARTIAL | Third coverage vocabulary. |

---

## 2. Explicit gaps vs WS0 types

### 2.1 `BuyingMotion` (required factor)

**Exists in pieces. No single typed object.**

Present:

- Live classification + deadline on `LiveSolicitation`
- Thesis `predicted_event` + `lifecycle_stage` + `projected_window`
- Motion labels from `build_motions()` over R1/R2/R4 specs
- Decision-rule cards with a named buyer and a dated clock

Absent (must add, do not overload motions.py):

- One closed `BuyingMotion.kind` that includes **live bid** alongside
  renewal / displacement / adjacency
- A required `parent_subject_id` pointing at a live record, thesis, or
  explicit "award-only motion" (if award-only motions are allowed, they
  still need an `OpportunityAssessment` parent; they must not skip Assess)
- A rule that a motion without a buyer **and** a clock is incomplete
- Separation from targeting `rule_id` T1/T2/T3

### 2.2 `OpportunityAssessment` (PARENT)

**Closest existing object: one ledger record inside `AssessRun`, not
`AssessmentResult`.**

Present: live / thesis / partner records, evidence, coverage, approval,
scope, profile version.

Absent:

- A first-class parent id that is stable across a live family, a thesis
  that later becomes a notice, and partner links
- A typed union `subject: Live | Thesis | PartnerLink` for WS0 consumers
  that must not import the whole report document
- An explicit "this parent may have zero children" state (census row
  that is UNSCREENED / EXCLUDED still exists)
- A pointer to Step 1 `CompanyDossier` / identity bind
- A child collection `lead_ids` (additive). Parents must remain valid
  with an empty child list so opp ID does not depend on lead gen.

### 2.3 `LeadRow` (CHILD)

**Absent as a type.** Nothing today is the product of all four factors.

Closest false friends (do not promote):

| False friend | Why it is not LeadRow |
|---|---|
| `LiveSolicitation` | Parent subject. No seller path, no next action, no lead tier. |
| `AssessmentResult` | Score only. No NOTICE proof. |
| `GradedPursuit` | Report card. |
| `build_motions()` item | Account motion, often award-based, no parent Assess id. |
| `build_target_actions()` row | Person × spec. Outreach grain. |
| `WebLead` | Sweep web result. Explicitly not a live solicitation. |
| Market Map slot-1 row | Projection. |

Required LeadRow fields the next schema pass should not have to invent:

1. `lead_id`
2. `parent_assessment_id` (required; fail closed if missing)
3. `assess_run_id` (must match parent)
4. `buying_motion` (required object)
5. `external_pathway` (required object)
6. `seller_path` (required object)
7. `next_action` (required object)
8. `lead_tier` (`T1 | T2 | WATCH | HOLD | REJECT`) stored as
   `LeadTier`, never as `rule_id`
9. `decision_trace_id`
10. `outcome_id` (optional, null until Outcome exists)

A constructor that receives only a SAM notice, or only a motion, or only
a contact, must fail.

### 2.4 `ActionableExternalPathway`

**Partial.** Live SAM evidence URL is the gold path. Partner
`next_validation_step` and published POCs are weaker paths.

Absent:

- Closed pathway kinds, recommended:
  `sam_notice | agency_forecast | published_poc | vehicle_ordering |
  industry_day | official_rulemaking`
- A hard rule: pathway URL must already be on an `EvidenceRef` or
  government-published contact. No invented people, no Apollo-only
  pathway.
- Forecast / award / news / web_lead structurally unable to be
  `sam_notice` (already true for `LiveSolicitation`; must stay true
  for the pathway enum)
- Event-lane pathways (tier A/B/C) as optional kinds, never as live
  solicitations

### 2.5 `SellerTransactionPath`

**Partial.** `PartnerDirection` is the closed vocabulary to reuse.

Absent:

- Binding the path to the **seller dossier** (can this client actually
  walk that path: prime ceiling, channel, vehicle)
- `prime_posture` (`agents/golden_press/prime_posture.py`) is derived
  from award history and is not attached to a LeadRow
- A fail-closed "path unknown" that yields HOLD rather than guessing
  prime
- Distinction: path is not a named person

### 2.6 `CurrentNextAction`

**Partial.** Templates live in `target_actions.RECOMMENDED_ACTIONS` and
thesis `watch_trigger` / partner `next_validation_step`.

Absent:

- One object with `verb`, `object`, `due`, `blocked_by`, `owner`
  (operator, not invented contact)
- Opp-ID verbs: review requirement span, finish attachment inventory,
  confirm SAM census, refresh stale coverage
- A rule that outreach verbs are illegal until Assess → Target **and**
  title-approval doors are open (existing contract surfaces)
- Deduping twenty identical target-action rows into one lead next action

### 2.7 `DecisionTrace`

**Partial, scattered.**

Present pieces: `EvidenceRef`, Assess coverage, approval fingerprint
(`agents/assess/binding.py`), live requirement review, decision-rule
receipts, targeting receipts, intake E1-E8, FactPack `F{n}` citations.

Absent:

- One append-only object a LeadRow can cite
- Stable step names (intake → search → assess → motion → pathway →
  path → action → tier)
- Input hashes already used by Assess (`projection_inputs`) reused
  rather than rehashed
- Explicit "rule that did **not** fire" (targeting already discloses
  unfired rules; Assess coverage already discloses missing attempts)

### 2.8 `Outcome`

**Absent.** No win / loss / no-bid / slipped / invalid-after-the-fact
object bound to a LeadRow or parent.

Do not reuse:

- `GateStatus.REJECTED` (strategy or Assess gate)
- `IntelligenceStatus.INVALIDATED` / `RETIRED`
- `LiveRecommendation.NO_BID`
- QA `DRAFT` / `RELEASE`

Those are process states. Outcome is a later market fact.

---

## 3. Rename collisions

Implement WS0 enums in a **new module**. Do not extend the colliding
tables in place.

| Token | Owner today | Meaning today | WS0 meaning | Required disambiguation |
|---|---|---|---|---|
| **T1** | `targeting_personas.json` `rule_id`, `targeting_rules.t1_t2_from_r1` | Renewal spec: R1 card whose nearest clock is **client** paper; seeks persona tiers 1-2 | Lead tier: highest actionable lead | Code: `TargetingRuleId.T1` vs `LeadTier.T1`. Persistence: never a bare `"tier": "T1"`. UI: "targeting rule T1" vs "lead T1". |
| **T2** | Same, displacement (R1 rival clock or R2) | Displacement spec | Lead tier: second actionable band | Same split. |
| **T3** | Targeting adjacency via paper holder (R4) | Not a lead tier at all | No WS0 T3 | Do not add a lead T3 to "match" targeting. |
| **tier** 1-4 | Persona ladder | Who to search for | Irrelevant to lead tier | Field name stays `persona_tier`. |
| **WATCH** | `Watchlist`, thesis `watch_trigger`, Candidate Review watch freshness, Signal Board "WATCH AOIs" | Several watch concepts | Lead tier WATCH | New field `lead_tier=WATCH`. Do not rename `Watchlist`. |
| **HOLD** | Partial-release holds, cutover holds, coverage holds | Process hold | Lead tier HOLD | `LeadTier.HOLD` vs `AssessHold`. |
| **REJECT** / `REJECTED` | Strategy packet, Assess gate, Horizon refine | Human refusal of a **process** | Lead tier REJECT | Never write lead REJECT into `ReviewStatus`. |
| **R1 / R2 / R3 / R4** | Decision rules | Account-decision rules | Trace only | Do not renumber as lead tiers. |
| **E1-E8** | Intake readiness (PR #1) | Company-mastery receipts | Not evidence ids | Assess `EvidenceRef.evidence_id` and dossier `EvidenceItem.evidence_id` (`E001`) are a third collision. Keep prefixes: `ready.E1`, `dossier.E001`, `assess.<id>`. |
| **EvidenceRef** vs **EvidenceReference** vs **EvidenceItem** | Assess / golden-press / intake | Three evidence atoms | Trace must wrap, not merge | Adapter maps. One WS0 view type may *cite* all three. |
| **SourceCoverage** | Assess contracts vs reports projection vs `CoverageState` | Three coverage dialects | Trace keeps them named | `assess_coverage`, `sweep_coverage`, `figure_coverage`. |
| **WebLead** | `agents/decisions/schemas.py` | Bounded web corroboration | Not a LeadRow | Keep the name. Do not subclass LeadRow. |
| **lead** in `band09_pass1_leadlist.html` | Historical Band 09 HTML | Contact / target list | Not WS0 | Ignore as authority. |
| **motion** | `build_motions()` commercial unit | Outreach batch | `BuyingMotion` | New type name required. Do not rename `motions.py`. |
| **recommendation** | `LiveRecommendation`, `FitRationale.recommended_action`, target `recommended_action` | Three actions | Only the last two inform `CurrentNextAction` | Assess recommendation stays on the parent. |
| **status** | `IntelligenceStatus`, `GateStatus`, `CoverageStatus`, QA state, email_status | Overloaded | Lead tier is not `status` | Field name `lead_tier` only. |
| **Opportunity** in `RawOpportunity` / `FusedOpportunity` / `PartnerOpportunity` / Market Map "Federal Opportunities" | Mixed | Live vs fused vs partner vs slot | Parent subject vs child | Partner stays a path, never a live opp. |
| **assess** | `AssessmentResult` vs `AssessRun` vs `AssessmentDocument` vs FOA title | Four generations | Parent = ledger record in `AssessRun` | Name the WS0 parent `OpportunityAssessment`. Do not call it `AssessmentResult`. |
| **profile** | `CapabilityProfile` vs `ClientProfile` vs dossier | Three seller objects | Cite dossier + `ClientProfile` | Do not revive `CapabilityProfile` as the ontology. |
| **target** | Assess → Target door, Band 09, Market Map nested targets, `run_targets.py` | Outreach unlock | Not LeadRow | Lead gen may exist before Target is unlocked; outreach may not. |

### 3.1 Mapping table: Assess recommendation → suggested LeadTier (adapter only)

This is a **later adapter suggestion**, not a silent alias.

| `LiveRecommendation` | Typical `LeadTier` if and only if all four LeadRow factors exist | Otherwise |
|---|---|---|
| `PURSUE` | T1 or T2 (clock + path decide) | Parent only; no child |
| `MONITOR` | WATCH | Parent only |
| `RESEARCH` | HOLD | Parent only |
| `NO_BID` | REJECT | Parent remains in census |
| `NONE` | no child | Parent only |

Thesis `PROPOSED` / `RESEARCH_NEEDED` → WATCH or HOLD.
Partner without capability/access evidence cannot mint a child (already
fail-closed).

---

## 4. Recommended additive schema package (file layout only)

Do **not** implement these files in this spike. Do **not** delete or
move Assess models. Do **not** put LeadRow inside
`agents/assess/contracts.py` (that file is a contract surface).

```
agents/leadgen/                      # NEW package, sibling of agents/assess
  __init__.py                        # exports names only, once contracts exist
  contracts.py                       # OpportunityAssessment, LeadRow, and the
                                     # four required factors; frozen, extra=forbid,
                                     # same _FrozenContract pattern as Assess
  tiers.py                           # LeadTier = T1|T2|WATCH|HOLD|REJECT
                                     # plus display labels "lead T1" / "lead T2"
  motion.py                          # BuyingMotion (do not edit
                                     # agents/golden_press/motions.py)
  pathway.py                         # ActionableExternalPathway
  seller_path.py                     # SellerTransactionPath; import
                                     # PartnerDirection from assess.contracts
  next_action.py                     # CurrentNextAction
  traces.py                          # DecisionTrace, Outcome
  ids.py                             # lead_id / parent id helpers; no I/O
  adapters/                          # later workstream; empty in the first
    __init__.py                      # schema PR except docstrings
    assess.py                        # AssessRun ledgers -> parent(+optional child)
    intake.py                        # CompanyDossier / IdentityResolution cites
    motions.py                       # build_motions / target_actions -> factors
    coverage.py                      # wrap the three coverage dialects
    market_map.py                    # projection TO slots 1/5/6/7; no minting

tests/test_leadgen_contracts.py      # first schema PR: construction +
                                     # parent-required + four-factor product +
                                     # T1/T2 namespace tests
tests/test_leadgen_adapters.py       # later; hermetic fixtures from
                                     # tests/test_assessment_document._searches

docs/LEADGEN_CONTRACT.md             # later, only if the operator wants a
                                     # contract surface; this spike is not it
```

Layout rules for the implementer:

1. Copy the Assess frozen-model pattern (`ConfigDict(frozen=True)`,
   tuples after validation, lists accepted at construct time).
2. Import `EvidenceRef`, `AssessScope`, `PartnerDirection`,
   `LiveClassification` from `agents.assess.contracts`. Do not duplicate.
3. Import identity / dossier types from `agents.intake` **after PR #1
   merges**. Until then, cite them by name and keep adapter fields as
   optional strings (`dossier_schema_version`, `identity_status`).
4. Do not import `targeting_rules` or Apollo modules from
   `agents/leadgen/contracts.py`.
5. Do not add LeadTier into `data/reference/targeting_personas.json`.
6. Do not add LeadRow fields onto `AssessRun` or `LiveSolicitation`.
   Parents stay valid with zero children.
7. Persist later (not in the first schema PR) under something like
   `data/state/lead_rows/<slug>--<name-hash>/` using
   `tools.artifacts.atomic_write_json`. Do not write into
   `data/state/assess_runs/`.
8. Market Map adapter is one-way: LeadRow → slot payload. Slot payload
   never becomes the store.

Suggested constructor invariants (encode in that later PR):

- `OpportunityAssessment.subject` is one of live / thesis / partner-link
  and the id exists on the cited `AssessRun` when `assess_run_id` is set.
- `LeadRow` requires the four factors and `parent_assessment_id`.
- `ActionableExternalPathway.kind == sam_notice` requires NOTICE-tier
  primary SAM evidence (reuse `LiveSolicitation` validators; do not
  weaken them).
- `LeadTier` and `TargetingRuleId` are distinct types; a test asserts
  `LeadTier.T1 is not TargetingRuleId.T1` and that JSON for each uses a
  namespaced key (`lead_tier` vs `targeting_rule_id`).
- `Outcome` may be missing. Missing outcome is not REJECT.

---

## 5. Risks that would weaken opportunity identification

1. **Promoting `AssessmentResult` to parent.** The scoring stack has no
   SAM-host rule, no requirement-span review, no attachment inventory,
   no coverage ledger. Opp ID would become a cosine of keywords.

2. **Deleting or "simplifying" `LiveSolicitation.BID_NOW`.** Metadata-only
   triage would mint Lead T1 rows. That is the exact failure
   `contracts.py` exists to prevent.

3. **Letting Market Map slot 1 own `lead_id`.** Slot 1 is ranked
   references. If it mints identity, a projection bug silently creates
   or hides leads, and release hashing fights the lead store.

4. **Making `lila_release` wait on LeadRow completeness.** The ruling
   says Market Map must not block LeadRow contracts. The converse is
   also true: missing children must not block an Assess-approved map.

5. **Aliasing targeting T1/T2 to lead T1/T2.** A renewal **rule** would
   display as a top **lead**, including award-only R1 cards with no
   posted solicitation. Opp ID and account-decision intel would collapse.

6. **Minting LeadRows from `WebLead`, forecasts, or USAspending
   `RawOpportunity`.** Those types are already barred from the live
   ledger. A convenience adapter would reopen that hole.

7. **Skipping Step 1 identity.** PR #1 exists because a wrong-company
   NAICS set produces a confident wrong notice set. A LeadRow with a
   strong SAM span against the wrong firm is a false market.

8. **Putting people on the LeadRow primary key.** Target-action grain is
   (person, spec). That invites auto-outreach and contact invention.
   Pathway may *cite* a published POC. It may not require a person.

9. **Merging the three coverage dialects into one enum.** A Signal Board
   `NOT RUN THIS REFRESH` is not an Assess `not_registered`. Collapsing
   them hides "never looked" vs "looked, zero".

10. **Using LeadRow as the Assess store.** If live census rows only exist
    when a child can be built, excluded / unscreened / awarded notices
    disappear from the census. Opp ID requires those rows to remain.

11. **Inferring `scope.preset` from lead volume.** Step 1 readiness
    forbids this. Empty LeadRows in a focused slice are not a dead
    market (L18). Re-focusing is a new sweep.

12. **Award-as-opportunity role reversal.** `EvidenceReference` already
    forbids a USAspending award from proving an open opportunity.
    SellerTransactionPath may cite an award as **path** evidence.
    BuyingMotion may cite it as a renewal clock. Neither may set
    pathway `sam_notice`.

13. **Quietly editing `agents/assess/contracts.py` to "add a few lead
    fields".** That file is a contract surface
    (`docs/CONTRACT_SURFACES.md`). Additive lead types belong in
    `agents/leadgen/`. Assess changes need an isolated, labeled diff
    and operator approval.

14. **Auto-outreach / contact invention.** Out of scope. Apollo remains
    behind Assess → Target and title-approval doors
    (`CONTRACT_SURFACES.md`). A LeadRow next action may say "confirm
    published POC" or "Target door still locked". It may not say
    "email this invented person".

---

## 6. Recommended parent / child join (for the later schema PR)

```
CompanyDossier + ClientProfile     (seller ontology; Step 1 / PR #1)
        │
        ▼
   AssessRun                       (unchanged)
      ├── LiveSolicitation[]       ──► OpportunityAssessment (kind=live)
      ├── OpportunityThesis[]      ──► OpportunityAssessment (kind=thesis)
      └── PartnerOpportunity[]     ──► SellerTransactionPath
                                        (and/or parent kind=partner_link)
        │
        ├── (optional) BuyingMotion × Pathway × Path × NextAction
        │         │
        │         └── LeadRow (child, lead_tier=…)
        │
        └── Market Map projection (slots 1,5,6,7,8)   # display only
```

Rules:

- Every `LeadRow` has exactly one parent `OpportunityAssessment`.
- A parent may have zero, one, or many children (distinct pathways or
  seller paths). Do not collapse amendments into extra parents; family
  id stays on the live record.
- Award-only R1/R2 motions may create a parent only if an
  `OpportunityAssessment` subject is defined for that award **as a
  motion subject**, not as a live solicitation. Safer first cut: those
  motions stay off LeadRow until a live or thesis parent exists.
- Partner records already must `linked_assess_ids` into live/thesis.
  Reuse that law for children.

---

## 7. What the next Claude may do, and must not do

**May do (later, not this PR):**

- Add `agents/leadgen/` as sketched in §4 with tests that freeze the
  four-factor product and the T1/T2 namespace split.
- Write adapters that **read** Assess / intake / motions and emit
  parents and children.
- Project children into Market Map slots without changing slot ids.

**Must not do:**

- Implement those packages in this spike PR.
- Edit `docs/MARKET_MAP_CONTRACT.md` or `product_bundle.py`.
- Merge PR #1 or redesign `agents/intake/`.
- Delete `RawOpportunity` / `AssessmentResult` / `LiveSolicitation`.
- Store lead tiers in `targeting_personas.json`.
- Unlock outreach from LeadRow.

---

## 8. Sources checked

In-repo: `agents/assess/contracts.py`, `ledger.py`, `scoring.py`,
`assess_agent.py`, `pursuit_grade.py`; `agents/schemas.py`;
`agents/decisions/schemas.py`; `agents/qualify.py`;
`agents/reports/document.py`, `source_coverage.py`;
`agents/golden_press/motions.py`, `target_actions.py`,
`decision_rules.py`, `targeting_rules.py`, `evidence_objects.py`,
`external_product_projection.py`; `data/reference/targeting_personas.json`;
`tools/capability.py`; `docs/MARKET_MAP_CONTRACT.md`,
`docs/CONTRACT_SURFACES.md`, `docs/CONVENTIONS.md`,
`docs/step1_intake.md`, `docs/assess_agent_plan.md`.

Unmerged: PR #1 `agents/intake/{dossier,identity,readiness,extract}.py`
on `cursor/step1-intake-mastery-da3f`.

Not found in this checkout or GitHub search: "Lead Gen Standard v1.2",
"Build Plan v1.0", `LeadRow`, `BuyingMotion`,
`ActionableExternalPathway`, `SellerTransactionPath`, `DecisionTrace`
as types. The locked ruling in the task header is the WS0 name
authority until those documents are checked in.
