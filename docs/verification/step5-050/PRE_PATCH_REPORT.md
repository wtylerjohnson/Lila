# Step5 v5 scorer implementation decision

Accepted base ca6620dd9a4387b5145e49588a878ca3efb925e4. The operator baseline,
32-case plan,14 artifact hashes,502 generated fixture files,40 preserved harness
files and21 source receipts were verified. No original protocol/harness or frozen
case is edited. Implement in tools/benchmark_v5 inside the existing checkout on
codex/step5-evidence-states, with byte-identical legacy v3/v4 policy comparators.

Extend native custody/first20/independence/source-certification patterns, then add
an isolated v5 packet/claim/support/result contract. Do not call legacy
classification for v5 judgments or migrate old packets implicitly.

Trusted boundary: the caller supplies a case-manifest digest before prepare and
keeps the returned prepared-packet anchor outside the case/reviewer folders. The
scorer never derives its trusted root from the review or private seal. A separate
caller-supplied support registry contains approved receipt digests and authorized
issuer IDs. Receipts bind the exact packet, subject, source/passage/span/raw hash,
claim value, rationale, brief, objective, assessment and support-policy hashes.
Reviewer supported=true, grades and self-written receipts have no authority.

Real-use support policy: the freeze names a source-support checker, independent
of product operators and the two final reviewers, who records a source-grounded
assessment before its exact receipt digest is admitted to the custodian-managed
registry. The coordinator may register that checker and receipt only after its
original-source review is recorded. No checker is automatically appointed and no
real support receipt is issued in this build. Without registered support, claims
remain unknown. A hash proves binding to an authorized assessment, not semantic
truth; substantive accuracy remains the named checker's responsibility. Synthetic
fixtures use separately authored audited facts and explicitly synthetic trust
anchors; they are not accepted as live support or an entailment engine.

Custody precedes semantics. Validate exact input manifests, code/policy freeze,
original source bytes, URI/subject linkage, exact passage spans, authority and
cutoff before accepting a registered receipt. Source certification runs before
prepare and again before score through the preserved native policy. Paths are
relative and confined with symlink/traversal rejection. Private product/rank maps
and support identities stay outside neutral reviewer bundles.

Keep fit, response_open, continuation_open, route_eligible and evidence_sufficient
states separate. Claims aggregate to true/false/unknown/conflicted only after
support validation. Negative fit or failure of every permitted route may decide the
objective only when supported. Evidence insufficiency is a knowledge gap, never
a standalone decisive negative. For respond_to_this_notice, response false decides
negative. For actionable_buying_now, response OR continuation is false only when
both supported false; expiry alone leaves broader action unknown. Preserve grade
and rationale; relevant additionally requires a supported grade2/3 assessment and
all required affirmative dimensions. Grade0 never supplies a decisive negative.

Retain raw judgments, accepted/rejected support reasons, decisive claim IDs, all
unknown/conflicted dimensions and material unknowns. Compare validated dimensions
and grade for agreement, preserving differing evidence/rationale. Fixed20 family
credit requires resolved family identity and independent native retrieval. Keep
duplicates, injected, unresolved, known and unfilled slots, reviewer/agreed counts,
disagreement, no adjudication, no automatic winner and no novelty claims.

S3-ROLE-007 native label remains a separately labeled diagnostic follow-up, not
fabricated by v5. Root strict Assess/source-clock P1 and Step6 surfaces remain
separate. Required proof:32-case synthetic matrix, hostile bindings, relocation,
CLI prepare/score/demo, exact legacy comparisons, local combined regression and
one existing operator support review, then published exact-SHA CoS/Fable.

Trust review G1-G5 incorporated: aggregate conflicts before precedence; exact
action/offering/route-universe scope on every claim; one externally pinned registry
snapshot; explicit digest revocations in a new registry/result only; actual checker
principal distinct from admission approver, operators, custodian and reviewers.
All admitted claims pre-exist their exact support receipt. A shared checker is a
shared validation dependency, not a third adjudicator. Same-subject record binding
is deliberately narrow; cross-notice relationships require a future adapter.
