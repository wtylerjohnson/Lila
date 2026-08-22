# Tranche handoff: scope-workstation creation and native strategy operation

**Base:** `eb2b14a`

**Disposition:** **PASS.** Tranche 3 creation, native strategy, exact search
binding, and shared foundation surfaces are committed and independently
reviewed. The authorized NETSCOUT DHS workstation exists as a fresh Pending
packet; legacy All remains independently current.

**Mode:** additive creation and alternate-scope operation. No migration or
cutover is part of this tranche. The exact legacy-current workstation remains
current beside any alternate native workstation.

## Review lanes

- Claude UI lane: **PASS** on base `eb2b14a`; Codex's reviewed repair layer is
  `9da821e`.
- Independent creation audit: **PASS** after same-id, crash-replay, sidecar,
  and NAICS-provenance repairs.
- Independent native binding audit: **PASS** after owner-generation CAS,
  forced owner mode, pre-fan-out locking, and shared-state isolation repairs.
- Independent UI/contract audit: **PASS** after modal, sanitizer, review-only,
  native packet-read, low-level job, and approval-instruction repairs.

## Implemented result

- `+ New scope` is an explicit POST beside the GET-only workstation switcher.
  The public request requires the client baseline strategy and never offers an
  unusable no-clone branch.
- Alternate native workstations coexist with the legacy-current workstation.
  Selecting the exact legacy-current scope is a byte-inert duplicate, not an
  implicit migration or cutover.
- Creation is receipt-first and registry-last, with exact baseline packet CAS,
  deterministic hard-stop recovery, and no rollback of pre-existing bytes.
- The clone carries the full baseline strategy into a fresh Pending,
  revision-zero packet. It carries no decision, approval, evidence, report,
  release, pointer, or Target state.
- Keyword and NAICS work on the selected native packet. Native mutations use
  exact packet, registry, and receipt generations and return a new packet SHA
  after each accepted write.
- Native execution is limited to the exact scoped search. The child revalidates
  identity, ownership, packet, revision, and scope at the last locked
  pre-fan-out point, and client-global side effects stay suppressed.
- Shared client foundation is physically separate from scope-owned artifacts,
  served from a strict allowlist, and rendered through safe image,
  Markdown, JSON, and text paths.

## Independent audit findings and repairs

### Creation and coexistence

- **Finding:** creating the same id as the legacy-current packet could act as
  an accidental future cutover. **Repair:** a clean same-id request now returns
  `created: false` without writes; an orphan native prefix fails closed for
  explicit migration; an already-complete historical native owner remains
  observationally replayable.
- **Finding:** completed replay proved only receipt/packet state. **Repair:**
  replay also validates the exact receipt-bound first creation-journal event
  and required nonempty review Markdown without rebuilding either.
- **Finding:** a public no-clone choice created a dormant dead end. **Repair:**
  the public endpoint now requires `clone_baseline: true`; the lower-level
  no-clone primitive remains non-public and explicitly receipted.
- **Finding:** the creation disclosure understated the clone as keywords and
  NAICS only. **Repair:** UI and contract language now state that the full
  baseline strategy is copied while downstream decisions and artifacts are
  not.

### Baseline NAICS provenance

- **Finding:** a near-miss reason could be promoted into an affirmative
  explanation for an in-boundary code. **Repair:** near-miss context remains
  unchanged in its review lane and an unsupported affirmative rationale stays
  blank for operator judgment.
- **Finding:** partial `naics_meta` hydration could rewrite existing provenance
  or behave all-or-nothing. **Repair:** existing records and order remain exact;
  only missing inferred codes are appended in boundary order, and only an exact
  NAICS keyword rationale may explain them.

### Native mutation and run binding

- **Finding:** packet identity alone did not close ownership replacement and
  pre-spend mutation windows. **Repair:** native mutations and runs carry
  registry, receipt, packet, revision, client, workstation, and scope
  generations; the final search check shares locks with packet writers and
  submits the first source tasks before releasing them.
- **Finding:** a native search could update client-global state owned by later
  tranches. **Repair:** forecast history/store and coverage writes, generic
  attempt coverage, contact harvest, Assess pointer refresh, and Outreach
  growth are suppressed; the admitted workstation output families are the
  scoped sweep, research picture, and scoped snapshot.

### Browser truthfulness and foundation safety

- **Finding:** users could dismiss or navigate away from a creation dialog
  after its write was already in flight. **Repair:** in-flight creation locks
  every close path; dialog focus/inert behavior and late-response guards keep
  the visible state truthful.
- **Finding:** native Assess could imply another paid search instead of Review.
  **Repair:** the post-search native action is Review; no metered sweep is
  triggered by navigation or phase rendering.
- **Finding:** shared Markdown and broad file discovery could cross the trust
  boundary. **Repair:** foundation files come from an exact allowlist, file
  access is path-confined, Markdown is sanitized through an inert template,
  text is escaped, and images use data URLs.
- **Finding:** native review Markdown printed the unqualified legacy
  `approve.py` command. **Repair:** native creation, recovery, revision, and
  amendment Markdown now names the exact workstation and directs approval
  through that selected Command Center view; the CLI footer is legacy-only.
- **Finding:** low-level native jobs could omit the workstation id or request a
  downstream step, and native detail reads did not revalidate the inner packet
  schema after catalog discovery. **Repair:** bound jobs require an exact id,
  native jobs are search-only, and the same exact native packet bytes are
  model-validated for outer and inner identity, canonical scope, status, and
  revision before a response is assembled.

## Standing prohibitions

- No pointer create, refresh, replacement, removal, or activation.
- No strategy, Assess, release, or Target gate decision made for verification.
- No report press, PDF/export press, release switch, or Desktop delivery.
- No metered, model, SAM quota, forecast, or network call during review.
- No adoption or relabeling of historical DHS sweeps, approvals, reports, or
  filenames as current authority.
- No migration/cutover and no retirement of the legacy-current All Federal
  workstation.
- No cross-workstation approval, evidence, report, release, or Target
  inheritance.
- No native write to client-global forecast, coverage, contact, Assess-pointer,
  outreach, or release state.

## Final SHAs

- Base UI commit: `eb2b14a`
- Creation/backend and native binding/search: `c87730a`
- UI repair layer: `9da821e`
- Documentation/handoff: this commit
- Integrated code HEAD reviewed before this handoff: `9da821e`

## Final verification

- Focused creation/API tests: **63 passed**
- Focused native mutation/concurrency tests: **49 passed**
- Focused native run/side-effect tests: **14 passed**
- Browser UI: **70 passed** at the last user-permitted real-Chrome run; the
  final **71 cases collect**, both inline script blocks parse in Node, and the
  newly added hostile-Markdown/in-flight-Back cases were not rerun in Chrome
  after the operator asked us to stop generating Chrome crash dialogs.
- Complete non-browser suite: **1129 passed / 1 skipped**. Two warnings are the
  sandbox refusing test-only job-log writes, not failed assertions.
- Changed Python surface: `ruff --no-cache` **passed**.
- `git diff --check`: **passed**.
- Final lane-diff and unrelated-file audit: **passed**; pre-existing
  `data/entities/unresolved.log` and
  `docs/reviews/cycle5-followon-solo-range.md` were excluded and preserved.

## Live NETSCOUT DHS creation receipt

- Client: `NETSCOUT`
- Workstation id: `agency_dhs`
- Scope: `{"agencies": ["DHS"]}`
- `clone_baseline`: `true`
- Creation response/status: `created: true`, packet `pending`, revision `0`
- `created_at`: `2026-07-13T18:53:24.054200Z`
- `created_from`: `operator`
- Baseline packet: `data/review/netscout.review.json`, SHA-256
  `2030a052396d786faf82052542098553c45a834da4ee66dbfd0debb675b6cf1e`
- Receipt: `data/review/netscout.agency_dhs.workstation_receipt.json`, SHA-256
  `590b84533ce6ce87de1465e126630cdd438e1114b985f3cc7797d06e8aee116e`
- Native packet: `data/review/netscout.agency_dhs.review.json`, initial
  SHA-256 `952ace3c29974ca6a46d09e48a9d7accba665f535a591d6452e77ffd4f2767c3`
- Registry: `data/review/netscout.workstations.json`, SHA-256
  `123c846cf221045a3b857c8202b8fbaf0870f36a37b9c1bcbdec12ccbff3aa47`
- Review Markdown: SHA-256
  `0fa39062d99ab3e3d246a9555453c6af195100696a36737841b50bec36519f41`;
  it names `agency_dhs` and contains no legacy `approve.py` command.
- Creation journal: SHA-256
  `2c61c3a6422e2f92cc17792f22db3a60f94332f27bb785e56c9affb352b077bc`;
  its first event exactly matches the receipt's client, id, scope, timestamp,
  source packet hash, and initial native packet hash.
- Legacy All packet post-create SHA-256:
  `2030a052396d786faf82052542098553c45a834da4ee66dbfd0debb675b6cf1e`,
  unchanged from pre-create.
- The before/after manifest changed only the five native/registry files above
  plus the registry's automatic `.history` copy. Nothing in `data/cleaned`,
  `data/reports`, `data/state`, or `data/handoff` changed. No sweep, approval,
  pointer, report, release, Target, outreach, or Desktop write occurred.
- Catalog result: legacy All remains current; DHS is native, Pending, and at
  Configure. The prior DHS filename-family sweep is `stale` and was not
  adopted.

## Handoff to William

The Tranche 3 HOLD is lifted. Hand William the Pending DHS workstation for
operator review. Do not press search, decide a gate, produce a report, move a
pointer, activate Target, or treat historical DHS artifacts as current state
without the next explicit authorization.

## Product-language follow-on

The strategy/keyword/NAICS judgment surface is now named **Analyst Layer** in
the pipeline and editor controls. Keyword Strategy and NAICS Boundary remain
workshops within it. This is naming only: packet persistence, approval,
post-approval amendments, and search behavior are unchanged. Verification for
the follow-on: 65 focused API/creation/alert tests passed, 71 browser cases collect,
both inline script blocks parse, changed-code lint passed, and Chrome was not
launched. The live DHS Markdown projection received the same heading-only
rename; its packet SHA stayed
`952ace3c29974ca6a46d09e48a9d7accba665f535a591d6452e77ffd4f2767c3`.
Follow-on SHA: this commit.
