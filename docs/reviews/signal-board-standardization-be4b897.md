# Signal Board standardization · review request

Submitted by: Claude (Opus 4.8)
Reviewer: Codex
Date: 2026-07-15
Branch: `rename/opportunity-assessment`
Binding base: `bda8057` (docs: LOCK the Federal Opportunity Signals format)
Binding endpoint: `be4b897` (feat(reports): deterministic Signal Board renderer)

## What this change is

Standardizes LILA's client deliverable on the operator-**locked** Federal
Opportunity Signals format (`docs/reference/federal_opportunity_signals.reference.html`,
spec `docs/opportunity_signals_format.md`). The operator's directive: "this is
how every report must look, down to every detail," and reports are "all signal,
no noise — the operator does the talking" (no LLM voice).

Approach: the locked reference is tokenized into a fixed-shell **template**
(CSS, structure, labels, scale-boundary notes preserved exactly; only per-report
data regions become `{{TOKENS}}`). A **deterministic renderer** fills only those
slots from pipeline data. No model composes the artifact, so there is no LLM
voice and the output is byte-identical in format on every run. Drift is blocked
by a **conformance lint** plus a **golden test**.

## Files (3, +856 lines, isolated — does not touch `capture_brief.py`)

- `agents/reports/templates/signal_board.template.html` — the tokenized locked
  format (fixed shell + `{{BEST_FIT}}`, `{{COMPETITORS}}`, `{{TEAMING}}`,
  `{{HORIZON}}`, `{{SIGNAL_CARDS}}`, `{{CHIPS}}`, `{{NEWS}}`, `{{SCALE_WORK}}`,
  `{{SCALE_TOTAL}}`, `{{REPORT_DATE}}`).
- `agents/reports/signal_board.py` — band builders reproducing the exact `sb-*`
  markup, `render_signal_board(model)` (token fill), `build_model(...)` adapter
  (assessment draft + contact plan -> model), and `lint_signal_board(html)`.
- `tests/test_signal_board.py` — golden conformance test.

## Verification (no gate/press/pointer/metered action taken)

- Golden test: **4 passed**.
- Full suite at `be4b897`: **1219 passed, 1 skipped** (82s).
- End-to-end on Insignary's real artifacts: renders 7 opportunity windows, 14
  teaming partners, 4 signal cards, 35 links; deterministic (same model ->
  byte-identical html); zero unfilled tokens; lint-clean.
- Lint has teeth (pinned by the golden test): an unfilled token, a missing
  "no affiliation or endorsement" disclaimer, or a missing "not pipeline"
  boundary qualifier each fail.

## Please review

1. **Tokenizer soundness.** The template was produced by a depth-aware
   inner-content replacement over the locked reference (see the scratch
   generator; the template is committed, the generator is not). Confirm no
   fixed structure/label/CSS was lost and that the tokenized regions are the
   right data boundaries.
2. **Lint completeness.** `lint_signal_board` enforces band presence + body
   order, filled tokens, the identification-only disclaimer, the scale boundary
   qualifier, and a link floor. Is this the right conformance contract, or is it
   too weak/strong? Should it move into the `lint.py` battery and gate the
   Produce path?
3. **Adapter fidelity (v1, the known gap).** `build_model` currently fills
   opportunity/teaming/figure bands from `draft.json` + `contacts.json`.
   Competitors, horizon, POCs, evidence dock, the full 4-cell opp detail, chips,
   news, and the expandable scale-math are **not yet wired** (their template
   tokens for POCs/evidence/sequence are also not yet cut). Review the model
   contract and advise on the cleanest mapping from `AssessmentDocument` /
   FactPack so the renderer never invents signal for an empty band.
4. **Where it plugs in.** Recommendation for wiring this as the Produce/report
   output (retiring the prose `capture_brief` composer as the *client*
   deliverable) without disturbing your in-flight opportunity-assessment
   doctrine work on `capture_brief.py`.
5. **Branding resolution.** The template still carries 6 base64 assets (client +
   agency marks) from the reference. For a generic renderer these should resolve
   per report from `tools/brand_marks` + an agency-seal library. Advise on the
   seal source.

## Notes

- Standing rules observed: no report press, search, pointer, gate, or metered
  call. Command-Center-only untouched.
- Pre-existing worktree modifications (`capture_brief.py` doctrine WIP,
  `data/entities/unresolved.log`, view-test edits) are outside this change and
  were not committed here.
