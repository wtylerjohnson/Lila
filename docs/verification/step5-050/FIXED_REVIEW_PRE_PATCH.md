# Step5 fixed-review repairs before publication

Fixed candidate55bf3aa800fd7a3dfdecd8d20ccc6abd2591a28a passed37 focused tests
and5802 combined tests but the operator's additional probes found two OPEN P2
contract defects. Accept both findings as required repairs, not residuals.

S5-REVIEW-050-01: preserve original semantic predicate/value proposals separately
from validated states. Add original-proposal disagreement and coverage outputs;
initial agreement reflects originals. Validated counts remain conservative and
separately labeled. Ignore differences only in claim/evidence IDs or rationale.
Missing predicates remain distinguishable from explicit unknown proposals.

S5-REVIEW-050-02: admitted contexts/check records must validate strict nested
schema types and canonical bytes, not Python coercive dictionary equality.
Boolean domains and integer-only Unicode spans are explicit. Apply checks to all
admitted receipts, not only selected ones; preserve exact positive controls.

Retain original review/probes/failing inputs. Add all six repros as tests, run
focused and combined regression after code changes, and obtain bounded existing
operator confirmation before publishing one combined Step5 + root P1 candidate.
Root P1 remains open and Step6 pending. Frozen cases and legacy harness unchanged.
