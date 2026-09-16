# UPSTREAM-LEADS-060: approved operating integration

Date: 2026-09-16. Work ID: UPSTREAM-LEADS-060.
Plan: LILA-WEEKEND-2026-09-11-v1, SHA-256
`71a4c7c11ee575596357e5e0fcfb66a75d2c855b4761040042732c8cad26bfc5`.

## Authority and scope

The user's direct response, **"approved on both"**, approves the additive
ResearchSubject v2 PROGRAM contract and the bounded Veeam DHS/MDA capability
input presented in the preceding handoff. Input file:
`veeam-capability-input.json`, original SHA-256
`dc81758f4bbeb8318e10f2bb8e9858aa4131ef5e6473b94e2cab994e7223e7c0`.
The original evaluation file is retained unchanged. Its prior development-only
status describes the time it was authored; this receipt records the subsequent
approval. No downstream Assess/Target/release approval is inferred.

The approved profile/taxonomy are installed for Veeam. Exact engagement scope is
DHS plus Missile Defense Agency, not all Defense. Procurement-code boundaries
are search lenses, not eligibility claims. Source-published contacts do not
prove buying authority. No paid contact enrichment or outreach is authorized
by this receipt.

## Preservation baseline

Operating main: `8f19f7f5abaae7a425267bbbb2c45ee99c32f95f`.
Development branch: `codex/upstream-leads-20260916`.
Protected operating file hashes before integration:

- `clients/netscout/profile.json`: `4c43f61c4c67eabd961136dcc415f8280974fef6d2d9d0bb7e399106def49c2a`
- `clients/netscout/capability_taxonomy.json`: `64360dfb67abdd260efa07a8c7e2a3812491a83db2d840a75db3ef48cedf0425`
- `clients/netscout/engagement_scope.json`: `0f8a6f22f1b616ff4b891a213e02cfc2bb928d9423c69ebbca6551535f17f1c4`
- `data/entities/unresolved.log`: `17ed3d14df94f1728cd67e3cdada21171cd7acbe69fd9e8c3c3f8ae2894c8caa`

Development `tmp/`, other worktrees and frozen calibration 005 are preserved.
The prior verification receipt remains an exact historical development result.
Fresh integration verification and ordinary-run receipts follow below when
executed; approval alone is not evidence of a completed run or improved yield.

## Integration preparation checks

- Live GitHub main still matched the local baseline before integration.
- MDA was missing from the agency catalog. The bounded catalog repair and
  exact-scope exclusion regression are included; this does not add an MDA feed.
- Targeted upstream/scope/workstation regression: 88 passed in 2.24 seconds.
- Profile and taxonomy load through the native loaders. Exact scope accepts
  DHS/MDA and rejects Army/VA; canonical workstation is `agency_dhs_mda`.
- The sandbox-only full regression attempt produced 8 local Chromium/PDF launch
  failures, 6,138 passes and 114 skips. No assertions were changed. It is not
  a passing receipt; the permission-correct full rerun is recorded separately.
- This shell/operating checkout has no configured SAM, Congress, GovInfo or
  regulations.gov API keys. Claude CLI is present and the default route is
  unchanged (`max`). The existing local notice store is present. These are
  execution-availability observations, not new permissions or coverage claims.

Permission-correct full strict offline regression: **6,220 passed, 40 skipped,
22 warnings, 26 subtests passed**, 136.45 seconds. Receipt:
`tmp/upstream-060-approved-suite-unrestricted.xml`. Command:

```sh
LILA_SUITE_OFFLINE=1 LILA_SUITE_STRICT=1 /Users/wtjohnson/Lila/.venv/bin/python -m pytest tests/ -q --tb=short --junitxml=tmp/upstream-060-approved-suite-unrestricted.xml
```

No test assertion or skip condition was weakened. This establishes regression
and fixture behavior, not real-client useful lead yield.
