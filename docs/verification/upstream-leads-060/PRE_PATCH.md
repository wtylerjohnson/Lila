# Upstream lead discovery: pre-patch investigation

Work ID: UPSTREAM-LEADS-060. Owner: this LILA build task.
Date: 2026-09-16. Authority: user's upstream lead-generation implementation request.
Development: `/Users/wtjohnson/lila-upstream-leads-20260916`, branch
`codex/upstream-leads-20260916`, baseline `8f19f7f5abaae7a425267bbbb2c45ee99c32f95f`.
Plan: LILA-WEEKEND-2026-09-11-v1, SHA-256
`71a4c7c11ee575596357e5e0fcfb66a75d2c855b4761040042732c8cad26bfc5`.

## Existing work and real-run trace

Current main includes accepted weekend repairs, parent-only research, published
forecast contacts and native action sheets. These are not new work. Frozen
benchmark 005, JTG repair worktree, SWIPE and BD Radar remain untouched.
The operating checkout's modified NETSCOUT profile, taxonomy, engagement scope
and entity log are protected; this development work does not adopt them.

The real saved DHS sweep generated 2026-09-12T23:55:16+00:00 is
`data/cleaned/searches_netscout.agency_dhs.json`, SHA-256
`6ffa90df28d62c99103937cf3e19020a656338ff207f001111c6973796da3756`.
It is NETSCOUT evidence, not a Veeam evaluation. It contains 176 scoped forecast
records reported, zero matched forecasts, and errors for GovInfo, funded demand,
Congress, federal hierarchy, CALC and regulations.gov. Errors name missing keys
in that run; this does not establish the current credential state.

The ordinary search producer (`run_searches.py:1644`, `:2012`, `:2076`) already
collects forecast, oversight, budget and other PROGRAM sources. Forecasts can
enter native research via `forecast_signals.research_candidates`.
`agents/assess/ledger.py:2172` calls `build_research_ledger`; that builder
(`agents/assess/research_subjects.py:138`) only reads forecasts and awards.
Native LeadGen subsequently retains these as research parents with zero buying
children (`tests/test_research_subjects.py:39`). Existing contact and action-sheet
renderers preserve source records, open questions and approved review overlays.
This is a real-input trace plus code-path evidence, not a new end-to-end release.
Command Center was not listening on port 8321 during this investigation. No
Veeam client profile or native Veeam run was found in the operating checkout.

## Prioritized causal gaps

1. **Evidence is lost before screening.** `tools/api/watchdog_rss.py:47` truncates
   each report body to 400 characters before capability matching. `:96` returns
   only keyword hits, capped at 20. Misses and their original text cannot be
   re-screened from that artifact. Preserve full parsed inventory and per-feed
   collection receipts separately from the existing compatibility result list.
2. **Native transport is narrower than collection.** ResearchSubject accepts
   only award/forecast (`agents/assess/contracts.py:508`). PROGRAM rows reach a
   separate Horizon bank, not native research. Budget mapping selects eight rows
   and four values (`agents/reports/horizon_discovery/dod_budget_exhibits.py:36`);
   watchdog Horizon excerpts are also capped (`watchdogs.py:26`). Add an explicitly
   versioned, source-bound PROGRAM research variant; do not relax live/Horizon
   qualification or invent buying authority. Preserve every original field.
3. **Coverage does not trace losses to delivery.** Source verdicts exist, but an
   adapter being registered or returning data does not establish agency/component
   coverage or investigated/actionable counts. Add an internal, nullable stage
   matrix with exact record references, receipts, errors and unmeasured states.
4. **Source scope is genuinely incomplete.** The DoD budget adapter reads PDI
   and counterdrug books (`tools/api/dod_budget_exhibits.py`), not the MDA budget
   justification portfolio. Recent RSS is not an oversight archive. These are
   collection limitations, not downstream classifier negatives. No adapter-count
   claim can close them.
5. **Research is not yet an actionable lead.** A keyword hit or general strategy
   does not establish a need owner, current decision, permissible route or named
   contact. Reuse existing source-bound review/target records and mark remaining
   fields unknown. Do not manufacture approvals or paid enrichment authority.

## Patch boundary and acceptance

Shared collector retention, native research projection, internal coverage and
targeted regressions only. Explicit additive ResearchSubject v2 PROGRAM variant;
existing v1 award/forecast identities and archived Assess runs remain readable.
New rows change their containing run identity and require normal review. No
automatic migration/reapproval, gate changes, frozen-case edits or client-count
targets. No new source adapter is justified merely by this transport repair.

Acceptance: original text beyond 400 chars can trigger screening; complete
inventory and partial failures survive; PROGRAM primary records retain exact
payload/hash and capability spans through native Assess and LeadGen; duplicate
observations/chronology are not silently discarded; future-captured evidence
cannot pass a historical cutoff; broad/irrelevant or out-of-scope records do not
become leads; no RFP, dollar amount or confirmed funding is required for research.
Unknown coverage remains unknown. Named-contact coverage counts only supported
targets, not email syntax. Legacy replay, qualification and full strict offline
regression must remain intact.

Veeam/DHS and Veeam/MDA are development evaluation cases, not held-out superiority
proof. Capability evidence comes from Veeam's current federal/product pages.
Independent original-source research is compared with the captured ordinary
inputs; web-discovered records are not relabelled as automatic discoveries.
Report historical correctness tests separately from measured live lead time.
Production cutover and a fresh native release remain distinct acceptance steps.

## Approved integration follow-up, 2026-09-16

The user approved both the PROGRAM contract and the bounded Veeam DHS/MDA
capability input. Before integration, exact-scope setup revealed that the
curated `tools/agencies.py` catalog omits Missile Defense Agency. As a result,
the normal strategy/workstation resolver cannot represent the approved MDA
scope. Add one canonical MDA component under DoD, with the full agency-name
alias; test exact resolution, parent membership, and rejection of unrelated
Defense components. This repairs scope identity, not source availability.
Do not substitute a DoD-wide evaluation for the approved MDA case.

## Delivery-path finding before the follow-up projection edit

The final consumer trace found `external_product_projection.py:385` defaults
every non-forecast research subject to agency spending, and
`external_product_render.py:250` labels it an existing contract. The additive
PROGRAM subject would therefore be misrepresented in the final eight-slot
product even though native Press retains it correctly. Minimal follow-up:
PROGRAM research belongs in existing slot 7 (future demand, including budgets
and program signals), without a solicitation date or an award label. Keep
the eight locked headings, one-owning-slot rule, source binding and review
checks unchanged. Add an Assess -> Press -> eight-slot projection -> HTML
regression that does not bypass source/review equality.
