# Command Center redesign handoff

## Delivery boundary

- Branch: `codex/command-center-redesign-20260807`
- Worktree: `/Users/wtjohnson/Documents/Codex/2026-08-07/we/work/lila-command-center-redesign`
- Starting implementation: `be13dda`
- Merge state: intentionally isolated and unmerged
- Preview: `LILA_UI_PORT=8392 python3 ui/server.py`

## Outcome

The real Command Center now preserves the Lovable Report Press shell while
implementing Select Client → Analyst Layer → Assessment Review → Targeting
Review → Press. Active client, approved scope, persisted registered edition,
stage, five readiness components, targeting gaps, and one next action are
visible without leaving the home surface.

Target Lists is an operational workspace. Every promoted play is reviewed
across six routes, every gap is assigned and time-bounded, and every target can
be converted into a source-bound account action. Target unlock no longer means
Targeting Review complete.

## Authoritative boundaries

- Command Center reads existing depository, calendar, target, ticker, client,
  and target inventory endpoints.
- Its only mutations are the new server-owned Targeting Plan and Targeting
  Review receipt endpoints.
- Assess and Target unlock decisions remain in the established client workspace.
- The first Press is authorized by current Assessment, Evidence, Targeting, and
  Assets. Output stays Pending until QA passes; release requires all five.
- `/api/run`, HTML/PDF previews, all attachment routes, and PDF export fail
  closed on current Targeting Review readiness.

## Verification

- Focused: `173 passed` across Command Center, Target gate, UI, dashboard
  downloads, and release-state tests.
- Full: `3071 passed, 12 failed, 3 skipped` in 74.95 seconds.
- The 12 failures reproduce the earlier baseline areas: Assess refresh/live
  report date fixtures, composer sidecar fixtures, the legacy `clogo-row`
  identity assertion, and radar date state.
- Browser: 1440, 1024, 768, and 390; mobile nav open; Select; Analyst; Review;
  Assessment; Targeting; target evidence/action; Press; search; server error;
  and no-qualified-target states.
- Keyboard: search Escape, modal drawer loop, mobile Tab/Shift-Tab loop, Escape
  close, and focus restoration verified.
- Final independent verdicts: no Critical or High blockers.

## Review order

1. `agents/review.py`
2. `ui/server.py`
3. `ui/static/command-center-home.js`
4. `ui/static/command-center-home.css`
5. targeting and Command Center tests
6. fidelity, targeting, critic, and defect-ledger docs

Do not remove the distinction between generation authorization and release
readiness. Do not infer Targeting readiness from target count or Target unlock.
Do not weaken the `/report` gate; it closes a direct save/print/PDF bypass.
