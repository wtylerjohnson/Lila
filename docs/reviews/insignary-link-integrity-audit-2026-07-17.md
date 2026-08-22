# Insignary Link Integrity Audit · 2026-07-17

This audit ran the Link Integrity Gate against the stored Insignary Signal
Board with render date 2026-07-17. It examined 39 rendered outbound-link
occurrences (19 unique URLs) plus the non-rendered NASA provenance URL used by
the AGENCY_DOC claim-visibility check.

Final rendered SHA-256 after rebasing and closing the merged gate-order review:
`8cf87a1ca883f90c9d0cef6aae7e03bf5564ff310184e9d9eff10686a8f5a3d2`.

The press was correctly blocked. No external link was classified DEAD. The
blocking finding is a SAM notice GUID used in three board locations that has
no matching primary record in the stored sweep:
`1e9c531df5a841c2935f370db3743213`. The gate did not substitute the different
ODOS extension notice present in the sweep. No HTTP result was used as proof
for either SAM.gov or USAspending.

Summary:

- USAspending: 24 occurrences, all `BUILDER-DERIVED+RECONCILED`; no HTTP calls.
- SAM.gov: 11 occurrences; 8 `BUILDER-DERIVED+RECONCILED`, 3
  `BUILDER-DERIVED+BLOCKED`; no HTTP calls.
- Rendered external links: 2 `OK`, 0 `DEAD`, 2 `UNVERIFIABLE`.
- AGENCY_DOC provenance: NASA news release `OK`; `$20B` is visible in fetched
  page text, so no claim-visibility warning was emitted.
- Workspace URLs remaining: 0.
- A fresh empty audit cache received 5 external results on 2026-07-17,
  including the provenance-only NASA source.

## Legacy stored-artifact scan

The bootstrapped, gitignored `data/reports/` copy still contains 299
`sam.gov/workspace` occurrences across 17 historical HTML files. The tracked
visual reference at `docs/reference/federal_opportunity_signals.reference.html`
contains another 8. The intentional eight-link regression fixture is excluded
from those counts. These historical files were not rewritten in place; every
current release/export/download boundary now normalizes and gates its final
bytes, so a legacy artifact cannot pass through as a clean client download.
The newly rendered Insignary board audited below contains zero workspace URLs.

## Every rendered outbound link

| # | Section | URL | Class | Proof |
|---:|---|---|---|---|
| 1 | How the total was built | https://www.usaspending.gov/award/CONT_AWD_1333BJ25F00280001_1344_47QTCK18D0036_4732 | BUILDER-DERIVED+RECONCILED | `usaspending_award`; generated ID present, fresh, and undisputed |
| 2 | How the total was built | https://www.usaspending.gov/award/CONT_AWD_70SBUR22F00000113_7003_HHSN316201200193W_7529 | BUILDER-DERIVED+RECONCILED | `usaspending_award`; generated ID present, fresh, and undisputed |
| 3 | How the total was built | https://www.usaspending.gov/award/CONT_AWD_36C10B25F0304_3600_47QTCA23D00BT_4732 | BUILDER-DERIVED+RECONCILED | `usaspending_award`; generated ID present, fresh, and undisputed |
| 4 | Relevant research signals | https://www.usaspending.gov/award/CONT_AWD_1333BJ25F00280001_1344_47QTCK18D0036_4732 | BUILDER-DERIVED+RECONCILED | `usaspending_award`; generated ID present, fresh, and undisputed |
| 5 | Relevant research signals | https://sam.gov/opp/1e9c531df5a841c2935f370db3743213/view | BUILDER-DERIVED+BLOCKED | `sam_notice`; exact GUID absent from stored primary records |
| 6 | Relevant research signals | https://www.usaspending.gov/award/CONT_AWD_36C10B25F0304_3600_47QTCA23D00BT_4732 | BUILDER-DERIVED+RECONCILED | `usaspending_award`; generated ID present, fresh, and undisputed |
| 7 | Relevant research signals | https://www.usaspending.gov/award/CONT_AWD_70B04C25F00001054_7014_NNG15SC77B_8000 | BUILDER-DERIVED+RECONCILED | `usaspending_award`; generated ID present, fresh, and undisputed |
| 8 | Relevant research signals | https://www.usaspending.gov/award/CONT_AWD_HR001124C0488_9700_-NONE-_-NONE- | BUILDER-DERIVED+RECONCILED | `usaspending_award`; generated ID present, fresh, and undisputed |
| 9 | Relevant research signals | https://sam.gov/opp/b208e33b8dcd49088d751842a5f462aa/view | BUILDER-DERIVED+RECONCILED | `sam_notice`; GUID present, fresh, and undisputed |
| 10 | Relevant research signals | https://sam.gov/opp/ae337f5d01e3499b980d347fa7567323/view | BUILDER-DERIVED+RECONCILED | `sam_notice`; GUID present, fresh, and undisputed |
| 11 | Best-fit opportunities | https://www.usaspending.gov/award/CONT_AWD_1333BJ25F00280001_1344_47QTCK18D0036_4732 | BUILDER-DERIVED+RECONCILED | `usaspending_award`; generated ID present, fresh, and undisputed |
| 12 | Best-fit opportunities | https://www.usaspending.gov/award/CONT_AWD_1333BJ25F00280001_1344_47QTCK18D0036_4732 | BUILDER-DERIVED+RECONCILED | `usaspending_award`; generated ID present, fresh, and undisputed |
| 13 | Best-fit opportunities | https://www.usaspending.gov/award/CONT_AWD_1333BJ21F00280023_1344_47QTCK18D0036_4732 | BUILDER-DERIVED+RECONCILED | `usaspending_award`; generated ID present, fresh, and undisputed |
| 14 | Best-fit opportunities | https://www.usaspending.gov/award/CONT_AWD_70SBUR22F00000113_7003_HHSN316201200193W_7529 | BUILDER-DERIVED+RECONCILED | `usaspending_award`; generated ID present, fresh, and undisputed |
| 15 | Best-fit opportunities | https://www.usaspending.gov/award/CONT_AWD_70SBUR22F00000113_7003_HHSN316201200193W_7529 | BUILDER-DERIVED+RECONCILED | `usaspending_award`; generated ID present, fresh, and undisputed |
| 16 | Best-fit opportunities | https://sam.gov/opp/1e9c531df5a841c2935f370db3743213/view | BUILDER-DERIVED+BLOCKED | `sam_notice`; exact GUID absent from stored primary records |
| 17 | Best-fit opportunities | https://www.usaspending.gov/award/CONT_AWD_36C10B25F0304_3600_47QTCA23D00BT_4732 | BUILDER-DERIVED+RECONCILED | `usaspending_award`; generated ID present, fresh, and undisputed |
| 18 | Best-fit opportunities | https://www.usaspending.gov/award/CONT_AWD_36C10B25F0304_3600_47QTCA23D00BT_4732 | BUILDER-DERIVED+RECONCILED | `usaspending_award`; generated ID present, fresh, and undisputed |
| 19 | Competitor lanes | https://www.usaspending.gov/award/CONT_AWD_70SBUR25F00000195_7003_NNG15SD08B_8000 | BUILDER-DERIVED+RECONCILED | `usaspending_award`; generated ID present, fresh, and undisputed |
| 20 | Competitor lanes | https://www.usaspending.gov/award/CONT_AWD_70SBUR25F00000194_7003_HHSN316201500040W_7529 | BUILDER-DERIVED+RECONCILED | `usaspending_award`; generated ID present, fresh, and undisputed |
| 21 | Competitor lanes | https://www.usaspending.gov/award/CONT_AWD_1333BJ24F00282021_1344_NNG15SD74B_8000 | BUILDER-DERIVED+RECONCILED | `usaspending_award`; generated ID present, fresh, and undisputed |
| 22 | Competitor lanes | https://www.usaspending.gov/award/CONT_AWD_1333BJ26F00282001_1344_NNG15SD26B_8000 | BUILDER-DERIVED+RECONCILED | `usaspending_award`; generated ID present, fresh, and undisputed |
| 23 | Competitor lanes | https://www.usaspending.gov/award/CONT_AWD_70B04C25F00001054_7014_NNG15SC77B_8000 | BUILDER-DERIVED+RECONCILED | `usaspending_award`; generated ID present, fresh, and undisputed |
| 24 | Prospective horizon | https://www.darpa.mil/research/programs/enhanced-sbom-for-optimized-software-sustainment | OK | GET 200, terminal URL unchanged |
| 25 | Prospective horizon | https://sam.gov/opp/b208e33b8dcd49088d751842a5f462aa/view | BUILDER-DERIVED+RECONCILED | `sam_notice`; GUID present, fresh, and undisputed |
| 26 | Prospective horizon | https://sam.gov/opp/1e9c531df5a841c2935f370db3743213/view | BUILDER-DERIVED+BLOCKED | `sam_notice`; exact GUID absent from stored primary records |
| 27 | Prospective horizon | https://www.sewp.nasa.gov/sewpvi/ | OK | GET 200, terminal URL unchanged |
| 28 | Prospective horizon | https://sam.gov/opp/b208e33b8dcd49088d751842a5f462aa/view | BUILDER-DERIVED+RECONCILED | `sam_notice`; GUID present, fresh, and undisputed |
| 29 | Prospective horizon | https://sam.gov/opp/49ca8b1859c74fe28a4e9d3ae1c83d68/view | BUILDER-DERIVED+RECONCILED | `sam_notice`; GUID present, fresh, and undisputed |
| 30 | Prospective horizon | https://sam.gov/opp/692f3f7143d84b2da163ab1bcdd73b0b/view | BUILDER-DERIVED+RECONCILED | `sam_notice`; GUID present, fresh, and undisputed |
| 31 | Prospective horizon | https://sam.gov/opp/ae337f5d01e3499b980d347fa7567323/view | BUILDER-DERIVED+RECONCILED | `sam_notice`; GUID present, fresh, and undisputed |
| 32 | Evidence dock | https://www.usaspending.gov/award/CONT_AWD_1333BJ25F00280001_1344_47QTCK18D0036_4732 | BUILDER-DERIVED+RECONCILED | `usaspending_award`; generated ID present, fresh, and undisputed |
| 33 | Evidence dock | https://www.usaspending.gov/award/CONT_AWD_1333BJ21F00280023_1344_47QTCK18D0036_4732 | BUILDER-DERIVED+RECONCILED | `usaspending_award`; generated ID present, fresh, and undisputed |
| 34 | Evidence dock | https://www.usaspending.gov/award/CONT_AWD_70SBUR22F00000113_7003_HHSN316201200193W_7529 | BUILDER-DERIVED+RECONCILED | `usaspending_award`; generated ID present, fresh, and undisputed |
| 35 | Evidence dock | https://www.usaspending.gov/award/CONT_AWD_36C10B25F0304_3600_47QTCA23D00BT_4732 | BUILDER-DERIVED+RECONCILED | `usaspending_award`; generated ID present, fresh, and undisputed |
| 36 | Evidence dock | https://www.usaspending.gov/award/CONT_AWD_HR001124C0488_9700_-NONE-_-NONE- | BUILDER-DERIVED+RECONCILED | `usaspending_award`; generated ID present, fresh, and undisputed |
| 37 | Evidence dock | https://sam.gov/opp/b208e33b8dcd49088d751842a5f462aa/view | BUILDER-DERIVED+RECONCILED | `sam_notice`; GUID present, fresh, and undisputed |
| 38 | Evidence dock | https://www.cisa.gov/sbom | UNVERIFIABLE | GET 403; warning-only manual check |
| 39 | Evidence dock | https://www.cisa.gov/known-exploited-vulnerabilities-catalog | UNVERIFIABLE | GET 403; warning-only manual check |

## AGENCY_DOC provenance check

| Source | Class | Proof | Claim visibility |
|---|---|---|---|
| https://www.nasa.gov/news-release/nasa-awards-solutions-for-federal-enterprise-procurement-contracts/ | OK | GET 200, terminal URL unchanged | `$20B` found after numeric normalization; no warning |

## INTERNAL sidecar rendering

```text
## MANUAL LINK CHECK
- https://www.cisa.gov/sbom · Evidence dock · GET 403 cannot verify access at https://www.cisa.gov/sbom
- https://www.cisa.gov/known-exploited-vulnerabilities-catalog · Evidence dock · GET 403 cannot verify access at https://www.cisa.gov/known-exploited-vulnerabilities-catalog
```

## SEWP provenance and approval binding

The tracked content row changed only its AGENCY_DOC source URL:

```diff
- "source_url": "https://www.sewp.nasa.gov/sewpvi/"
+ "source_url": "https://www.nasa.gov/news-release/nasa-awards-solutions-for-federal-enterprise-procurement-contracts/"
```

The visible prospective-horizon link remains
`https://www.sewp.nasa.gov/sewpvi/`; it is a separate outbound program link.

After the NASA page fetched OK, the worktree-local ignored sweep's agency-doc
row was refreshed through the canonical artifact writer. Its digest changed
from `bf0a758e081568378a02aff317b023cede79782e94d87780c9ff5314cd54fc07`
to `26901734d9d51cded2c3ec1271d041ec81b625013a6764b9b67bd891a7cb1de4`.
The stored approval digest is
`b75fcfa8ec7657c10f0c6f5ccd7c23aebb45398e579e0f9cc450c9270b1a8986`.
The copied worktree sweep was already stale against that approval before this
URL refresh; the refresh changed the evidence fingerprint again and the
approval remains correctly stale with: `Assess approval sweep evidence
changed; review and approve the current results again`.
