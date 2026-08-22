# NAICS Boundary Workshop repair split

**Base:** `22a72846e85357756836b5fa24d4c3c3629d3a86`

**Review:** `docs/reviews/naics-boundary-workshop-22a7284.md`

**Disposition:** HOLD until both isolated tranches are repaired and independently
reviewed. No gate, pointer, approval, scope, report press, release, or metered
surface is in scope.

This is the binding no-overlap tasking for the follow-on repair. The seam is
intentional: Codex owns persisted truth and Claude owns browser behavior.

## Tranche A - Codex - backend contract truth

### File ownership

- `agents/decisions/schemas.py`
- `agents/review.py`
- `tests/test_naics_contract_repairs.py` (new)
- `docs/CONVENTIONS.md` (backend doctrine names and invariants only)
- `ui/server.py` (audit follow-up only: distinguish omitted NAICS fields from
  explicit null at `/api/strategy/terms`)

Do not edit `ui/index.html` or `tests/test_naics_workshop.py` in this tranche.

### Required outcomes

1. Reconcile NAICS-bearing search specs by stable source identity so an
   empty-boundary save followed by a restore repopulates both SAM.gov and
   USAspending. A web spec must not persist NAICS, even if legacy data carried
   a nonempty list.
2. Validate every NAICS record, not only `inferred_naics`: exact six-digit
   code, allowed role, allowed origin, and dictionary/list payload shape.
   Invalid input must fail with `RevisionError` and must not increment or
   persist a revision.
3. Make one persistence owner reconcile active and kept-out state against the
   prior packet. The two lanes must be disjoint. Inferred-only move-out must
   preserve prior metadata in kept-out; inferred-only restore must recover it.
   A transition through the API must not destructively erase a prior decision.
4. Journal complete before/after NAICS decision state, including
   `naics_meta` and `kept_out_naics`, in both pre-approval revision and
   post-approval amendment paths.
5. Preserve additive legacy loading and the single approval leg.

### Verification bar

- New regressions prove empty-to-restored reconciliation, web cleanup,
  disjointness, lossless move/restore, complete journaling, invalid code,
  invalid role/origin, malformed shapes, and no persistence on failure.
- Existing `tests/test_naics_workshop.py` remains green unchanged.
- Full suite is green. Commit only the four owned files in one isolated
  Codex repair commit.

## Tranche B - Claude - workshop interaction and presentation

### File ownership

- `ui/index.html`
- `tests/test_naics_workshop_ui.py` (new)

Do not edit `agents/decisions/schemas.py`, `agents/review.py`,
`tests/test_naics_contract_repairs.py`, or the existing
`tests/test_naics_workshop.py` in this tranche. If a server change appears
necessary, stop and hand it back across the seam rather than editing it.

### Required outcomes

1. `Inspect first` selects a read-only candidate for the inspector and does
   not add it to the authoritative boundary. Only `Use in boundary` promotes.
2. Remove nested interactive semantics. Mouse and Enter/Space must each
   inspect and remove exactly as labeled, including on a real keyboard path.
3. At 320px and 390px there is no horizontal document or workshop overflow.
   Use a one-column phone layout unless a compact alternative is proven.
4. Restore and manual re-add of a kept-out code must reuse the preserved
   record rather than replacing title, role, rationale, origin, or note.
   Restoration is an event; it must not overwrite original provenance.
5. Inspector title, role, and note edits must mark provenance accurately and
   must not destroy focus while tabbing through the form.
6. `Approve search vocabulary & review NAICS` must move programmatic focus to
   a stable NAICS heading/region after the save and re-render, not only scroll.
7. Undo expiry must redraw or disable/remove the control; a stale Undo click
   must never dereference null.
8. Correct the downstream-impact copy: persisted web `SearchSpec.naics_codes`
   is empty, but the current web execution prompt receives authoritative NAICS.
   Do not claim the whole web lane is untouched.

### Verification bar

- Behavioral tests exercise real state transitions, keyboard actions, focus,
  expiry, and rendered 320px/390px overflow. Source-string assertions alone do
  not satisfy this tranche.
- Demonstrate live NETSCOUT proof without saving: Inspect first leaves five
  boundary cards; keyboard Remove moves one card out; phone widths do not
  overflow.
- Full suite is green. Commit only the two owned files in one isolated Claude
  repair commit.

## Merge and seal protocol

1. Each builder mechanically verifies the base and owned-file diff before
   committing.
2. Each commit must be independently reviewed by the other builder.
3. Do not lift HOLD until both reviews pass and the combined full suite is
   green.
4. Do not press a report or change any operator decision as part of repair or
   verification.
