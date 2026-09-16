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

## Operating cutover and authorized run

Implementation commit `c527408f040d4db759c6c5e19a5ca32b3100b121` was
fast-forwarded into operating local `main`. All four protected file hashes
above were unchanged immediately after integration. No remote push performed.
The full-suite XML SHA-256 is
`89a0ee8e6212de893e4c5635a67ac0bfd566d2cc023d105c948af43233eb688b`.

The existing `request_approval` -> `set_scope` -> `decide` machinery recorded
the approved Veeam strategy, with the direct user authorization in its journal
and decision note. Decision timestamp: `2026-09-16T17:42:10.009618+00:00`.
The imported strategy's numeric confidence is deliberately 0 (unscored), not
a fabricated calibrated estimate. Unknown NAICS rationales remain unfilled.
The generated-client-file diagnostic describes empty generated defaults, not
the retained hand-authored files; native loaders verified the three approved
files, and the ordinary search accepted them without changing any gate.

Input hashes:

- Review packet: `286a3a0c37cc97c1d975fb864c5fbc0e786f66a2ed8f51f9c2d2ec3860ae9aa8`
- Profile: `28c47e12c7b7824bb3e876dcb2871d064b61aa77d4efbe9b94ab8108a254f91c`
- Taxonomy: `db8e62a8c9acd85beb4af3aed985c49bfbc68c6b376be403b693492f929328b3`
- Engagement scope: `64f80898116467ded78e80cfb1b34a2b00d34a43bed3572404ebfdb1062ec7a1`

Command Center started only from `/Users/wtjohnson/Lila`; `/api/build` returned
revision `c527408`, `stale=false`, and the correct operating root. Authorized
request: `POST /api/run` with `client_name=Veeam`, `step=searches`,
`args.workstation_id=agency_dhs_mda`. Job `8337fcb395b5` started
`2026-09-16T17:42:26+00:00`; its durable log is
`data/state/job_logs/8337fcb395b5.log`. No strategy expansion, metered contact
enrichment, outgoing message, Assess approval or client release was performed.

## Ordinary run result: collected evidence, not certified lead improvement

Job `8337fcb395b5` completed with return code 0 after 894.2 seconds. This is
process completion, not complete source coverage or successful synthesis.
Collection took 591.7 seconds; the Research Picture Claude invocation timed
out after 300 seconds and its error was retained rather than presented as a
completed assessment.

Saved ordinary sweep:
`data/cleaned/searches_veeam.agency_dhs_mda.json`, generated
`2026-09-16T17:57:18+00:00`, SHA-256
`bf6b01b7b41abed437e98b2260b7cf8c02bfa47681959001fb5261f4e56a30ef`.

- SAM daily extract: 84,504 active rows screened, 23 DHS/MDA candidates.
  Six records were attachment-enriched, with zero newly relevant records.
  Dispositions: 10 weak-fit discards, 2 historical-award notices, 9 incomplete
  text records and 2 unresolved-context records. The last 11 remain unscreened,
  not proven irrelevant. The two unresolved-context records concern asbestos.
- Forecasts: 991 parsed rows retained before relevance filtering, 2 strict
  matches and 4 research candidates. These are the same four candidates found
  in the frozen evaluation, not a measured increase in useful lead yield.
- Watchdog: 29 RSS reports retained, zero keyword matches. This is feed-level
  retrieval, not a complete agency or oversight archive search.
- Internal coverage: 406 agency/component-family rows (58 labels x 7 families).
  Agency and component scopes overlap; counts must not be summed into a market
  total. MDA's unknown family counts remain null, not zero opportunities.
- The run recorded 13 blocking source-coverage gaps. Missing API credentials,
  partial USAspending results, source access/schema failures and the synthesis
  timeout remain limitations. A completed adapter call is not coverage proof.

## Native Assess and internal Press projection

Activated the native Assess pointer through
`python -m tools.assess_refresh --client Veeam --activate`. Run ID:
`assess:v3:b47ae8df79cfcb9c5fae963a06f8a04c28e164bfe4e894dabf76cfa3670468a4`.
Pointer: `data/state/assess_runs/veeam--dd62c72b6a5e/agency_dhs_mda.current.json`.
The run-addressed Assess artifact SHA-256 is
`6fc842ddb7daf25a6ca87241e064ecc3febb45b8694624ab667f89b8f6239e60`.
The API reported `state=current`, `approval_status=pending`, `can_release=false`.
Pointer activation is not Assess approval.

Assess retained 19 canonical live records and 7 research subjects:

- Four APFS forecasts: ICE/HSI Data Backup Equipment and Support Services
  (73283), USCIS Enterprise Contact Center Experience 2 (74826), CBP Digital
  Forensic Enterprise Collection and Analysis Tool and Support (75200), and
  TSA Servers purchase (71624). Three estimated solicitation dates are already
  past. Published POCs are present, but current buying decisions and product
  demand still require investigation; hardware or a disaster-recovery service
  clause does not establish a new Veeam software purchase.
- Two historical USCIS awards explicitly reference Veeam software licenses or
  maintenance. They establish existing use, not a new buy or a recompete.
- One PROGRAM research false positive: FEMA State-Administered Direct Housing
  Grant, `reginfo:1660-AB17:202510`. The matched text is "Disaster Recovery" in
  the Disaster Recovery Reform Act, describing temporary housing, not digital
  recovery. Its source and exact match span survived to Assess. It was not
  promoted to a lead. This exposes a context-screening repair still needed;
  it must not be counted as a relevant early buying need.

An INTERNAL native Press projection used that Assess run with no target actions
and no external review/release directory. It produced 26 assessment parents,
0 buying-action children and 0 qualified leads. The zero is not proof that the
scope contains no opportunities: source coverage and screening are incomplete,
synthesis failed, and contact/target investigation has not been supplied.
Neither real-company precision, recall nor pre-solicitation lead-time
improvement has been established by this run.

Internal artifacts are under `data/review/upstream-060-veeam-internal/`:

- `native-press-receipt.json`: `5713e414faab1f210bcc339b914be8412dc1052b29400722d4fb9592c5d8e21a`
- `INTERNAL-native-preview.html`: `bb3c0d390a0748ff1bd686535e79f5a667a71afdc4ed73fe3d1ecc9b9bbc5cbd`
- `post-assess-coverage.json`: `80189713667ae19d7ba6c4289061d4ee18724c19b8d15649309a74ede82f388e`

The internal HTML passed em-dash lint. No rendered-browser QA or external
eight-slot client release is claimed. The existing full product path remains
preserved; this diagnostic preview does not replace it.

## Run side effects and remaining work

The ordinary workflow recorded 31 contact observations and added 13 internal
outreach-list candidates (list total 14). No messages were sent and no paid
enrichment was requested. Internal list inclusion is not contact approval,
buying authority, or proof of a qualified lead.

The three NETSCOUT file hashes still match the preservation baseline. Entity
resolution changed the already-dirty `data/entities/unresolved.log` to SHA-256
`c7be51e70722038bf4a268231b551699ce2d0c4629aa6b950bfe6a33994061da`.
The existing logger rewrites a JSON map and increments unresolved-name counts;
it is not an append-only text log. The pre-run bytes were not separately saved,
so exact semantic preservation relative to that dirty baseline is unverified.
The log was not staged, restored or committed. Other user files and protected
development `tmp/` remain outside this commit.

Highest-impact next repairs/evaluation:

1. Resolve physical versus digital recovery context with paired regression
   examples, retaining genuinely adjacent technical needs without broad
   negative-keyword suppression.
2. Recover source requirement text and disposition each of the 11 unscreened
   notices; retry synthesis from captured evidence rather than recollecting
   everything or interpreting its failure as no demand.
3. Investigate the strongest APFS candidate and existing-use award context into
   a current, evidence-linked buying hypothesis and role-specific conversation.
   Do not require confirmed funding to retain a hypothesis or turn a published
   POC into assumed buying authority.
4. Establish a real MDA primary-source sample and measured collection path.
   Compare independent scope-matched research with the ordinary workflow before
   claiming recall, earlier discovery, or useful lead-yield improvement.

Acceptance reached: approved contracts/configuration integrated locally,
regression verified, and evidence carried through an ordinary collection and
native Assess run into internal projection. Acceptance not reached: demonstrably
better actionable early leads. No remote push or client release was performed.
