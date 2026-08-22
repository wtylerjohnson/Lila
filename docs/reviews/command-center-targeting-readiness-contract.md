# Command Center targeting-readiness contract

## State model

Target unlock and Targeting Review completion are separate server-owned events.
Unlock permits work after a current Assess approval. Completion requires a
second receipt and is never inferred from target count, contact count, or a
polished report.

## Per-play requirements

Every promoted play is reviewed across Buyer, Acquisition, Partner & Access,
Incumbent Displacement, Positioning, and Event. Each play/lane pair must be one
of:

- covered by a complete target action bound to that same play and lane;
- no qualified target found; or
- not applicable to that play.

An unresolved lane requires structured evidence checked, the missing role or
route, an exact next research action, named owner, and deadline. Free-text
length alone cannot satisfy the gate.

## Complete target action

A promotable action requires a current target and authoritative source plus
route, disposition, commercial motion, proposed company role, first ask,
message, CTA, action window, owner, learning goal, desired outcome,
qualification question, stop condition, and promotion criteria. Reject and
defer require a reason. Sourced identity/contact facts remain visually and
semantically separate from operator-authored commercial guidance.

## Receipt binding

The Targeting Review receipt is SHA-256-bound to:

- the full current target/contact record, including channel values, grades,
  agency, bucket, needs, reason, title, and source;
- the operator-authored plan;
- the exact current Assess approval; and
- the exact Target unlock decision.

Target, contact, plan, Assess, or Target-unlock drift fails closed.

## Press and release

Assessment, Evidence, Targeting, and Assets authorize the first Press.
Output readiness is Pending until that authorized generation passes QA.
Release requires all five. Candidate review, client views, agency reports,
target reports, capture briefs, HTML/PDF preview, HTML downloads, dossiers,
and PDF export all consume the server-owned targeting gate.

## Verification

- Focused suite: 173 passing across Command Center, Target gate, UI,
  dashboard download, and release-state tests.
- Full suite: 3071 passed, 12 baseline failures, 3 skipped.
- Browser: Apex exposed 4 promoted plays × 6 lanes (0/24 before operator work),
  exact structured gaps, full action record, and server refusal while locked.
