# Command Center Lovable fidelity contract

## Binding visual system

The production home surface preserves the later Report Press Lovable system:
white top bar and navigation, GTM lockup, Manrope typography, violet account
hero with orbital imagery, coral primary action, Capitol decision vignette,
four-stage rail, quiet bordered cards, right-side readiness rail, and full-height
workflow studios. The implementation reuses the prototype's
`account-orbits.png` and the existing production Capitol asset locally.

## Surface contract

- Top bar: brand, Search, Calendar, operator identity, and a mobile menu.
- Sidebar: all nine specified destinations open real production-backed workspaces.
- Context: selected client, approved scope, persisted active edition, and current stage.
- Rail: Select Client, Analyst Layer, Review, Press. Assessment Review and
  Targeting Review remain distinct mandatory work products inside Review.
- Home: one next action, time/owner context, book-of-business queue, five
  unaggregated readiness components, and targeting signal.
- Studios: right-side modal drawers preserve parent context and trap focus.
- States: loading, empty, partial evidence, no qualified target, server refusal,
  pending first Press, and hard-blocked release are all explicit.

## Material changes and evidence

| Observed failure | Root correction | Lovable characteristic preserved | Regression evidence |
|---|---|---|---|
| Press appeared available before mandatory targeting | Separate Assessment and Targeting work products plus server-owned targeting receipt | Four-stage rail and studio pattern | `test_press_readiness_is_explicit_and_fail_closed` |
| Target Lists was read-only and generic | Play-specific six-lane planner and complete account-action record | Calm cards, compact rails, progressive disclosure | targeting contract tests and screenshots 39–41 |
| Polished report preview bypassed Targeting Review | Canonical-client gate on HTML/PDF preview and every attachment boundary | Existing Report Library interaction | `test_report_preview_fails_closed_without_current_targeting_review` |
| First Press required the output it was meant to generate | Split `canPress` from `releaseReady`; Output becomes Pending until QA | Five-component readiness treatment | static Press contract test |
| Mobile drawer allowed focus to escape | Named modal navigation, inert background, bidirectional focus loop | Lovable off-canvas navigation | `test_mobile_navigation_is_modal_and_traps_keyboard_focus` plus browser loop check |
| Active edition was implicitly `documents[0]` | Validated per-client edition picker and local persistence | Select Client studio and context strip | `test_active_edition_persists_per_client_and_drives_report_preview` |

## Responsive contract

- 1440: full sidebar, context row, hero, four-stage rail, two-column work area.
- 1024: icon rail retains accessible names; core hierarchy remains visible.
- 768: mobile navigation trigger, full-width primary flow, no horizontal overflow.
- 390: stacked context and hero, compact stage rail, usable drawers and forms.
- Reduced motion is honored; focus-visible treatment is present for all overlay controls.

## Reference evidence

Primary Lovable comparison: `qa/sidebar-revision-01-command-center.png`,
`qa/workflow-review-hub-final.png`, `qa/workflow-review-targets.png`,
`qa/prototype-report-press.png`, and `qa/prototype-mobile-pass2.png` in the
prototype repository. Final production captures are recorded in the gauntlet
handoff and defect ledger.
