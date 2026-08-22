# Scope workstations · contract and migration plan

Status: Tranche 3 complete; Tranches 4-5 pending
Operator decision: scope is a persistent workstation, not a transient filter
NETSCOUT scope choice: a fresh Pending DHS workstation now exists beside the
legacy-current All workstation; no migration, boundary approval, search,
pointer change, or report press was authorized or performed
Date: 2026-07-12

## Product contract

A workstation is the durable unit of federal capture work:

`workstation_id = exact client identity + canonical scope designator`

Examples:

- `NETSCOUT / agency_dhs`
- `NETSCOUT / agency_dod`
- `NETSCOUT / all`

Switching the top scope control switches the complete operating context:
strategy boundary, progress, evidence, approvals, opportunities, report
families, Target work, and artifact shelf. It is never a visual filter over
one mutable client-global pipeline.

## Ownership boundary

Client-global foundation:

- exact client identity and slug;
- intake submission and source scrape;
- capability profile and product identity;
- brand assets;
- the machine-inferred baseline strategy used only as a template when a new
  workstation is explicitly created.

Workstation-owned:

- selected agency scope and canonical designator;
- keywords, kept-out keywords, NAICS boundary and provenance;
- strategy decision and revision journal;
- search specifications and sweep artifacts;
- Horizon, qualification, requirement-review, and Assess artifacts;
- Assess approval and partial-release decision;
- immutable Assess CURRENT pointer;
- composition caches and report artifacts;
- proceed-to-Target approval, contacts, and target reports;
- progress state, next action, document preferences, and artifact shelf.

Nothing later than client intake may silently bleed between workstations.

## State model

The dashboard state vocabulary is:

1. `dormant` · workstation identity exists; no boundary has been accepted.
2. `configure` · keyword/NAICS boundary is pending or rejected.
3. `search` · strategy approved; no current sweep for this workstation.
4. `assess` · sweep exists; evidence/review approval is not current.
5. `produce` · Assess approval is current; no releasable current report.
6. `target` · releasable assessment exists; outreach door may be locked/open.

Intake is never a workstation stage. It is the shared client foundation.

An unused `all` workstation therefore displays `Dormant · establish boundary`,
not `Intake incomplete`.

## Canonical identity and scope

- `all` is the explicit workstation id for the all-federal scope. Existing
  unqualified report filenames remain the all-federal filename contract.
- Focused ids use the established grammar from
  `agents.review.scope_designator`, for example `agency_dhs` and
  `agency_dhs_cisa`.
- A workstation stores its canonical scope object. Code never reconstructs a
  scope by parsing a display label.
- Multi-agency order is canonicalized before identity is minted so equivalent
  selections cannot create duplicate workstations.
- The displayed label is derived (`All Federal`, `DHS`, `DHS + CISA`) and is
  not an identity input.

## Storage contract

### Registry

`data/review/<slug>.workstations.json`

Versioned, atomic, and journaled. Minimal shape:

```json
{
  "schema_version": "1",
  "client_name": "NETSCOUT",
  "workstations": [
    {
      "id": "agency_dhs",
      "scope": {"agencies": ["DHS"]},
      "created_at": "...",
      "created_from": "legacy-current",
      "archived_at": null
    },
    {
      "id": "all",
      "scope": {"all": true},
      "created_at": "...",
      "created_from": "operator",
      "archived_at": null
    }
  ]
}
```

The registry contains identity only. It does not carry progress, approval, or
release verdicts; those are derived from authoritative artifacts.

### Strategy packet

New workstation-native packets:

- `data/review/<slug>.<workstation_id>.review.json`
- matching `.review.md` and `.journal.jsonl`

The legacy `data/review/<slug>.review.json` remains the client-foundation and
migration source until every caller has cut over. It is never mutated merely
because the operator switches workstations.

`load_packet`, `revise`, `amend_terms`, `decide`, and `set_scope` gain an
explicit workstation/designator parameter through one path owner; callers may
not temporarily rewrite the legacy packet's `search_scope` to simulate a
switch.

### Approval/review artifacts

Every mutable approval or review artifact that can differ by scope gains the
same designator segment, including:

- Assess approval;
- target approval;
- Horizon review;
- qualification;
- live requirement decisions;
- partnering/flag decisions where scope-bound;
- contact plans and target reports.

Legacy unqualified artifacts are eligible only through the migration adapter
for the one legacy-current workstation whose exact binding they already carry.
No fallback may make one approval satisfy a different workstation.

### Existing scope-aware families

These already carry, or validate against, the designator and should be reused:

- `searches_<slug>.<designator>.json`;
- report/QA/arbiter/internal/PDF families;
- immutable Assess pointers by designator;
- compose-cache keys;
- agency composition cache and scoped Desktop naming.

## API contract

Additive endpoints:

- `GET /api/client/<slug>/workstations`
- `POST /api/client/<slug>/workstations` · explicit create only
- `GET /api/client/<slug>/workstation/<id>`

Existing mutation endpoints gain a required `workstation_id` after the UI
cutover. During compatibility migration, omission resolves only when exactly
one unambiguous legacy-current workstation exists; otherwise it is a 409, never
"use whichever scope is in the packet now."

`POST /api/run` keeps its established outer shape but accepts additive
`args.workstation_id`. The server resolves the immutable scope from the
registry and rejects an args/scope mismatch.

## URL and UI contract

Canonical deep link:

`/client/<slug>/workstation/<workstation_id>`

The top-of-client segmented switcher is persistent and dominant. Each entry
shows scope label plus derived stage, for example:

- `DHS · Produce`
- `DoD · Assess`
- `All Federal · Dormant`

Switching workstations:

- updates the URL and browser history;
- reloads one complete workstation payload;
- changes every progress stage, artifact, target, and next action below;
- never persists a new operator decision;
- remembers the last-opened workstation as UI preference only.

`+ New scope` is the sole creation door. Creation presents All Federal or a
validated agency selection and may explicitly clone the client baseline
strategy. It never clones human approvals, evidence, or Target decisions.

Client-global foundation documents ultimately appear in a separate small area
and are not duplicated into each workstation shelf. That small in-workstation
area is deferred with the workstation-native strategy cutover in Tranche 3.
During the Tranche 1/2 compatibility slice, exact workstation shelves omit
unqualified foundation and Desktop files; those files remain available from
the client-global home depository and are never relabeled as scope-owned.

## Progress and concurrency

- Progress is derived per workstation from its exact artifacts.
- Job records include `workstation_id`, and logs display it.
- Initial implementation retains the conservative one-running-job-per-client
  lock. The UI may show jobs in their originating workstation, but parallel
  same-client execution is a later decision after every mutable artifact is
  partitioned.
- A job started in one workstation remains visible there if the operator
  switches away; it cannot move the active selection or write another
  workstation's state.

## Artifact shelf

`client_documents` becomes workstation-aware:

- focused shelves admit only the exact designator family;
- `all` admits only unqualified all-federal report families;
- scope-bound working intelligence and Target artifacts follow the same rule;
- client-foundation documents are returned separately;
- Desktop files with no machine-verifiable workstation identity remain in the
  client-global area unless the operator explicitly assigns them.

No filename heuristic may override a current release or approval verdict.

## NETSCOUT migration

Preflight correction (2026-07-13): the runtime no longer matches the earlier
"current DHS" premise. The exact legacy packet currently names `all`; the
Assess approval also names `all`, while the unqualified designated sweep
embeds a DHS focus and does not match that workstation. A separate designated
DHS sweep/report family exists, but filenames are history, not operator
authority. No workstation registry exists.

Therefore the read-only tranches must show the exact current All Federal
workstation at `search` with downstream state held, and must not mint or label
DHS current from artifact names. The product-default All identity is a derived
navigation possibility, not a new persisted decision.

Migration remains additive and idempotent, but is deferred until the operator
explicitly chooses whether the accepted working boundary should be All Federal
or DHS:

William selected DHS on 2026-07-13. Tranche 3 created a fresh, Pending
`agency_dhs` workstation from the client baseline at
`2026-07-13T18:53:24.054200Z`. That creation is not the migration described
below: legacy All Federal remains independently current until the Tranche 5
dry run and explicit cutover are complete.

1. Read the legacy packet and resolve its exact current scope; never override
   it from a sweep, pointer, cache, report, or Desktop filename.
2. Require the designated sweep and every mutable approval proposed for
   adoption to prove that exact client and scope. Any mismatch is a migration
   hold, not a repair heuristic.
3. After the operator's explicit scope choice, copy the strategy packet,
   markdown, and journal to the matching workstation-native family without
   changing semantic contents.
4. Associate already-designated sweeps, pointers, caches, and reports only by
   exact embedded identity; do not rewrite immutable artifacts.
5. Move/copy only mutable approval artifacts whose complete binding proves the
   selected workstation; anything ambiguous stays legacy and blocks migration.
6. Other identities begin `dormant` unless the operator explicitly creates and
   accepts their boundary. Historical artifacts never imply progress.
7. Record a migration receipt with hashes. Re-running produces no changes.

Migration never changes a pointer, approval decision, release verdict, or
report bytes.

## Implementation tranches

### Tranche 1 · read model, no mutation

- Registry model/path helpers and derived legacy catalog.
- Workstation state resolver from exact artifacts.
- Additive read endpoints.
- Tests proving no artifact, gate, or pointer write.

### Tranche 2 · top switcher and deep links

- Top segmented control and dormant/configure displays.
- Entire page consumes one workstation payload.
- Scope switching is read-only.
- Desktop and phone computed-layout tests.

### Tranche 3 · explicit creation + strategy partition

- Journaled creation endpoint.
- Workstation-native strategy packets.
- Keyword and NAICS workshops operate on the selected packet.
- Search runs bind exact registry scope.
- Add the separate client-global foundation-document area inside the
  workstation view without duplicating those files into a scope shelf.

Implementation note (2026-07-13): creation and alternate-scope operation
passed the complete non-browser suite and independent adversarial review.
Creating an alternate id must coexist with the legacy-current id. Creating the
same id as the legacy-current packet is a byte-inert duplicate, never an
implicit migration. Native search may write its exact sweep and research
picture, but shared Assess, Target, contact, outreach, forecast-history, and
release surfaces remain closed until their owning tranches.

### Tranche 4 · approval and Target partition

- Scope-specific Assess/review decision paths.
- Scope-specific proceed-to-Target and outreach artifacts.
- Compatibility fallback removed once migration proves complete.

### Tranche 5 · migration and cutover

- Dry-run migration report.
- Operator-confirmed NETSCOUT migration for the scope William selects, All
  Federal or DHS.
- Cutover tests across the selected current scope, DHS, DoD, and a dormant
  alternate scope.
- Remove UI reliance on the mutable legacy `search_scope` selector.

## Acceptance tests

- Switching DHS -> All -> DHS changes no bytes on disk.
- DHS and DoD may carry different keyword/NAICS boundaries and approval states.
- A DHS approval cannot satisfy DoD or All Federal.
- A DHS report never appears on the All Federal shelf.
- Dormant All Federal shows no inherited progress after an explicit DHS
  migration, or when All exists without an accepted boundary. Merely creating
  an alternate DHS workstation in Tranche 3 does not retire an independently
  current legacy All workstation.
- Creating a workstation clones no approval or evidence.
- A run launched from DHS is bound to `agency_dhs` even if the UI switches to
  DoD while it is running.
- Direct URLs restore the exact workstation after restart.
- Every existing NETSCOUT pointer, approval, sweep, and report byte remains
  unchanged by migration, whether William selects All Federal or DHS.
- Legacy single-workstation clients remain operable during the compatibility
  window.
- Full test suite and release/lint gates remain green.

## Explicit non-goals

- No aggregation of scope workstations into an All Federal report.
- No automatic creation of every possible agency workstation.
- No cross-workstation approval inheritance.
- No concurrent same-client jobs in the first release.
- No report press, pointer activation/removal, or operator gate decision as
  part of implementation or migration testing.
