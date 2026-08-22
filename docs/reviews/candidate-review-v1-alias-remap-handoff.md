# Candidate Review v1 — evidence alias remap: implementation handoff

Written 2026-07-23. Self-contained: a fresh session should be able to implement
from this without re-deriving anything.

**Worktree:** `/Users/wtjohnson/Documents/Codex/2026-07-21/i/worktrees/lila-candidate-review-v1`
(branch `codex/candidate-review-v1`, all work uncommitted).
**Tests:** `PYTHONDONTWRITEBYTECODE=1 /Users/wtjohnson/federal-sales-os/.venv/bin/python -m pytest tests/test_candidate_review_v1_*.py -q -p no:cacheprovider`
(there is no local `.venv`; use the sibling one). Current baseline: **889 passing**.

---

## 1. What the alias remap is

`build_candidate_inventory` (`agents/candidate_review_v1/candidate_engine.py:336`)
does not accept the evidence you hand it verbatim. It first **canonicalizes**
(`_canonicalize_evidence`, `candidate_engine.py:411-478`):

1. Group every input `EvidenceRecord` by `source_identity.canonical_key`
   (`:437-439`).
2. All rows sharing an identity must have the same semantic fingerprint, else it
   raises `conflicting observations share exact source identity` (`:446-450`).
3. **The survivor is `rows[0]` after sorting by `evidence_id`** — i.e. the
   lexicographically smallest evidence id wins (`:445, :451`).
4. The survivor is rebuilt with merged `supports` (order-preserving union),
   `retrieved_at = max(...)`, `verified_at = max(non-None)` (`:452-465`).
5. Every collapsed row becomes an `EvidenceAlias(alias_evidence_id,
   canonical_evidence_id, source_identity_key)` (`:467-474`).

The result is `CandidateBuildResult.evidence` (canonical only) plus
`CandidateBuildResult.evidence_aliases`, exposed as the property
**`evidence_alias_map -> dict[alias_id, canonical_id]`** (`candidate_engine.py:322-326`).

**Rule:** any id *not* in the map is already canonical. Remapping one id is
`alias_map.get(old_id, old_id)`.

The engine remaps its own **seeds** internally. It does **not** touch anything
else you plan to hand the composer.

## 2. Why it matters — the silent-hollowing failure

`compose_candidate_review_document` builds its evidence pool from
`candidate_build.evidence` (canonical) and resolves every object's evidence
references against it (`composer.py:169 resolves()`). An object still pointing at
an **alias** id does not resolve, and the composer **drops it without raising**:

| Surface | Drop site |
|---|---|
| vehicle signal | `composer.py:211-212` |
| vehicle watch record | `composer.py:231-232` |
| claim (pattern / market / past award) | `composer.py:258-259` |
| search concepts | `composer.py:266` |
| execution framework | `composer.py:270` |
| calendar event | `composer.py:281-282` |
| coverage row (`accepted_evidence_ids`) | `_reconcile_coverage`, "accepted evidence not in pool" |

(Candidates are the exception — they come from the build already canonical, and
an unresolved candidate ref raises `ComposerError` at `composer.py:177`.)

So skipping the remap produces a **structurally valid, fully certified, entirely
plausible report that is quietly missing its events, vehicle signals, narrative
claims, keywords, and method section.** This is the single most likely way a
wired driver ships a hollow deliverable. It is exactly the "looks complete,
needs manual repair" failure mode this build exists to eliminate.

**Current state:** `run_candidate_review.py:44 _guard_no_alias_remap(...)` refuses
to run when an alias exists and there is anything to remap (called at `:157-158`).
That guard is a placeholder — it fails loudly instead of hollowing. Implementing
the remap means **replacing** that guard, not adding to it.

## 3. Surfaces that must be remapped

Everything the driver passes to `build_calendar` /
`project_vehicle_watch_coverage` / `compose_candidate_review_document` that
carries evidence ids minted **before** canonicalization:

1. `verified.event_seeds` — `EventSeed.evidence_ids`, `registration_evidence_ids`,
   `agenda_evidence_ids`, `venue_evidence_ids`, `attendance_evidence_ids`,
   `flagship_evidence_ids`, and nested `timing.evidence_ids`.
2. `verified.vehicle_signals` — `VehicleSignal.evidence_ids`, nested
   `vehicle.evidence_ids`, `relationship.evidence_ids`, `dates[].evidence_ids`.
3. `verified.vehicle_watch_records` — every `*_evidence_ids` field
   (`identity_`, `management_`, `scope_`, `status_`, `access_`, `official_`),
   `dates[].evidence_ids`, and `ceiling_or_value.evidence_ids`.
4. `authored.pattern_claims`, `authored.market_signals`, `authored.past_awards`
   — `EvidenceBoundClaim.evidence_ids`.
5. `authored.search_concepts` — `SearchConcepts.evidence_ids`.
6. `authored.execution_framework` — `ExecutionFramework.evidence_ids`.
7. `verified.accepted_vehicle_evidence_by_query` — **holds `EvidenceRecord`
   objects, not ids.** Each aliased record must be *replaced by the canonical
   record object* from `candidate_build.evidence`, then deduped.

Caller-supplied `kpi_tiles` / `ticker_items`, if ever passed, carry
`evidence_ids` + `source_evidence_id` and need the same treatment.

## 4. Implementation spec

New module: **`agents/candidate_review_v1/alias_remap.py`** (additive, pure,
no imports from the driver).

```python
def remap_evidence_ids(model: BaseModelT, alias: Mapping[str, str]) -> BaseModelT
def remap_all(models: Sequence[BaseModelT], alias) -> tuple[BaseModelT, ...]
def remap_accepted_evidence(
    accepted: Mapping[str, Sequence[EvidenceRecord]],
    alias: Mapping[str, str],
    canonical_by_id: Mapping[str, EvidenceRecord],
) -> dict[str, tuple[EvidenceRecord, ...]]
```

Approach: dump → rewrite → re-validate.

1. If `alias` is empty, **return the object unchanged** (identity, no rebuild).
2. `data = model.model_dump(mode="python")`.
3. Walk `data` recursively. Rewrite:
   - any dict key ending in `evidence_ids` whose value is a list/tuple of `str`;
   - any key exactly `evidence_id` or `source_evidence_id` whose value is `str`.
4. `type(model).model_validate(data)` and return.

### Four things that will bite you

- **Dedupe after mapping, order-preserving.** Two aliases can collapse to the
  same canonical id. Most of these tuples have a `_unique(...)` validator
  (e.g. `EvidenceBoundClaim._claim_is_three_part_and_bound`,
  `TickerItem._ticker_is_traceable`, `VehicleSignal`), so a naive map produces a
  duplicate and the model raises. Use `tuple(dict.fromkeys(mapped))`.
- **`min_length=1` fields survive** — mapping never empties a non-empty tuple,
  so `EvidenceBoundClaim.evidence_ids`, `TickerItem.evidence_ids`, and
  `VehicleSignal.evidence_ids` stay legal. Do not "helpfully" drop empties.
- **`TickerItem.source_evidence_id` must remain inside `evidence_ids`** after the
  rewrite (validator at `contracts.py:2666`). Because both are mapped with the
  same table this holds automatically — but assert it in a test.
- **Do NOT rewrite `date_id` / `money_id`.** The deterministic author namespaces
  them as `f"{evidence_id}:{kind}"` (`authoring.py`), so after collapse two dates
  may keep distinct ids while pointing at one canonical record. That is correct
  and collision-free; only `evidence_ids` must resolve. Rewriting the id prefix
  would *create* collisions in the composer's global date/money namespace
  (`composer.py:194-201`).

### Driver change

In `run_candidate_review.py`:
- Delete `_guard_no_alias_remap` (`:44`) and its call (`:157-158`).
- After `alias = candidate_build.evidence_alias_map`, remap all seven surfaces
  and pass the remapped values onward to `project_vehicle_watch_coverage`,
  `build_calendar`, and `compose_candidate_review_document`.
- Keep `evidence=()` in the compose call — `candidate_build.evidence` is already
  the canonical universe, and re-adding a record that reuses a source identity is
  a hard `ComposerError` (`composer.py:350-356`).

## 5. Tests to write (`tests/test_candidate_review_v1_alias_remap.py`)

1. Empty alias map returns the identical object (no rebuild).
2. A single aliased id is rewritten in a nested model (`EventSeed.timing.evidence_ids`).
3. **Two aliases collapsing to one canonical are deduped** and the model still
   validates (the headline regression).
4. Ids absent from the map are untouched.
5. `TickerItem.source_evidence_id` stays inside `evidence_ids` after remap.
6. `remap_accepted_evidence` swaps alias records for canonical record objects and
   dedupes.
7. **Integration (the one that matters):** build evidence where two records share
   a `source_identity.canonical_key` so one becomes an alias; attach a claim and
   an event seed that reference the alias id; run the driver/compose; assert the
   claim and event are **present in the document**, not dropped. Without the
   remap this test fails silently-by-absence — which is the whole point.
8. Driver no longer raises when an alias exists.

## 6. Plan once the remap is done

Ordered; each step is independently reviewable.

1. **Live fetchers** — new `agents/candidate_review_v1/live_fetchers.py` providing
   `fetch_vehicle(lead)` (routes by `lead.lane`: SAM notice detail; USAspending
   award detail; agency forecast page; GSA eLibrary) and `fetch_event(lead)`
   (HTTP GET, follow redirects, return canonical `final_url` + page text), over
   the existing `tools/api` clients. Must **return `None`, never raise**;
   hash bytes into `record_sha256`; **pre-dedupe leads by canonical key before
   fetching** to protect SAM quota; reuse `_http` retry/timeout and `sam_quota`.
2. **Move notice verification into the watch run** (the 24h trap). A current
   notice needs `verified_at <= as_of` *and* `as_of - verified_at <= 24h`, and
   `as_of` is the watch generation's timestamp — so verifying at report time is
   structurally impossible. Verify during collection, persist the notice block
   into the generation, and have the document driver read it rather than
   re-fetch. Stale actives are dropped by design (`authoring.py` handles this).
3. **Driver live mode** — `--live` passes the real fetchers; default stays
   zero-network/zero-LLM.
4. **Command Center step** — one branch in `ui/server.py::_step_cmd` named
   **`candidate_review`**, NOT `pre_assessment` (that id is an existing artifact
   family keyed by `_report_rows`/`_status_banner`; reusing it desyncs the UI).
   It runs `run_candidate_review_watch.py --client X` then
   `run_candidate_review.py --client X` over the existing
   `POST /api/run` → `GET /api/job/<id>` contract. Approval gating and the
   release switch stay untouched; DRAFT remains the resting state.
5. **First live pull**, operator-triggered, one client. Expect the event parser
   to drop a lot of real organizer pages (it currently requires published
   `Organizer:` / `Location:` strings) — iterate it against real HTML.
6. **Then:** Pass C (vehicle signals/watch records → Section 3), Max-plan prose
   enrichment (the `authoring.py` adapter path, currently raising), the
   `_select_diverse` contract-surface fix (below), golden fixtures for the three
   clients plus an unseen fourth, and cutover.

## 7. Contract-surface fix still owed (separate approved diff)

`candidate_engine.py:989-1016 _select_diverse` returns candidates **out of ranked
order**: the diversity pass selects, then the back-fill pass *appends* at the end,
while `expected_visible` is derived from the ranked inventory in rank order — so
`CandidateBuildResult` raises `visible candidates differ from ranked inventory
content`. Repro: ranked `[(A,.90),(A,.80),(B,.70),(A,.60)]`, `maximum=3` →
returns `(A1,B1,A2)`, expected `(A1,A2,B1)`. Fix is a one-line re-sort into
ranked order before returning. `candidate_engine.py` is a **contract surface**:
ship it as its own labeled diff with the repro as its regression test, per the
session protocol. Currently masked because the author caps at 12 seeds, which
takes the `len(ranked) <= maximum` early return.
