# Cycle 3 merge review · triage (2026-07-12)

Adversarial review of 4ae2a13...codex/assess-ledger-integrated at merged HEAD
24e70ef: 23 agents, 5 angles, refute-first verification. 17 CONFIRMED, 1
refuted. Full evidence (repro harnesses, quoted lines):
/private/tmp/claude-501/-Users-wtjohnson/f89f38ea-5b61-42aa-9b83-4aeac894c132/tasks/w60vwleh9.output

## LIVE NOW (fix before any press) — priority order

1. **CLOSED (Cycle 5)** · views.py:81 [Codex] Sales redaction garbled
   prospect-facing copy: the
   sweep substituted "Included in the full engagement" INTO third-party news
   headlines/URLs kept in the gated model. Client-visible defect on the
   teaser surface. The sales gate now drops the complete conflicting cited row
   before its three-item cap, backfills with safe rows, and preserves every
   retained headline and URL byte-for-byte while the physical leak gate holds.
2. **CLOSED (Cycle 5)** document.py:1161 + facts.py:908 [Codex] as_of default
   silently moved from local date.today() to utc_today(): deadline grading and
   date stamps shifted by a day for ALL legacy builds during US evening hours.
   The builders now keep local presentation stamps separate from the shared
   UTC operational cutoff. The presentation regression is closed; UTC
   comparisons remain explicit doctrine, and supplied dates replay both
   clocks exactly.
3. ui/index.html:1456 [Claude lane → active session] Plain "Approve results"
   always sends partial_release_approved:false — re-approving silently
   revokes a standing partial-release grant.
4. ui/server.py:41 [Claude lane → active session] ASSESS_RUN_DIR hardcoded
   while gate legs resolve via LILA_ASSESS_RUN_DIR — env-split desyncs the
   dashboard from the gates.

## DORMANT until pointer activation (fix before ANY cutover)

5. **CLOSED (Cycle 5 T2)** live_report.py:256 [Codex] A malformed scope
   designator now checks exact-client pointer existence before deciding state.
   Pointerless clients remain ABSENT and preserve legacy report truth; any
   existing client pointer still makes the unbindable sweep INVALID. Pointer
   directory read failures remain fail-closed.
6. release.py:241 [Claude lane → active session] Freshness leg treats
   NotADirectoryError/PermissionError on pointer.stat() as fail-closed for
   pointer-less approved clients; sibling leg (approval.py exists()) swallows
   them. Align to exists()-semantics: any OSError = absence.
7. release.py:240 [Claude lane → active session] TOCTOU: pointer statted
   BEFORE artifact = fail-OPEN order under a concurrent pointer refresh.
   Stat artifact first, or re-stat pointer after.
8. **CLOSED (Cycle 5 T2 · CONTRACT SURFACE)** approval.py [Codex]
   Content-identical reapproval now retains the exact approval artifact,
   timestamp, run identity, and pointer freshness. A same-binding transient
   failure of the explanatory evidence manifest preserves the prior manifest
   only when all other approval facts match. Real CURRENT-pointer regressions
   prove approval bytes, pointer bytes, and T5 identity remain unchanged;
   substantive approval changes still refresh normally.
9. **MEMO DELIVERED; WILLIAM DECISION PENDING** live_report.py [Codex +
   WILLIAM adjudication] `verdict_totals["discard"]` is repurposed for NO_BID
   in ledger mode. Exact NETSCOUT/Osprey deltas and both options are costed in
   `docs/plans/cycle5_items9_10_adjudication_memo.md`; no code changed.
10. **MEMO DELIVERED; WILLIAM DECISION PENDING** live_report.py [Codex +
    WILLIAM adjudication] `counts()["monitor_notices"]` narrows to
    market-research families in ledger mode. The same memo recommends
    preserving the frozen legacy semantics; no code changed pending William.

## QUALITY (schedule normally)

11. **CLOSED (Cycle 5 T2)** horizon_discovery/base.py [Codex] Offline Horizon
    mappers no longer call the mutable coverage writer. Mapper failures log,
    isolate, and emit no rows.
12. **CLOSED (Cycle 5 T2 · CONTRACT SURFACE)** horizon_discovery/base.py
    [Codex] Re-mapping or recomposing a stored successful sweep no longer
    refreshes coverage timestamps or counts. Only an actual collection attempt
    records success.
13. **CLOSED (Cycle 5 T2)** regulations_gov.py [Codex] Production collection
    now retains `agencyId` as buyer scope, and the real collector-to-Horizon
    path proves DHS-focused projection accepts only DHS-bound evidence.
14. **CLOSED (Cycle 5 T2 · CONTRACT SURFACE)** ledger.py [Codex]
    `regulations_gov` and `cisa_kev` now appear as required Horizon coverage
    rows and therefore enter the canonical blocker manifest. Their lack of
    run-bound completeness keeps them `PARTIAL`; the first refresh of an older
    run intentionally requires explicit operator reapproval of the new rows.
15. **CLOSED (Cycle 5 T2)** views.py [Codex] Teaming-only dossier cards no
    longer render an empty internal-QA block.
16. **CLOSED (Cycle 5 T2)** views.py [Codex] The sales pursuit-card renderer is
    explicitly pursuit-only; monitor copy remains owned by the established
    teaming-watch surface.
17. release.py freshness leg re-check ordering with run-id sidecars [active
    session] — supersedes items 6/7 once QA sidecars stamp run ids (Cycle 4
    item 1); prefer content identity over mtime entirely.

Routing: items marked [Claude lane → active session] belong to the session now
working the Cycle 4 handoff queue (commit 021a660 onward). [Codex] items go
into the Cycle 4 report-back as findings-to-fix. Nothing here blocks legacy
report production EXCEPT items 1-4.

## Addendum · Claude lane status + second-run corroboration (2026-07-12, same session)

Claude-lane items are CLOSED, suite 799 passed / 1 skipped:

- Items 3, 4 (LIVE NOW) fixed in 60b12fe: plain Approve now sends no partial
  key; absent preserves an unrevoked standing grant verbatim (explicit false
  still revokes; drifted grants stay inert via the existing binding);
  ui/server.py resolves LILA_ASSESS_RUN_DIR exactly like the gate legs.
  NOTE for William: the running Control Room needs a restart (his call) to
  pick up the server-side halves; until then behavior is exactly pre-fix.
- Items 6, 7 (dormant-leg) fixed in 1b854a6: pointer absence now follows
  Path.exists() semantics (corrupt runs store keeps legacy clients
  releasable; EACCES still fails closed), and the freshness leg consumes the
  scan-time artifact mtime (artifact-before-pointer stat order: concurrent
  pointer refresh now fails closed, mid-request regeneration cannot ride the
  old build's verdict).
- Item 17 landed as Cycle 4 item 1 (70abdd8): stable-family QA sidecars stamp
  assess_run_id and release_state requires the exact current pointer run id.

A second independent review workflow (20 agents, refute-first, this session)
re-confirmed the above and the open [Codex] items, and surfaced FOUR findings
not in the list above — add to the Cycle 4 report-back:

18. **CLOSED (Cycle 5 T2)** ui/server.py [Codex] Assess approval and revocation
    responses now add `refresh_note` with the creation-free ledger-refresh
    outcome while preserving the durable human action and existing response
    keys. Failures remain HTTP 200 for the saved gate action, log loudly,
    leave the pointer untouched, and hold stale CURRENT projection inputs
    `INVALID`; the regression uses a real immutable run and CURRENT pointer.
19. **CLOSED (Cycle 5 T2 · CONTRACT REPAIR)** views.py [Codex] Pointerless
    zero-triage reports again render the legacy pursuit landscape even when a
    SAM census is present. Strict held/current reconciliation and census lint
    behavior remain intact.
20. **CLOSED (Cycle 5 T2 · CONTRACT SURFACE)** horizon_discovery/base.py
    [Codex] An absent stored source or stored error envelope no longer
    overwrites the last real coverage result. Missing stored payload is not a
    new failed pull.
21. **CLOSED (Cycle 5 T2)** horizon_discovery/federal_register.py [Codex]
    Federal Register reserved metadata keys are excluded before keyword-lane
    mapping; an `errors` payload cannot fabricate PROGRAM evidence.

Second-run evidence: journal at
~/.claude/projects/-Users-wtjohnson/84adc182-7967-4ca6-b10b-712dcb539bd4/subagents/workflows/wf_b79e5520-e25/journal.jsonl

## Addendum 2 · post-press findings (2026-07-12 morning, QB lane)

22. **CLOSED (Cycle 5 T2)** agents/assess/parity.py:905 [Codex] the hermetic
    LILA_ENTITIES_DIR override leaked into current-pointer validation. The
    temporary directory now covers only offline document projection; it is
    restored before CURRENT validation hashes the operator's real crosswalk.
    An unstubbed CLI regression builds and preserves a real pointer, runs with
    no pre-set entity override, and proves the pointer remains CURRENT without
    the false review/reference-input hold. A second regression proves a
    caller-supplied entity directory is restored and receives no projection
    traffic.
23. FIXED (Claude lane, same morning): every artifact runner and Control
    Room gate hook called materialize_current_assess_run unconditionally,
    and persist mints the scope pointer, so any sweep was an IMPLICIT
    CUTOVER (proved live: the 08:36 NETSCOUT DHS sweep created
    agency_dhs.current.json without an operator activation decision).
    Creation-free owner landed at tools/assess_refresh.py; all six call
    sites rewired; operator activation is an explicit CLI act. The server
    hook also now logs failed refresh outcomes (the observability half of
    item 18; the response-note half stays with Codex).
24. **OPEN (QB lane · pre-existing P1)**
    `tools/assess_refresh.py:76` checks pointer existence before calling the
    materializer, while `agents/assess/ledger.py:2279` later replaces the
    pointer unconditionally. If an operator removes or replaces it between
    those operations, refresh can reverse the rollback. Present at mandated
    base `ae454ee`; the Codex lane did not introduce or widen it. Carry and
    revalidate the exact expected pointer identity into persistence, pin
    concurrent removal/replacement, and use a shared per-scope lock for the
    strict syscall-level guarantee.

## Addendum 3 · item 23 (2026-07-12)

23. **CLOSED (same day)** tools/assess_refresh.py:76 + ledger persistence
    [QB lane; found by Codex Tranche 2-3 adversarial review, P1]: an operator
    pointer removal or replacement between the refresh existence check and
    persistence was recreated/clobbered, reversing the rollback. Repaired via
    compare-and-swap: the refresh threads its observed pointer bytes into
    persist_assess_run, which refuses to write on mismatch. Codex's minimal
    regression plus a replacement twin are pinned in test_assess_refresh.py.
    Activation stays the only expectation-free path.
