# REPAIR-STEP5-TRUST-REVIEW-050

**Design disposition: retain the proposed external trust boundary; correct the evidence-insufficiency rule and make the guards below explicit before final implementation.** This is a document review of the coordinator implementation decision and accepted32-case plan. No moving implementation was inspected, no new probe was run, and no code defect or completed guard implementation is claimed.

The caller-owned pre-prepare manifest digest, externally retained prepared anchor and externally supplied receipt-digest allowlist are deliberate trust inputs. I do not recommend replacing them with a case-local seal, mandatory signatures, a new identity service or a supposed semantic hash check. Their protection assumes a trustworthy caller/custodian. A compromised trusted caller can admit false assessments; that limitation must remain explicit.

## G1 — Required design correction: evidence insufficiency is not a decisive relevance negative

The coordinator decision, lines39-42, permits supported negative “route/evidence predicates” to decide the objective. That conflicts with accepted cases `insufficient_evidence`, `unreadable_not_empty` and `negative_assertion_discovery` if `evidence_sufficient=false` is included.

An authorized checker can correctly record “the SOW is unavailable; evidence is insufficient.” That substantiates a knowledge gap. It does not establish that the opportunity fails the supplier-control objective. A registered, perfectly bound receipt must not transform this gap into not_relevant.

Required rule: keep evidence sufficiency/availability/authority as epistemic status and an affirmative-credit gate, never a standalone decisive-negative predicate for business relevance. The frozen decisive-negative allowlist must exclude grade0, missing evidence, discovery-only status, unreadability and failed acquisition/certification. A supported substantive fact such as “the entire purchase is audiovisual hardware, supplier-control software excluded” can instead establish fit=false. A separate valid fit=false proof may decide the objective even when timing or evidence about other dimensions is unknown. Source-policy certification failure remains a comparison-validation failure, not an opportunity-quality negative.

Acceptance obligation: identical unavailable SOW plus a registered insufficiency receipt remains unknown; a separately supported scope exclusion remains not_relevant with that evidence gap still displayed.

## G2 — Aggregate conflicts before evaluating decisive negatives

Lines39-44 introduce conflicted states but do not fully specify their interaction with decisive negatives. Never scan individual receipts for any accepted false value before aggregating the exact predicate/context.

Two accepted, unsuperseded receipts supporting fit=true and fit=false for the same subject/objective must yield fit=conflicted. Neither last receipt order, an issuer ranking invented at score time, nor the mere existence of a negative receipt may choose the result. A material conflict remains unknown overall unless another independently resolved criterion already decisively falsifies the objective. Conversely, an unrelated action conflict must not erase a valid decisive scope rejection.

A correction/supersession must be an explicit authorized relationship between receipt digests under the frozen support policy, retaining both records. Registry insertion order or later approval time is not substantive supersession. This is receipt interpretation, not the separate root source-clock investigation.

Acceptance obligation: reorder duplicate/opposing receipts and obtain the same validated states; contradict the decisive predicate versus an unrelated predicate and preserve the distinction.

## G3 — Bind the action/route relationship and the scope of negative claims

The proposed exact packet/subject/source/brief/objective binding is necessary. For action alternatives it must also identify which action, route and offering the proof addresses, or explicitly establish their relationship. A family ID alone cannot join a fit fact from one call, an open action from another and a route available only for a third into a single relevant opportunity.

Define predicate meanings in the frozen objective/policy:

- `route_eligible` means the rubric's plausible route for this offering/action, not necessarily fully qualified prime eligibility. A documented prime-only restriction cannot establish that all permitted partner routes fail when partnership eligibility remains unknown.
- `continuation_open=false` cannot mean “no continuation evidence was supplied.” It needs affirmative disproof within a declared action universe and assessment scope. No unlimited claim that all conceivable future buying has ended is defensible from a closed RFI.
- Cross-notice support is permitted only through a validated, predicate-appropriate relationship, such as an explicit amendment or continuation. URI/subject equality or title similarity cannot invent that relationship.

Bind the action/route identifiers or relation-receipt digest into each relevant claim/support receipt, alongside the already proposed fields. Distinguish a verified direct-route negative from a verified negative of every route allowed by the objective. Keep the stated response OR continuation rule; it correctly preserves unknown when continuation is unresolved.

Acceptance obligation: block unrelated within-family joins; retain a supported amendment/continuation positive. Direct route false + permitted partner route unknown must not become overall route false.

## G4 — Pin the support-registry snapshot and require both digest admission and issuer authority

The separate caller-supplied registry is appropriate. Specify that a scoring event uses one immutable parsed snapshot with a caller/custodian-controlled identity or digest, recorded in the result alongside the external input and prepared anchors. Re-reading a mutable allowlist midway through a score can mix authorization versions even when every individual receipt hash matches.

Every usable receipt must satisfy all of: exact digest admitted, issuer authorized for this case/policy/receipt role, complete semantic tuple matches, synthetic/live domain matches, and source/binding/admissibility checks pass. Authorized issuer alone must never waive digest admission; a registered digest must not waive issuer or source-policy checks. Reviewer/case content cannot choose a replacement registry, add an issuer, or turn a supplied string path into authority.

Use strict receipt schema/types and reject duplicate JSON keys, unsupported predicates/schema versions, duplicate conflicting identifiers and boolean/numeric coercions. Verify exact canonical span encoding and quote against the bound raw extraction. The source-subject relationship itself must be bound to checked original identity evidence; a free subject field inside a card is not that check.

Define corrections/revocations for subsequent scoring events, retaining the old result and its registry snapshot. Do not silently rewrite historical output or expand an existing snapshot after seeing the result. These are reproducibility/admission guards within the existing caller trust model, not a demand for cryptographic proof of substantive truth.

## G5 — Make real issuance operational without making the checker a hidden adjudicator

Lines21-30 correctly prohibit automatic appointment and live receipt issuance in this build. Before real use, the freeze/registry should record the checker's actual principal, role, human/AI provenance, permitted case/policy and independence attestation. Different arbitrary issuer strings are not evidence of different principals. Do not represent AI support checking as human domain validation.

Specify the custodian/checker separation. The proposal explicitly excludes operators and final reviewers as checker, but does not say whether the registry custodian can approve their own support assessment. For real comparative use, require a separately named admission approver or explicitly document this reduced independence; do not silently claim independent source checking when one principal performs both roles.

A workable sequence within the proposed design is:

1. Freeze objective, source policy and checker role/eligibility; retain caller input anchor.
2. Prepare the immutable neutral packet and retain its external anchor.
3. Checker assesses the exact proposed source claims against original material; record support, contradiction or insufficiency with rationale and identity/authority context. If final grade/rationale is part of the receipt tuple, that claim must already exist before the checker binds it. Do not issue a wildcard receipt authorizing unseen later reviewer wording.
4. Custodian records the original-source check and admits exact receipt digests in a named registry version. Support identities, product/rank maps and the other review stay out of each reviewer's bundle.
5. Score using the pinned registry and anchors. Preserve each reviewer's original judgment and validation outcome; an unsupported judgment becomes held/refused, never silently rewritten to the checker's preferred grade.

A shared checker is a shared validation dependency for the two final reviewers. Disclose that dependence. Its support screening is not a third-reviewer adjudication and must not eliminate disagreements or produce an adjudicated winner. If grade/rationale changes, require a new exact claim-bound receipt or leave that claim unsupported; a receipt for source facts cannot automatically authorize any grade2/3 interpretation of those facts.

No actual checker needs to be appointed now. Until this real-use policy and registered support exist, live claims stay unknown as proposed.

## What already addresses the baseline risks

The proposal expressly retains source certification before prepare/score, source-byte/URI/subject/span/authority/cutoff checks, external anchors, exact approved receipt digests, no reviewer-created authority, synthetic/live separation, grade0 not decisive, all affirmative gates and response/continuation alternatives. These are sound stated controls; they need implementation verification on a fixed candidate later. Hashes establish identity of an admitted assessment, not the correctness of the assessment.

The authority guard must protect positives as well as negatives. A trusted checker can be wrong. The realistic claim is “source-grounded assessment by the named authorized checker, bound to this exact decision context,” with disagreements and proof limits retained. It is not machine-proven semantic truth, market completeness, qualification or seller readiness.

## Handoff scope

This review supplies one design correction and four groups of concrete acceptance guards. Examples above are reasoned counterexamples, not executed probes or observed implementation failures. `VERIFICATION.json` binds the reviewed document versions and confirms baseline preservation. Source code, strict Assess, source clocks, LeadRow, full suites, live/source calls, Fable and new workers were not touched. Coordinator owns final policy wording/implementation and subsequent exact-candidate review. Operator checkpoints after one completion.
