# Step5 evidence states and combined source-clock candidate

The v3/v4 scorer could hide a supported scope exclusion behind unrelated timing
uncertainty. It could also accept an unsupported positive or negative merely
because a review cited a passage. The separately versioned v5 harness binds each
assessment to original evidence, a frozen objective and an external support
registry before evaluating a decision. Old packets require an explicit future
migration; this change never reinterprets frozen calibration004/005.

The parent root change `dbc2458de359e25077cffadf2b7889e350ab6802` repairs the separate
strict Assess acquisition-clock fallback. Its original verification and contract
extension remain in `../S4-SOURCE-CLOCK-001/`. The combined Fable scope is Step5
plus S4-SOURCE-CLOCK-001. That P1 stays open until combined external acceptance.
Step6 remains pending. No production merge, authentic-source success or recovered
lead is established by this package.

## Portable commands

Run from this repository root with Python3.10+; the v5 package and unittest tests
use only the standard library. Local validation uses Python3.14.5. The combined
product suite additionally requires the repository's existing dependencies.

```
PYTHONDONTWRITEBYTECODE=1 python -m unittest discover -s tests -p 'test_benchmark_v5*.py' -v
PYTHONDONTWRITEBYTECODE=1 python -m tools.benchmark_v5 --help
PYTHONDONTWRITEBYTECODE=1 python -m tools.benchmark_v5 demo /private/tmp/v5-new-demo
```

Choose an unused directory for the synthetic demo. On macOS use a physical path
such as `/private/tmp`; symlink roots and components are deliberately rejected.
The demo authors synthetic source records, capture receipts, external trust files,
two proposed reviews, audited support receipts and `result.json`. It calls no
source, product, model or paid service. Synthetic audit facts are separately
declared in `synthetic.py`; the fixture checker cannot approve a contrary claim
merely because its reviewer wrote `supported=true`. This oracle is not a language
entailment engine or an authentic native product run.

The fixture produces the exact inputs consumed by the production-form CLI:

```
python -m tools.benchmark_v5 prepare CASE NEW_PACKETS \
  --input-trust EXTERNAL_INPUT_TRUST.json --anchor-output NEW_EXTERNAL_ANCHOR.json \
  --reviewer-one REVIEWER_ONE --reviewer-two REVIEWER_TWO
python -m tools.benchmark_v5 score CASE PACKETS NEW_RESULT.json \
  --anchor EXTERNAL_ANCHOR.json --support-trust EXTERNAL_REGISTRY.json \
  --support-trust-sha256 CALLER_PINNED_REGISTRY_SHA256 --support-dir RECEIPTS
```

Uppercase arguments are caller-supplied paths/principals, not executable example
values. Input and prepared anchors must be outside case and packet folders. The
registry must also be outside the untrusted receipts directory. The API accepts
the same explicit anchors. It never derives trust from `PRIVATE_key.json`, a
case-local seal, a reviewer field or an issuer name alone. Outputs are exclusive
create; old packets, anchors and results cannot be overwritten by these commands.

## Trust, admission and correction policy

Before any real use the authorized caller freezes the case/code/brief, aware
reference time, offering, allowed route universe and finite continuation scope.
Every case card declares one subject/action. The lock names actual checker
principal IDs. No checker is appointed by this build and there is no default live
receipt issuer. An empty checker registry is permitted: claims remain unknown.

The checker must be independent of the product operators, neutral custodian,
admission approver and both final reviewers. Registry principal, role, human/AI
kind, provenance and independence attestations are required. Different labels
are not identity verification; workflow honesty is still a trusted prerequisite.
The registry admission approver must be independent of operators and reviewers.

The reviewer first proposes the exact predicate/value/rationale/evidence/scope.
The named checker then reviews that exact proposal against original material and
records a source-grounded assessment. A separately named approver admits the
exact receipt digest into one externally controlled registry snapshot. The caller
pins that snapshot digest before scoring; the scorer reads it once and retains
the snapshot and digest in the result. Both digest admission and frozen issuer
authorization are required. Changing a grade or its claim rationale needs a new
receipt; unvalidated free-form row commentary is retained only as raw judgment.

A receipt binds packet bytes, blind card, original subject, private family identity
digest, source/passage IDs, original URL, publication/retrieval metadata, exact
Unicode codepoint span/quote and raw-record digest, brief/objective/policy hashes,
action/offering/routes/continuation scope, proposed claim, issuer, verdict and an
exact checker-record digest. The checker record must repeat the same context
digest. A hash verifies that the admitted assessment is the one being scored; it
does not prove the assessment substantively correct.

All admitted unsuperseded claims in the same context can expose a conflict, even
when a reviewer cites only one side. Receipt order and timestamps never select a
winner. For correction, create a new externally pinned snapshot, retain the old
receipt in `accepted_receipts`, and explicitly map its digest to a nonempty reason
in `revoked_receipts`; admit a newly checked replacement separately. Produce a new
result. Never expand or rewrite an old snapshot after seeing a score. Cross-case
or cross-policy receipts cannot be reused. There is no implicit supersession.

Synthetic and live domains must match throughout. The source-support checker is
a shared validation dependency, not a third adjudicator. Raw reviewer claims,
support outcomes, disagreement and agreed subtotals remain separate. Arbitrary
checker/admission principal strings cannot prove organizational independence.
Compromised trusted callers/checkers and checker semantic errors are outside the
hash guarantees; this package must not be represented as automatic semantic truth.

## State and source rules

Predicates are `fit`, `response_open`, `continuation_open`, `route_eligible`,
`evidence_sufficient`; grade is a separately supported integer0-3. Each aggregates
to true, false, unknown or conflicted before classification. Supported fit=false
or failure of every permitted route may decide not_relevant despite unrelated
unknowns. Grade0, unavailable evidence and evidence_sufficient=false never do.

For `respond_to_this_notice`, a supported response closure decides this response
objective only. For `actionable_buying_now`, response OR the explicitly declared
continuation must be evaluated. False plus unknown remains unknown; both branches
must be independently disproved to decide that action scope is closed. The
checker must not interpret absent continuation evidence as affirmative disproof.
An unbounded assertion that all future buying has ended is outside this contract.

Route means a plausible route for this offering/action, not complete prime
qualification. A direct-route failure cannot disprove permitted partner routes.
Every claim must bind the full frozen route scope; the checker substantively
reviews the quantifier. Relevant requires supported grade2/3, fit, action, route
and sufficient original evidence. A credible grade2 investigation can retain an
honest qualification question. All unresolved dimensions and evidence gaps remain
visible even when another predicate has decisively excluded the objective.

This version deliberately accepts only retained JSON notice records with schema
`benchmark-original-notice.v1`, original `notice_id`, `source_id`, `source_url`,
`published_at_utc` and `text`. Exact spans refer to that text. This is a bounded
record adapter, not authentication of upstream government bytes or a parser for
arbitrary HTML/PDF. Real normalized records require a separately audited original
source chain. A same-family title/URL/identifier cannot join unrelated subjects or
actions; cross-notice amendment/continuation relations need a future adapter and
are refused here. Same-subject explicit continuations are supported. Missing,
unreadable, failed, discovery-only, non-substantive or undated evidence cannot
support either positive or negative substantive claims.

The byte-identical vendored v4 native-refresh policy runs before packet creation
and again before scoring. A certification failure emits no comparable score; it
is not a zero-quality result. Case files use strictly confined relative manifests
and code/policy freeze hashes. Neutral reviewer bundles expose original identity
and authority but no product, native rank, private family map or support issuer.
Blindness still depends on honest workflow attestations, not OS access control.

Fixed first20 of first100 accounting retains duplicates, known independently
retrieved, injected, unresolved identity/provenance, rank21 exclusion and empty
slots. Credit is once per resolved family with independent native retrieval.
Unknowns/disagreement remain unadjudicated; no winner, tie, novelty, possible yield,
market emptiness, seller readiness or production readiness is inferred.

## Proof scope

`ACCEPTANCE_PLAN.json` preserves the original32-case plan and original
NOT_RUN baseline annotations. `CASE_RESULTS.json` records implementation results
separately. `LOCAL_VERIFICATION.json` and `COMMANDS.json` bind actual logs, tested
tree, publication child and exact commands without self-hashing a receipt.
Publication and clean archive reproduction are separate from local regression.
The named portable_exact_commit unit test is only a CLI check; the remote SHA
receipt supplies the actual publication proof.

All Step1 R1-R7, Step3 retained surface/diagnostic/role/semantic residuals and
Step4 source/transient-mutation/cloud-proof limitations retain their existing
meaning. S3-ROLE-007 is not manufactured in the scorer. No frozen scores change.
