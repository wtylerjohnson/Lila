# Candidate Review v1 baseline record

Recorded: 2026-07-22

## Isolation and rollback

- Source checkout: `/Users/wtjohnson/lila-codex-source-mesh`
- Isolated worktree: `/Users/wtjohnson/Documents/Codex/2026-07-21/i/worktrees/lila-candidate-review-v1`
- Integration branch: `codex/candidate-review-v1`
- Starting commit and rollback target: `1299e6920a660fe928e9fc824c12a9ac5be86629`
- Source branch: `codex/federal-source-mesh`
- Tracked baseline patch SHA-256: `9390842836f9b3f3cadb5499e9845fdfbb6a75cd8061d73b0ad4c29b180f19f7`

The isolated worktree was populated with the source checkout's 15 modified
tracked files and two untracked iMerit files. Every copied file matched the
source byte-for-byte before Candidate Review v1 changes began. The source
checkout remained unchanged. The isolated worktree intentionally does not
copy the source checkout's ignored `.env` symlink.

## Preserved source changes

Modified tracked files:

- `agents/reports/board_content.py`
- `agents/reports/composer.py`
- `agents/reports/signal_board.py`
- `agents/reports/templates/signal_board.template.html`
- `clients/mark43/compose_trail.md`
- `clients/mark43/signal_board_content.json`
- `clients/riverbed/compose_trail.md`
- `clients/riverbed/signal_board_content.json`
- `data/entities/unresolved.log`
- `run_compose.py`
- `run_signal_board.py`
- `tests/test_composer.py`
- `tests/test_incumbent_buyers.py`
- `tests/test_signal_board.py`
- `tools/api/incumbent_buyers.py`

Untracked files:

- `clients/imerit/compose_trail.md`
- `clients/imerit/signal_board_content.json`

## Frozen reference artifacts

| Client | Reference artifact | SHA-256 |
|---|---|---|
| Mark43 | `Mark43_Federal_Opportunity_Assessment_CANDIDATE_REVIEW_EDITABLE_2026-07-21.html` | `f774609b0b7cfcc119d435b45355c21806fc466c2d7fb81d72362f8319fc5c33` |
| iMerit | `iMerit_Federal_Opportunity_Assessment_CANDIDATE_REVIEW_EDITABLE_2026-07-22.html` | `6c5583ffc4396b719fea2d3557b3e27a2c8f2cb2c3d9718ae39996f92b50ffe9` |
| Riverbed | `Riverbed_Federal_Opportunity_Assessment_CANDIDATE_REVIEW_EDITABLE_2026-07-22.html` | `6af1535177ec7c48f56813813bc4b7dddcba09e7627fa593650b68fdd7c565ff` |

Riverbed is the interaction and visual superset. These hashes identify the
locked inputs; the reference files are not copied into the repository.

## Baseline verification

Verified test runtime:

`/Users/wtjohnson/federal-sales-os/.venv/bin/python`

Focused suite:

```text
tests/test_arbiters.py
tests/test_assessment_arbiters.py
tests/test_intake.py
tests/test_recompete_context.py
tests/test_assessment_chain.py
tests/test_signal_board.py
tests/test_refresh_press.py
tests/test_ui.py
```

Result: **281 passed in 2.28 seconds** with bytecode and pytest cache writes
disabled. No pre-existing focused failure was observed.

Additional baseline smoke:

`tests/test_incumbent_buyers.py::test_riverbed_taxonomy_v2_aligns_collision_kills_with_profile`

Result: **1 passed in 0.25 seconds**.

## Safety rule

All Candidate Review v1 implementation occurs on
`codex/candidate-review-v1`. Do not reset, clean, overwrite, or use the dirty
source checkout as an implementation target. The starting commit plus the
recorded preserved patch is the rollback boundary.
