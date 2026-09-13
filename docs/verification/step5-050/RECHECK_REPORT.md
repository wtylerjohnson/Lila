# REPAIR-STEP5-RECHECK-050

**PASS — both assigned defects are closed within this narrow offline recheck on exact candidate `482091a271d632e87635473023d5c918e2bc02d3`.** Parent: `55bf3aa800fd7a3dfdecd8d20ccc6abd2591a28a`; existing branch: `codex/step5-evidence-states`. No remaining concrete finding was identified within the two-defect scope. This is not combined Fable acceptance or closure of root source-clock P1.

All six original repros and 20 nearby controls pass. Five selected unittest methods also pass; the broad 39-test suite, previous 30-case review, baseline, root P1 review, full regression and archive/CLI review were not repeated. Each original repro uses a new synthetic freeze for the repaired code while retaining byte-identical original source records. The three original disagreement repros also retain identical raw judgments and unchanged validated agreed counts. Original55bf3aa artifacts were not edited.

| Original defect | Disposition | Recheck result |
| --- | --- | --- |
| S5-REVIEW-050-01 / G5 | CLOSED_BOUNDED | Unsupported fit false/true, grade0/3, and fit false/unknown now produce one original-proposal disagreement and one union disagreement. Validated-state disagreements remain empty, validated agreement remains1, and classifications/counts remain unknown. Initial semantic and complete agreement are0. |
| S5-REVIEW-050-02 / G4 | CLOSED_BOUNDED | Admitted float span `[0.0,282.0]`, boolean span `[false,282]`, and checker-record `synthetic=1` each raise ValueError before any score file is created. Each defect is also rejected when both reviewers submit no claims and thus use none of the admitted receipts. |

The G5 repair at `tools/benchmark_v5/scorer.py:85-99` compares original semantic predicate/value proposals separately from validated states. `scorer.py:113-118` exposes proposal coverage, both disagreement/agreement measurements and the explicit validated-only basis of agreed counts. The recheck confirms:

- Missing proposals differ from an explicit unknown. Coverage is0 versus1 and original disagreement remains visible.
- Two empty reviews have zero completed slots and complete initial agreement0. Two matching sets of six explicit unknowns have coverage6/6 and complete proposal agreement1, while relevance remains unknown with zero confirmed credit.
- Omitting each of the six slots in turn gives5/6 coverage and complete initial agreement0, even when both reviewers omit the same slot.
- A complete supported positive retains agreement1 and one relevant family. An extra unsupported opposing claim produces original disagreement without overriding identical validated-positive counts or inventing adjudication. The output explicitly states the basis of those counts.
- The nearby existing test for different evidence/IDs/rationale but the same substantive decision continues to pass.

The G4 repair validates received contexts at `tools/benchmark_v5/support.py:39-60`, applies those checks to every admitted receipt at `support.py:102-109`, and enforces a boolean checker-record domain plus exact comparison at `support.py:114-119`. Canonical serialized equality in `tools/benchmark_v5/io.py:41-43` distinguishes false,0 and0.0. The global admitted-context check at `scorer.py:56-62` runs before evaluating reviewer selections. Nearby controls confirm:

- Unused receipts with an extra context field or numeric boolean-predicate value are rejected.
- A malformed, unused, explicitly revoked receipt is still checked and rejected; revocation does not waive the admitted-record schema.
- Canonical integer-span/boolean-domain receipts remain accepted after admission. If used, the valid positive counts; if unused, it supplies no inferred reviewer claim or credit.
- Canonical comparison ignores object key order while distinguishing boolean, integer and float values.

`COMMANDS.jsonl` records the exact interpreter, argv, working directory, environment overrides, timestamps and before/after SHA for each execution. `focused_defect_tests.stderr.log` records the five selected methods; `narrow_recheck.stdout.log` and `PROBE_RESULTS.json` retain each input expectation and actual result. `recheck_probes.py` retains the executed reproduction code. Each fixture retains its source, new freeze, raw proposals, admitted receipts/check records, registry and successful result or refusal receipt. `ARTIFACT_MANIFEST.json`, `FIXTURE_MANIFEST.json` and `VERIFICATION.json` bind their hashes.

HEAD and all 12 snapshotted candidate files remained fixed; all 1,350 files in the original review directory remained byte-identical. Only this recheck directory and operator status files were written. No product edit, new worker/worktree, source/model/live call, push, production operation or Fable dispatch occurred. Coordinator owns combined regression, current-commit archive verification, the planned documentation/receipt publication child and one combined Step5/root P1 Fable handoff. Root P1 remains open pending combined acceptance; Step6 remains pending. Operator checkpoints after one completion handoff.
