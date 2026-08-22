# Tranche handoff: scope workstation read model and top switcher

**Base:** `3d4d934e68e7bcc15a88bf1809a5c838fc3cb9f3`

**Disposition:** **COMPLETE / READY FOR WILLIAM.** The read-only compatibility
slice, exact launch binding, auxiliary isolation, and browser switcher are
implemented and independently adversarially reviewed.

**Verified implementation commits:**

- `f7bd598` · `[ISOLATED CONTRACT] bind scope workstation compatibility`
- `86e934b` · `Add scope workstation switcher and browser isolation`

**Mode:** additive read model, GET API, browser navigation, and fail-closed
binding on the existing run door. No gate decision, scope write, registry
creation, search execution, pointer action, release action, report press,
metered call, or Desktop delivery occurred.

## Result

The Command Center now treats scope as a complete operating workstation rather
than a visual filter. The top segmented rail switches one exact client/scope
context, updates the canonical URL and browser history, and replaces the whole
operating body and sidebar.

This is deliberately the read-only compatibility slice:

- one exact legacy-current workstation may use the existing pipeline payload;
- registry/product-default noncurrent identities render as physically separate
  dormant workstations;
- dormant payloads contain no strategy, evidence, approval, opportunity,
  report, release, Target, or sidebar-target state;
- the mutable legacy scope selector is read-only under a workstation URL; and
- explicit creation and workstation-native strategy packets remain Tranche 3.

The separate client-global foundation-document area is also deferred to
Tranche 3. During this slice, unqualified foundation and Desktop documents stay
in the home depository and are omitted from exact workstation shelves rather
than being relabeled as scope-owned.

## Adversarial repairs made before exposure

- Replaced mutable dictionary scope with a tuple-backed frozen typed model.
- Registry parsing no longer invents missing ids/scopes, repairs `{}` into All,
  accepts fuzzy agency prefixes, or trusts a false display label.
- Multi-agency identity follows stable agency-catalog order, preserving the
  established `agency_dhs_cisa` family regardless of input order.
- Legacy packets require an exact nonblank client identity.
- Current sweep progress requires a regular readable JSON artifact whose exact
  client and embedded `search_scope` match the workstation.
- A saved keyword/NAICS refinement now invalidates an older same-scope sweep.
  New runs stamp `strategy_revision`; legacy post-revision evidence must prove
  an aware `generated_at` at or after the packet's `revised_at`.
- Dormant history is separate from active `sweep_exists`; old files never
  advance dormant progress.
- The six phases now resolve through dormant, configure, search, assess,
  produce, and target using the existing approval/release owners.
- Exact report-family filtering prevents All from admitting `agency_*` files
  and prevents DHS from admitting the near-prefix DHS + CISA family.
- Dashboard document assembly disables unresolved-entity telemetry, making a
  core detail GET observational.
- A monotonic navigation sequence prevents a late detail response from painting
  a newer workstation selection.
- Packet status is rebound with scope before selecting the early/full adapter;
  the reproduced pending-after-Target race now returns 409.
- `/api/run` rejects dormant/stale ids, resolves omission only to the sole
  legacy-current row, and passes exact client/id expectations into the child.
  Search rechecks revision and scope immediately before source fan-out.
- Finale jobs capture their origin and cannot refresh or relabel a later view.
- Competitor/recompete overlays close on navigation and late responses cannot
  repaint them. Contacts has a canonical URL and the same last-intent guard.

## Auxiliary GET purity repair

The first browser pass exposed navigation-time writes and unqualified reads
outside the new detail endpoint. The completed repair is:

- `ui/server.py` makes the banner GET observational;
- `tests/test_dashboard_views.py` pins no mark adoption and no views job;
- every full-view auxiliary URL carries `workstation_id`, and the server
  rebinds the exact current packet/sweep before serving it;
- change digest scope cursors must match; the legacy client-only recompete
  calendar is withheld until its schema gains a scope field;
- `tools/contact_graph/outreach.py` preserves malformed operator bytes and
  raises an explicit unavailable state; and
- the automatic auxiliary GET graph is executed under a byte-tree invariant.

Brand-mark adoption is now an explicit operator workflow:
`python3 tools/brand_marks.py --adopt`. Opening a client never adopts a mark.
The Target/outreach rail reports unavailable on corrupt curated JSON rather
than presenting an indistinguishable empty list.

## Live NETSCOUT truth

The earlier migration premise that NETSCOUT is currently DHS is stale. The
exact current review packet names All Federal, while the unqualified designated
sweep embeds DHS. The Assess approval also names All but is no longer sufficient
to make that mismatched sweep exact. A separate DHS artifact family exists, but
filenames cannot override the operator's scope and no workstation registry
exists.

The implemented behavior therefore shows one current **All Federal · Search**
workstation with a scope-proof hold. The downstream legacy body is physically
withheld: no old approvals, opportunities, reports, Produce controls, sales
link, or Target state appear. The visible recovery control is now server- and
child-bound to exact All Federal. It was not pressed during implementation.

DHS is not invented as a selectable workstation. Making DHS current or adding
it as a durable second workstation requires the explicit creation/migration
door and William's scope choice in the next tranche.

## Browser proof

An isolated three-workstation fixture (DHS current, DoD dormant, All dormant)
proved:

- DHS -> All -> DHS replaces the complete payload and sidebar;
- dormant All contains no DHS target sentinel, banner, finale, report, release,
  or Target rail;
- native-link keyboard activation selects the exact workstation;
- Back, Forward, direct URL load, and reload restore the exact id;
- a deliberately delayed old detail response cannot repaint the new view;
- every automatic full-view auxiliary request carries the exact id;
- delayed competitor and Contacts responses cannot repaint a later view;
- all three finale jobs post the captured origin id and never refresh the
  workstation selected afterward;
- switching requests are GET-only; and
- dormant, held, early, and full modes have zero document/switcher overflow at
  320px and 390px.

The live NETSCOUT browser proof separately confirmed the held All Federal view
contains only the client foundation, exact diagnostic, recovery action, and
six-stage ladder.

## Verification status

Final verification after every repair:

- complete suite: **1097 passed / 1 skipped**;
- real-Chrome workstation suite: **18 passed**;
- qualified browser graph plus server-executed byte-invariant auxiliary graph:
  **19 passed**;
- exact scope/status/sweep CAS, stale boundary, run binding, child binding,
  auxiliary withholding, and legacy compatibility fixtures: green;
- targeted and staged `git diff --check`: clean; and
- no new output-reachable em dash.

The browser half captures every automatic auxiliary URL and origin race. The
server half executes those same qualified banner, competitor, recompete,
change-digest, and Target paths under a complete byte-tree comparison. The
live NETSCOUT held view was then rechecked on the updated server.

## Handoff

The compatibility HOLD is closed. Tranche 3 is the explicit `+ New scope` door,
workstation-native keyword/NAICS strategy packets, and the separate shared
foundation-document area. It must preserve the exact dormant whitelist,
require a workstation id on mutations, clone no approval/evidence/Target
state, and must not migrate NETSCOUT until William chooses All Federal versus
DHS.

Pre-existing unrelated dirty files were preserved and excluded from this
tranche: `data/entities/unresolved.log` and
`docs/reviews/cycle5-followon-solo-range.md`.

The auxiliary-purity repair files are related tranche work, not pre-existing
noise: `tests/test_dashboard_views.py`, `tests/test_outreach.py`, and
`tools/contact_graph/outreach.py`.
