# CONTRACT_SURFACES.md · never modified silently (audit 2026-07-10)

These surfaces are contracts between the pipeline, the client deliverables,
and the operator. Changing one silently corrupts trust downstream of the
code. The protocol, when a task requires touching one:

1. Complete all other work fully first; it ships regardless.
2. Implement the contract-surface change as an isolated, clearly-labeled
   diff with its own tests.
3. Present it separately for operator approval. Do not fold it into an
   unrelated commit.

## The surfaces

### External product slots · `docs/MARKET_MAP_CONTRACT.md`

The Federal Market Map is LILA's sole external product. Its eight slots,
names, and order are operator-locked by the 2026-08-23 ruling. The slots are a
fixed schema that each client press fills with governed evidence; a zero or
research gap fills its slot honestly rather than removing it. The executable
owner is `agents/golden_press/external_product_contract.py`, whose locked
digest makes any add, remove, rename, or reorder fail loudly. No other report
family, plan, fixture, or renderer may amend this surface. Only a later
explicit operator instruction may change the lock.

The executable consumers are
`agents/golden_press/external_product_projection.py` and
`agents/golden_press/external_product_render.py`. The sole external release
transaction is `agents/golden_press/product_bundle.py`, launched by Command
Center step `lila_release`. It must emit all eight slots, a certified graph,
the pressed evidence and replay inputs, validation, a file-hash manifest, and
one deterministic complete ZIP or emit nothing externally. The executable
family registry is `agents/reports/product_families.py`; it admits exactly one
releasable family, `lila_federal_market_map`. Signal Board, Candidate Review,
capture brief, target report, and the seven-section studio remain internal
views or compatibility adapters and cannot be selected by this transaction.

### counts() semantics · `agents/reports/document.py`
The single source of truth for every count client copy may state.
Load-bearing semantics that look like bugs but are doctrine:
- `competitors` is PRODUCT-GATED when a capability profile exists (lane
  cohabitants never count as competitors in client copy; L13 renders them
  as their own internal population instead).
- `pursue_notices` is SOLICITATION-level (amendment pairs dedupe to one).
Pinned by `tests/test_scoreboard_populations.py::test_counts_and_count_token_stay_product_gated`
and `tests/test_dashboard_views.py::test_banner_counts_come_from_counts`.

### {{COUNT:*}} / {{COUNT_WORD:*}} tokens
`resolve_count_tokens` (document.py) substitutes ground truth at render
time; unknown tokens are left in place and lint_counts flags them, never
silent. The composer-side vocabulary (the "available names" list) lives in
the `_SYSTEM` prompt in `agents/reports/capture_brief.py` and MUST match
counts() keys. Token semantics under agency focus: `agencies` resolves to 1;
component tallies are FACT figures with [F#] citations, never tokens.

### Fact ID format · `agents/reports/facts.py`
`F{n}` ids, `[F#]` citations in copy, validated by lint_text against the
FactPack. Renderers and the internal file both key on this format.

### Client templates and the locked schema
`CaptureBriefContent` (capture_brief.py): 3 thesis paragraphs, 5 stats,
fixed sections. `render_assessment` / `render_scoreboard` views;
`gate_for_sales` leak contract (client content physically absent from the
gated model; leak tests assert on strings, so the mechanism must stay
physical, not cosmetic). Identity assets (GTM logo, cover art) are fixed
constants of the client-approved template. Render marks
(`data-counts-verified`, `data-pop*`, `data-entity`) are machine-checked;
renaming them breaks lints and the dashboard drill-down hooks
(`ui/index.html` binds `.vb-comp`).

### C1 machine Horizon public projection

Every machine-composed Prospective Horizon card carries a public
`job_context` plus an inert `machine_evidence.job_context_basis`. The basis
names version, event kind, exact source field, and exact source text; the same
basis is bound into the INTERNAL compose trail. Award-window, recompete, and
expiring-contract contexts may use only a published description. Forecasts may
fall back to their published title. Client capability terms, relevance spans,
recipient names, and facts from other records are never job-description
sources.

`validate_machine_horizon_card` deterministically reprojects the public object
from that basis, and direct press invokes `validate_machine_horizon_contract`
after validating the current compose pair. Missing detail is public and named;
thin one-word descriptions remain verbatim with an explicit limitation.
Changing source priority, normalization, fallback wording, basis version, or
press revalidation is a contract change. C1 is the sole Horizon producer for a
machine board; later model enrichment cannot append context-less cards.

Official PROGRAM cards may enter that projection only through the shared
validated stored-row seam. The direct card, inert machine evidence, and
INTERNAL trail bind the same adapter, raw record id, composite route identity,
canonical official URL, timing value, and public timing label. Direct press
reopens only the declared adapter's current stored sweep and reproduces that
binding. Cross-adapter identity reuse, a rebuilt pair with a changed URL, or an
invented day for month-only or fiscal-year timing refuses named. PROGRAM
relevance may cite the native official `objective`, `abstract`, or `summary`
fields and their exact spans, but never a derived `matched_terms` list.

### C1 machine per-card client relevance

Every machine-composed Competitor lane, Candidate teaming partnership, and
Prospective Horizon card carries one public `client_relevance` object plus an
inert `machine_evidence.client_relevance_basis`. The public line is a pure,
deterministic projection of an exact CORE match. It uses decisive,
evidence-bounded capture language: competitor cards direct incumbent
replacement and follow-on capture; teaming cards direct either a
partner-or-displace decision on a cited award holder or outreach to a prime
identified by a matched prime-channel signal; Horizon cards direct defend,
takeout, pre-solicitation,
recompete, or expiry action according to the stored event kind. These are
prescribed actions derived from cited facts, never invented claims that a bid,
partnership, or outcome already exists.

The basis names the profile surface and version, CORE term, exact matched text,
source field, source identity, and bounded source quote. A matched
prime-subaward signal requires two supports: the selected play and the cited
subaward description. It supports decisive outreach, but never claims that the
historical subaward proves access to the selected opportunity.
The identical basis is bound into the INTERNAL compose trail by `(band,
card_identity)`, because one award may legitimately appear in more than one
band. Direct press invokes
`validate_machine_client_relevance_contract` after validating the current
compose pair. Missing, changed, duplicated, extra, or cross-band evidence
refuses named; operator-authored legacy cards remain readable without the new
field.

Direct press also rebinds every basis to the current client profile and
taxonomy version, requires exact current CORE membership, and reproduces each
matched span from the exact stored source identity. Buyer, partner, route,
Horizon event kind, and client-footprint semantics must agree with the other
structured fields and the current sweep/calendar. Relevance text is never
line-clamped: the complete capture directive remains visible. Machine
`client_relevance` copy may not contain the defeating phrases `qualify`,
`unverified`, `validation-required`, `role validation`, or `not an open bid`;
the structured evidence and section framing carry those distinctions without
weakening the client-facing action.

### Federal Opportunity Pre-Assessment source-coverage projection

Every Signal Board press projects a public **Research source coverage** band
from the designated stored sweep's `results._attempts` manifest. The catalog
owner is `tools/api/source_catalog.py`; its standard-lane sequence follows the
sanctioned `run_searches.py` fan-out order and is reconciled against the runner
before collection starts. Catalog lanes absent from the manifest read
`NOT RUN THIS REFRESH`; clean attempts read `RETURNED`; interrupted bounded
returns read `PARTIAL RETURN`; stale official evidence reads `STALE OFFICIAL
SNAPSHOT`; and hard failures read `RATE-LIMITED/FAILED`. The public model retains
no raw provider error or response text. Unknown future attempted source names append
deterministically after the catalog, preventing the fixed catalog from hiding
a newly added lane. Result provenance refines a nominal success when required:
`contract_awards.source_mode=live_usaspending_fallback` reads `RETURNED VIA
OFFICIAL FALLBACK`, and `sbir_gov._provenance.stale=true` reads `STALE OFFICIAL
SNAPSHOT`. Neither mode may claim that its primary API returned.

Connected capabilities outside the standard sweep are a separate, explicitly
non-contributing list. SAM Notice Detail/Resources and SAM Entity
Management/Exclusions each state `NOT USED THIS REFRESH`; their presence cannot
imply that they supplied a fact in the report. GSA Acquisition Gateway is an
enabled child of the standard acquisition-forecast lane, so its current child
attempt and limitations remain inside that lane's provenance. The Evidence
dock remains the distinct, curated set of cited
records. The locked renderer lint requires the coverage band in order, all
cataloged standard rows, and the connected-not-used disclosure. Changing the catalog,
status projection, ordering, connected-capability disclosure, or the press
adapter's stored-sweep binding is a contract-surface change. The 2026-07-20
catalog has 31 standard lanes, including DSIP, RegInfo Unified Agenda, DARPA,
DoD budget exhibits, and ForeignAssistance.gov; tests bind the count and exact
order to the same central catalog rather than duplicating either value here.
The sweep also stores a deterministic aggregate verdict. `comprehensive` means
every required exhaustive lane completed and every required bounded lane met
its declared boundary; it never means the entire internet or every restricted
procurement system was searched. Advisory gaps remain visible without blocking
that contract-level verdict.

### Forecast press impact and window decay (2026-08-18)

"Changes the deliverable" has one meaning for the forecast layer: the
live-window keep count, `press_impact_live_window_keeps` on the frame
re-screen forecast receipt. A keep counts only when `classify_window`
(tools/frame_rescreen.py) dates it live against an explicit as-of day
from a stated solicitation or award window. `fy_only` rows (a stated
fiscal year only, dated to that federal FY's September 30 end and sorted
by it), window-unstated rows, and stated-past rows never count;
stated-past rows are reclassified evidence carrying the decay note,
never candidates, and are never dropped. Changing this definition, the
window classifier's state vocabulary, or the ordering that puts live
rows first is a contract-surface change.

### DRAFT/RELEASE gate · `run_capture_brief.py`
State machine in the `.qa.json` sidecar. RELEASE requires a current human
Assess approval AND zero open flags AND arbiter consensus AND the operator's
`--release` switch. The operational approval at
`data/review/<slug>.assess_approval.json` is bound to the exact gate scope,
selected sweep contents, and complete capability profile; any change makes it
ineligible until the operator reviews and approves again. Approvals made
through the standard operational/UI path also carry an additive
`evidence_manifest` (2026-07-11: per-notice salient fields plus a
hash of the complete notice and triage record, triage verdict and reason,
flattened profile, and scope) so the dashboard can show what changed next to a stale
approval (`approval_evidence_diff`,
`GET /api/client/<slug>/approval-diff`). The manifest is explanatory UX only;
the binding comparison remains the sole staleness authority, and pre-manifest
approvals stay valid. The execution
boundary and HTML/PDF download boundary both revalidate it. Direct runner use
without a current approval may still generate evidence, but only as DRAFT or
`.DO-NOT-SEND`, never as a client release. Flag adjudication lives at
`data/review/<slug>.flag_decisions.json`. The dashboard Produce button is the
sanctioned path. Agents never weaken any leg of this conjunction.

Content-identical Assess reapproval is idempotent. When the binding, note,
evidence manifest, and complete partial-release state are unchanged, the exact
prior approval artifact and `approved_at` are retained. A transient failure to
regenerate the explanatory manifest retains the prior manifest only when its
binding and every other approval fact are unchanged. This prevents a timestamp
from changing the approval hash, immutable run identity, or CURRENT pointer by
itself. Any substantive approval change still writes a new artifact.

The three-view runner treats an exception or timeout in either optional
model-compose phase as a blocking `compose_failure`: deterministic
client/sales/internal HTML still materializes, every selected filename is
`.DO-NOT-SEND.html`, and no PDF or Desktop delivery occurs. This fallback does
not alter the approval, scope, or release resolver.

Internal review PDFs (operator-directed, 2026-07-12): POST
/api/export/review-pdf renders ANY report html, releasable or not, through
the ungated exporter for operator review. It is not a release door and never
weakens one: the output is force-stamped with an INTERNAL REVIEW banner,
named `.INTERNAL-REVIEW.pdf`, written only under data/state/review_exports
(never data/reports, never the Desktop delivery folder), and the gated
/api/export/pdf refusal rules are untouched.

The stable full-federal HTML filename carries no release state.
`agents/reports/release.py::release_state` is the ONE derivation point
(2026-07-11): every artifact family (stable sidecar-certified, client-view
filename contract, legacy capture_brief), one html-mtime time basis, newer
DO-NOT-SEND always superseding, sidecar html_sha256 build-pairing, and the
Assess-approval axis folded in. The FINAL PRODUCT badge, report rows, document
shelf, HTML download, PDF export, and the /report preview banner all consume
that single verdict and fail closed when the sidecar is missing, unreadable,
stale, mispaired, DRAFT, or explicitly release-ineligible.
The DRAFT HTML remains available as an internal preview. Legacy
`capture_brief` and explicit `.DO-NOT-SEND` artifacts retain their filename
contract. Across stable full and alternate-view families, a newer
`.DO-NOT-SEND` build always supersedes an older clean file at the download
boundary.

### Operator gates
Search scope, strategy approvals, release switches: engagement decisions,
never agent-side (L11). The Command Center API contract
(`POST /api/run {client_name, step, args}` -> `{job_id}`;
`GET /api/job/<id>`; disk-backed logs in `data/state/job_logs/`) is what
external tooling and the operator's muscle memory depend on. The job read
surface additively returns server-authoritative `started_at`, `finished_at`,
`elapsed_seconds`, `quiet_seconds`, `heartbeat_age_seconds`, and
`runner_state` (`starting`, `process_alive`, `heartbeat_stale`, `finished`). A
fresh heartbeat means only that the Control Room recently observed the child
process alive; it does not prove a nested model/network call is advancing. A
stale heartbeat is diagnostic only and never changes job status, releases the
same-client lock, retries, kills, or affects an approval/release gate. Private
monotonic clock fields never leave the API. `artifacts_written` counts
successful `[out:*]`/absolute `[out]` log records, excluding failed writes; the
UI uses it only to refresh and expose a generated DRAFT after a nonzero exit.
It never changes the job status or release verdict.

### Scope-workstation creation and operating surfaces (2026-07-13)

`agents/workstations.py`, `GET /api/client/<slug>/workstations`,
`GET /api/client/<slug>/workstation/<id>`, and the canonical
`/client/<slug>/workstation/<id>` URL form one additive navigation contract.
The route slug is never client identity: a present review packet must resolve
the exact name, and malformed identity/registry state fails closed. Workstation
scope is deeply immutable, registry labels are derived, agency resolution is
exact, and multi-agency order follows the stable agency catalog so equivalent
inputs mint one established artifact designator.

`POST /api/client/<slug>/workstations` is the one public Tranche 3 creation
surface. The body contains exactly `scope` and boolean `clone_baseline`; the
public surface requires the client baseline clone and returns 409 for
`clone_baseline: false`. Creation is additive coexistence, not migration or
cutover. An alternate native id may be created while the legacy-current id
remains independently current. Posting the exact legacy-current id is a
byte-inert duplicate (`created: false`): it cannot create a registry, receipt,
native packet, Markdown, or journal. Any same-id native prefix requires an
explicit migration, while an already-complete historical native owner may be
replayed without changing bytes.

Creation is a receipt-first, registry-last transaction under one per-client
cross-process lock. The receipt binds exact client, canonical id/scope,
baseline-clone choice, aware creation time/source, source packet SHA-256, and
initial native packet SHA-256. Receipt, packet, Markdown, and creation journal
are prepared against one exact baseline packet snapshot; the source hash is
checked before file promotion and again before the registry is atomically
promoted as the visibility switch. Ordinary exceptions roll back only new
files. A hard-stop prefix is recoverable only when an identical request can
reproduce it against the unchanged baseline. Creation replay validates the
receipt-bound first journal event and required Markdown instead of rewriting
them. Runtime authority remains deliberately generation-based: the active
registry row and receipt hashes prove immutable native ownership, and the
separately read packet hash proves the mutable strategy generation.

The clone is the client's complete validated baseline strategy, including its
pursuit framing, keywords, NAICS state, target notes, set-aside angles, search
specifications, and preserved near-miss context. The new packet is Pending at
revision zero and inherits no reviewer decision, approval, sweep, evidence,
report, release, pointer, or Target state. Existing NAICS metadata is preserved
exactly and in order; missing inferred codes are appended in boundary order.
Only exact affirmative NAICS keyword evidence may hydrate a rationale.
Near-miss reasons remain cut/adjacency context and never become affirmative
in-boundary explanations. Missing explanations remain blank for the operator.
Native review Markdown names the exact workstation and provides no unbound
`approve.py` instruction; that command remains a legacy-only gate surface.
The Command Center presents this complete judgment surface as the **Analyst
Layer**, with Keyword Strategy and NAICS Boundary as its internal workshops.
This is a product-language contract only: it does not add an approval state,
change packet persistence, or alter the pre/post-approval mutation APIs.

Only an exact legacy-current row at Assess, Produce, or Target may receive the
full compatibility operating payload. Configure and Search receive a smaller
physical whitelist: client foundation, the current keyword/NAICS boundary, and
the action appropriate to that phase. They contain no prior sweep, approval,
report, release, sidebar-target, or Target state. Every noncurrent row is
dormant until workstation-native strategy packets exist and receives no
strategy or downstream state at all.

A designated sweep advances progress only when its regular JSON payload binds
the exact client and scope. One compatibility case is frozen: an unqualified
pre-workstation sweep selected by the legacy-current All Federal row may omit
`search_scope`, and that omission means All Federal only. Explicit scope
metadata remains authoritative; focused and native sweeps require it. The
sweep must also prove it was generated from the current keyword/NAICS
boundary. New sweeps embed the exact integer
`strategy_revision`; a revised packet makes any different revision stale.
Legacy sweeps without that field remain compatible only before the first
revision, or when an aware `generated_at` proves they postdate `revised_at`.
Stale same-scope evidence returns the workstation to Search and cannot lend
Assess, Produce, or Target progress to the amended boundary. Detail assembly
revalidates one packet/sweep
snapshot against the selected workstation and checks both hashes again before
releasing the response; concurrent replacement returns a 409 rather than a
mixed payload. If initial scope proof fails, the downstream body is withheld
rather than shown under the workstation label.

Full-workstation auxiliary GETs (banner, competitor detail, change digest,
recompetes, and Target rail) carry `workstation_id` and rebind the exact
current packet/sweep before reading scope-owned material. Cross-workstation
requests return 409. A change digest is shown only when its embedded
`source_cursors.scope_designator` matches. The legacy recompete calendar has
no scope field, so an exact workstation withholds it until that artifact is
partitioned; a client-only filename is not scope proof. The combined browser
request-graph and server byte-tree tests pin these GETs as observational.

`POST /api/run` accepts the additive `args.workstation_id`. Omission is
compatible only when the server resolves exactly one legacy-current owner;
dormant, unknown, or stale ids are rejected before launch. The resolved exact
client/id travels into the child environment, where packet and scope owners
recheck it before reading runner inputs. Search also rechecks revision and
scope at the last zero-spend point before fan-out. A UI switch, concurrent
gate edit, or packet replacement therefore cannot move or relabel a job.

Native mutations are compare-and-swap operations over three independent
generations: registry SHA-256, receipt SHA-256, and the exact packet SHA-256
returned by the previous read or write. The mutation endpoint requires the
native workstation id and `expected_packet_sha256`; under the packet-writer
and native-creation locks, the persistence owner revalidates ownership and
packet generations before an atomic write and returns the replacement packet
hash. Native and legacy packet resolution are explicitly forced, so neither
family may fall through to the other.

A native Tranche 3 job requires explicit workstation identity and carries
exact client, workstation, packet hash, strategy revision, registry hash, and
receipt hash into the child. Only `searches` is authorized. Search reacquires
the packet-writer and ownership locks, rereads all bindings, and submits its
initial source work while those locks are still held, closing the pre-spend
mutation window. Its admitted workstation outputs are the exact scoped sweep,
scoped research picture, and scoped sweep snapshot. It must suppress
client-global forecast store/snapshot and coverage-ledger writes, generic
source-attempt coverage, contact-graph harvest, Assess current-pointer refresh,
and Outreach growth.
Opening native downstream approval, report, release, or Target execution is a
later-tranche contract change.

Report shelves admit the exact designator family; `all` excludes every
`agency_*` family and focused prefixes do not admit a near-prefix family such
as DHS + CISA into DHS. Unqualified working-intelligence and Desktop files stay
client-global because they carry no machine-verifiable scope identity.
Document ordering/hide preferences are stored per workstation.

The shared client-foundation area is client-global by design and cannot be
populated by artifact-family discovery. Its strict allowlist is the exact
intake submission, `clients/<canonical-slug>/profile.json`, baseline strategy
Markdown, and resolved client brand mark. Reports, sweeps, approvals,
outreach, and Desktop content are excluded. `/api/file` remains realpath-bound
to `data/`, with only the exact capability-profile exception; traversal and
unsupported binary files fail closed, and allowed images are returned as data
URLs. Browser rendering treats Markdown as untrusted: it parses in an inert
template, retains only the explicit safe tag vocabulary, strips attributes,
admits only HTTP(S) links with a forced new-window `noopener noreferrer`
policy, escapes JSON/text, and discards responses whose client/workstation
origin is no longer active. Weakening either the server allowlist or renderer
sanitizer is a contract-surface change.

Listing, selecting, deep-linking, Back/Forward restoration, and responsive
rendering are GET-only and byte-invariant. The automatic scoreboard banner is
observational and never adopts brand files or starts view jobs; outreach GETs
never rename a corrupt operator file, and surface corrupt curated bytes as an
unavailable state rather than an empty list. Navigation never calls `set_scope`,
creates a registry, decides a gate, moves a pointer, activates Target, presses
a report, or writes entity-resolution telemetry. Weakening a phase whitelist,
adding a fallback to another workstation, or letting filename existence
override the embedded scope binding is a contract-surface change.

### Immutable Assess run and ledger identities
`agents/assess/contracts.py` and `agents/assess/ledger.py` define the strict
machine-readable intelligence boundary behind Review. A run envelope is bound
to the exact scope, sweep, profile, projected ledgers, source coverage,
diagnostics, approval state, and hashes of mutable projection inputs (Horizon,
qualification, live requirement decisions, partnering state, approval, entity
crosswalk, and vehicle reference). The immutable envelope preserves the exact
projected output and hashes of raw inputs; transactional `.history/` artifacts
preserve the exact mutable source bytes. Live family ids use explicit SAM
issuing-office + solicitation identity
when both exist, otherwise notice identity; every amendment posting maps to
that stable family id. Partner records may link only to live/thesis ids in the
same run. Ledger ids, source-coverage uniqueness, SAM-only live provenance,
and classification/recommendation consistency are validator-enforced.

The immutable envelope path is
`data/state/assess_runs/<slug>--<exact-name-hash>/<run-hash>.json`; the only
mutable object is the atomic scope-specific current pointer.
`GET /api/assess/run` is the read
surface. Changing identity inputs, weakening strict `BID_NOW`, allowing broad
NAICS partner evidence, overwriting an existing run, or making the sidecar a
prerequisite for search/report generation is a contract-surface change and
requires isolated tests and operator review.

`BID_NOW` additionally requires an exact human-reviewed requirement span bound
to current NOTICE evidence. A nonempty attachment inventory requires a human
review bound to the reconciled inventory hash, reviewer, and timestamp. The
review API rejects run/evidence/excerpt/hash drift at the POST boundary.

### Assess -> Target operator door (2026-07-12)

The outreach lane (contact plan, target report, Target rail, Apollo) is
physically locked behind an explicit, journaled operator decision:
`data/review/<slug>.target_approval.json`, written only by
`POST /api/review/target-approve`, which itself refuses without a CURRENT
Assess approval. `agents/review.py::target_gate_status` is the one gate
derivation: unlocked means an unrevoked proceed-to-Target decision AND the
evidence-bound Assess approval is currently valid, so evidence drift re-locks
outreach with no new click. `POST /api/run` holds `contacts` and
`target_report` closed server-side; the dashboard collapses the Target rail
until unlocked. Revocation stamps `revoked_at` and never deletes the audit
event. Weakening any leg, or unlocking outreach from any other code path, is
a contract-surface change.

### Title-set approval (2026-08-06, ADDITIVE to the door above)

The targeting supply step requires a SECOND, narrower operator decision on
top of the Assess -> Target door: `data/review/<slug>.title_approval.json`,
one entry per motion, each binding the SHA-256 fingerprint of the canonical
inference payload that produced that title set.

It exists because of ordering. Both approvals above PREDATE the titles: the
Target gate is what unlocks the lane that generates them, so neither can
bind a vocabulary that did not exist when it was signed.
`agents/golden_press/title_approval.py::supply_may_run` is the one
derivation, and it consults `target_gate_status` FIRST, so a closed Target
door still closes everything and this gate can never unlock outreach from a
new path.

`target_gate_status` is deliberately UNCHANGED and keeps exactly its two
legs. Adding a third would re-lock every client currently holding a valid
Target approval until a title set existed, which would weaken a documented
door rather than extend it. Drift in the bound fingerprint (evidence,
organisation resolution, capability vocabulary, rules, schema, or prompt
version) re-locks supply with no new click, mirroring how evidence drift
already re-locks Produce. Revocation stamps `revoked_at` and never deletes
the audit event, per motion or wholesale.

Weakening either leg, unlocking supply from another code path, or recording
an approval from anything other than an explicit human act is a
contract-surface change.

### Client-file doctrine strings
Banned in client copy (R14): "unverified", "not verified", "did not
verify", "no record", "not capability-verified", "pending verification",
"zero matched", "could not confirm". Plus the em-dash ban (R9 +
lint_emdash) and BANNED_PHRASES. These lists are contracts with the
white-label promise; additions are cheap, removals need approval.

### Official-source Horizon factory boundary
`agents/reports/horizon_discovery/` may emit PROGRAM-tier fact-bank rows only.
Its registry is intentionally distinct from the live discovery registry, and
adapters must not implement or return live-opportunity search records. Adding a
source means one adapter file, one registration import, fixture mapping tests,
and proof that the emitted fact kind projects to `EvidenceTier.PROGRAM`.
Coverage ownership ends at the live collection boundary: live fan-out records
one `record_pull` row for each source it actually attempts, including
collection failures. Offline Horizon adapters map stored payloads only and
never mutate coverage; replay, recomposition, an absent stored key, or a stored
error envelope is not a new pull. The positional `record_pull` signature,
Horizon validation gate, fact-bank citation matching, and no-invention rules
remain unchanged contract surfaces.

Reviewed decision-maker rows are independently reloaded from
`clients/<slug>/signal_board_people.json` at compose and direct press. Their
`route_kind + route_identity` must equal a direct identity on a displayed Best
Fit, Competitor, Candidate teaming, or Prospective Horizon card. An identity
buried only in machine evidence, a route basis, the evidence dock, a POC row,
or another nested support object does not count. Rebinding a person to another
otherwise valid displayed card refuses named even when the compose-pair hash
is rebuilt, because the reviewed roster supplies the independent binding.

In keyword-keyed Federal Register payloads, `_attempts`, `disabled`, `error`,
`errors`, and `note` are control metadata, never evidence lanes. The adapter
skips them before PROGRAM-row construction.

Legacy `budget_pressure` facts retain their MARKET-tier meaning. Factory budget
rows use the distinct `budget_line` kind and project as PROGRAM/BUDGET. CISA KEV
rows are government-wide TIMING context; the CISA publisher identity never
establishes a CISA procurement buyer.

### Partner identity gate on pursuit cards
Opportunity-specific partner paths render inside the existing pursuit-card and
standalone-dossier structures. Client and internal views may carry candidate
identity, grade, and cited evidence. The sales model may carry direction and
verifier-bearing lock shape only: candidate collections, evidence details, and
every exact candidate-name occurrence must be physically absent before any
sales renderer runs. `gate_for_sales`, its full-model leak test, the unchanged
`AssessmentDocument` shape, and `counts()` teaming semantics are contract
surfaces. Cited-news rows are atomic at this boundary: a row containing an
exact candidate identity in any string field is removed before the three-row
sales cap, allowing later safe rows to backfill. Remaining cited headlines and
URLs stay byte-for-byte unchanged rather than receiving lock-token redaction.

### Strict Live SAM report cutover and release leg

The exact scope-specific current Assess pointer is the only report cutover
marker. Its three states are contractual: no pointer means legacy fallback;
a fully validated pointer means ledger-backed report truth; a present but
invalid, stale, cross-client, scope-mismatched, profile-mismatched, input-
drifted, malformed, or expired pointer fails the Live SAM lane closed. Pointer
removal is the supported rollback. No second feature flag or implicit
fallback may weaken those states.

If a sweep's selected agency cannot form a valid scope designator, the resolver
must inspect the exact client's pointer directory before deciding state. No
pointer means `ABSENT` and preserves dormancy. Any pointer means `INVALID`
because the resolver may not guess a scope. An unreadable pointer directory is
also `INVALID`.

Pointer CREATION is equally operator-owned (2026-07-12): every runner and
gate hook refreshes through the creation-free owner
(`tools/assess_refresh.py::refresh_current_assess_run_if_active`), which
materializes ONLY where a pointer already exists. The refresh carries the exact
pointer bytes its existence check observed, and `persist_assess_run`
compare-and-swaps against them: an operator removal or replacement during the
refresh means nothing is written and the operator's pointer state stands
(item 23 repair, 2026-07-12). Activation remains the only expectation-free
persistence path. First activation is the
operator's explicit act (`python -m tools.assess_refresh --client ... --activate`,
or a future Control Room control). A code path that mints a pointer as a
side effect is a contract violation, not a convenience.

Once current, `LiveSolicitationLedger` records control pursuit eligibility,
solicitation-family identity, recommendation, deadline, and NOTICE evidence
for the board and Section 01 fact inputs. Raw SAM rows can supply display-only
fields after the strict join. `BID_NOW` plus `PURSUE`, a future deadline, and
the exact human-reviewed requirement evidence remain mandatory. Program,
market, forecast, budget, legislation, and award-expiry evidence cannot imply
a live posting.

The established `counts()` keys and semantics, including solicitation-family
dedupe for `pursue_notices`, the `{{COUNT:*}}` token vocabulary, fact-id
format, and `build_fact_pack` signature are frozen. `verdict_totals` remains
posting-level and therefore counts amendment postings even when the board has
one family card.

For a backfilled client, the existing Assess approval release decision is
conjoined with the immutable strict run's `can_release()` result. The public
`release_state` shape is unchanged. Partial release is an explicit operator
decision attached to the exact current required-source blocker rows and their
digest. It is not a caller bypass, does not carry across blocker drift, and is
physically separate from normal approval. Clients without a pointer keep the
pre-cutover release behavior exactly.

`regulations_gov` and `cisa_kev` are required Horizon source-coverage rows.
Until their collectors provide a run-bound attempt and completeness manifest,
successful payloads remain `PARTIAL`; missing or failed pulls remain blocking
rather than disappearing from coverage. Refreshing a run created before these
rows were registered changes the required blocker manifest, so any prior
partial-release authorization intentionally does not carry forward. Release
remains held until the operator approves the newly displayed blocker rows and
digest.

The operator decision is also bound to the run id and blocker digest displayed
before the click; stale or missing UI state is rejected before the approval
artifact is written. For an exact backfilled scope, report HTML older than the
current pointer is a pre-cutover artifact even if an older QA sidecar called it
clean. `release_state` therefore requires the selected HTML to be strictly
newer than the pointer while preserving its frozen return shape. Identical
immutable-run persists leave the pointer untouched. ABSENT pointers retain
legacy artifact behavior. Date doctrine (Cycle 5, 2026-07-12): when callers
omit `as_of`, `AssessmentDocument.as_of` and `FactPack.as_of` use the operator
host's local calendar date for presentation and provenance, while every
date-only deadline, eligibility, and candidate-grading comparison uses the
shared UTC cutoff. An explicit `as_of` binds both clocks for deterministic
offline replay. Sales relative-deadline copy consumes the pursuit grade's
comparison date, not the presentation stamp. Internal same-day QA converts an
aware sweep timestamp to the host-local date before comparison; naive
artifact-mtime fallbacks are already local, and malformed timestamps retain
the confidence warning. Held or incomplete strict coverage must reconcile
accepted posting totals against the SAM census in client copy and pass
`lint_screen_census`.

`ABSENT` also preserves the legacy client-visible zero-triage landscape. A
nonzero `sam_census` alone cannot activate strict reconciliation copy or marks.
CURRENT held or incomplete coverage remains explicitly reconciled and linted.

Run-id pairing (Cycle 4, 2026-07-12): under a current pointer the
stable-family QA sidecar must also STAMP the exact current run id
(`assess_run_id`); a missing, superseded, or sentinel stamp
(`strict:invalid`, `strict:unresolved`) fails release closed even when the
mtime ordering looks fresh. The pointer's run id is obtained through
`agents.assess.current_run_identity` (stat-memoized in release.py), so a
present pointer the seam refuses to validate blocks release rather than
supplying raw bytes. Clients without a pointer ignore stamps entirely
and legacy sidecars stay byte-identical (the stamp key is written only when a
strict state exists). The same one-per-press identity token folds into every
compose cache key (agency content fingerprint, full-picture review digest,
sectioned compose digests, resume fingerprint), so prose priced under one
evidence truth is never reused under another. Under a CURRENT projection the
composed Section-01 cards must join the strict actionable NOTICE allowlist
exactly (id and evidence URL), fit traces are built from the ledger's records
and never from legacy triage, and the agency runner rematerializes an
EXISTING strict run after its final scoped write (creation-free: activating a
pointer stays an operator decision). Weakening any of these legs is a
contract-surface change.

### Current Assess run identity seam

The public read surface is frozen as:

`agents.assess.current_run_identity(client_name, designator, *, review_dir=None) -> Optional[dict]`

For an exact CURRENT pointer it returns a dictionary with exactly these four
keys, and no others:

- `run_id`
- `pointer_digest`
- `blocker_manifest_sha256`
- `persisted_at`

`pointer_digest` is the canonical JSON digest of the coherently sampled current
pointer. `blocker_manifest_sha256` is the established required-blocker manifest
digest for that immutable run's coverage. `persisted_at` is the pointer file's
UTC ISO-8601 modification time. The seam validates the immutable artifact,
exact client and scope binding, current mutable projection inputs, and a pointer
that did not change during the read.

An absent pointer returns `None`. Every present-but-invalid, stale, drifted,
malformed, cross-client, cross-scope, or concurrently changed pointer also
returns `None` and logs the reason; this surface raises nothing to its callers.
It reports identity only. It does not express approval or release eligibility,
does not call or alter `release_state`, and does not change that function's
frozen return shape. Changing the signature, return-key vocabulary, digest
meaning, or fail-`None` behavior is a joint contract-surface change.


## 2026-09-08 operator amendment: assessments with lead generation

`agents/golden_press/product_bundle.py` consumes the read-only Assess exporter
and pure lead Press through `product_leadgen.py`, producing one complete ZIP.
Slot 5 retains all stored L1 assessments with their dispositions; qualification
continues to govern priority ranking and lead-tier promotion. Source detail
recovery uses `l1_store._to_record`, preserves known values and original dates,
and leaves strict ledger evidence gates untouched. Replacement releases block
on source-record or detail loss. See the matching CONVENTIONS and Market Map
contract amendments and `tests/test_assessment_output_retention.py`.

## September 8 operating-repair amendment (explicit operator request)

The operator authorized repairing dossier delivery, importing the three reviewed
Apex research cases, completing targets, and narrowing priority while preserving
assessment history. This additive change retains the eight assessment slots,
operator approval/scope semantics, and independent lead qualification tiers.

- PublishedContact adds optional email and phone. LeadRow adds typed targets,
  reviewed research context and company evidence IDs. Contact source, status,
  authority boundary, route, reason and next ask travel together.
- A bound CompanyDossier is validated against the release client, considered by
  the qualifier, embedded in the receipt, and packaged with its original digest.
  A mismatched or changed supplied dossier blocks replacement.
- ReviewedCases is a versioned, scope-bound input to the immutable Assess ledger.
  It cannot approve bid_now or overwrite a discovered notice. Its hash is an
  Assess projection input; a changed casebook must be refreshed before release.
  The strict report authenticates the same casebook bytes and exact record before
  projecting supplemental research separately from discovered SAM postings.
  Research priorities may appear as explicitly labeled standing research without
  changing source classification, raw counts, discovery badges, pursuit rank,
  or existing watchlist selection. This September 9 repair completes the authorized
  reviewed-case path; corrupt raw/index joins and all release gates still refuse.
- Research priority never implies LEAD_T1/T2 or permission to contact. Closed RFI
  sources may support explicitly conditional follow-on investigation. Source
  deadlines remain historical, and unanswered questions stay visible.
- Disposition changes archive the previous casebook. Priority selection shows at
  most three reviewed cases; all parents, targets, reasons and evidence remain in
  the assessment and immutable releases. A stored quality baseline blocks
  silently dropping previously reviewed cases or their target details.

Client dossiers, research inputs, enrichment and generated releases are operating
data, not versioned source. Synthetic regression fixtures exercise the contracts;
real Arista/Apex acceptance is recorded separately through Command Center.

When the lead companion is complete, automatic priority requires LEAD_T1 or
LEAD_T2 and is capped at three. Reviewed investigation/qualification cases may
form the separate explicit priority selection. An assessment classified as a
qualified opportunity alone cannot silently become seller-ready priority.
