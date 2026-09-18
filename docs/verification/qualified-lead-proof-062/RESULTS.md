# Qualified lead proof 062 — local implementation and review

The new code can evaluate an early buying event without a published solicitation. No increase in qualified Veeam leads has been demonstrated. The frozen baseline and same-input replay both produce **0 accepted unique buying events** under the same rubric. This is a staged development result, not a deployed or client-approved release.

## Requested steps

| Step | Completed locally | Remaining |
|---|---|---|
| 1. Connect research to qualification | Optional, source-bound buying event; native T2 path; five evidence legs and verified email/mobile; negative/counterevidence checks | Observe the new path with real sufficient evidence in an ordinary run |
| 2. Investigate the four candidates | Refreshed official sources; preserved saved enrichment; specific disposition and next question for each | Buyer facts remain unresolved; none qualifies |
| 3. Complete opportunity-to-action fields | Native action-sheet path retains who, why now, product fit, eligible supplier route, question and evidence; office and mobile distinguished | No real accepted event currently meets all requirements |
| 4. Fixed before/after comparison | Frozen source hashes, one rubric, deduplication, separate contact counts, time/cost fields and zero-denominator handling | Fresh-run comparison; historical timing/cost unavailable |
| 5. Ordinary-run proof | Real Command Center harness and automatic run-addressed Assess/Press/scorecard companion prepared | Approved September 18; ordinary Command Center run in progress |

## Four investigation outcomes

| Candidate | Finding | What prevents qualification |
|---|---|---|
| MDA MD30 | Published recovery/continuity planning; three saved provider contact records, two with explicit mobile numbers | Remaining need, purchase decision, eligible Veeam supplier and MD30 ownership unconfirmed |
| CBP biometrics | Current program activity and published program route | Remaining recovery work, tooling decision, technical owner and verified mobile unconfirmed |
| TSA backup infrastructure | Official forecast includes servers, VM hosts and backup media | Actual procurement status after the forecast date, software scope, owner and supplier unconfirmed |
| ICE backup equipment | Official forecast amendment describes VMware/NetApp to FlexPod | Current procurement status, remaining recovery software, owner and supplier unconfirmed |

Two saved email/mobile records are contact inputs, not two leads. They are not present in the historical baseline and were not silently inserted into the replay. No new paid enrichment or outreach was performed. Exact questions and evidence boundaries are in `four-investigations.json`.

## Additional discovery

Scanned all 84,052 rows of the September 17 full SAM extract; 1,981 were within DHS/MDA. Expanded approved-profile and technology terms matched 23 notices across 12 solicitation keys. Ten keys were excluded from the Veeam shortlist based on the retained descriptions. Global Entry Airport Modernization and the Biometric Capture Device GWAC remain research: their attachments were not inspected and no recovery-software requirement was established. This bounded scan is not an exhaustive negative finding about the market. The extract was read locally; no SAM API fallback was used.

## Validation and limits

- Final strict offline suite: **6,280 passed, 40 skipped, 22 warnings, 26 subtests passed**, 147.57 seconds. Test count demonstrates regression checks, not output quality or new leads.
- The first full suite found a new failure in the companion diagnostic path (`str.with_suffix`), reproduced across runner tests. It was corrected to use `Path`; a missing persisted sweep now reports NOT RUN rather than a qualification count. The final whole-suite result above includes the repair.
- Final native HTML passed authored-copy, contact, count and federal-link lint. Original quotations are marked as source text; original JSON downloads remain unchanged.
- Browser checks on that same HTML hash: navigation and evidence disclosure; 390px layout; no page errors; two actual source downloads byte-equal to the bound originals. The existing standalone renderer has no text editor or HTML export control; neither was added or claimed.
- Four named dirty operating files and both frozen baseline artifacts still match their kickoff/baseline hashes. This is a scoped preservation check, not an assertion that all filesystem activity was observed.
- Several baseline required source lanes are partial or failed; they still block promotion. Those failures are not screened-zero results and were not waived. Source capture establishes retrieval, not current unmet demand.
- The early-event predicate uses conservative literal source checks. Unrecognized wording remains research; this is not a calibrated semantic classifier. Existing live-notice qualification remains unchanged, with the scorecard additionally requiring verified email/mobile.
- Elapsed time and cost for the proposed fresh run are unmeasured. Cost/time per accepted event is undefined at zero, not $0. No Claude Max subscription allocation has been invented.

## Run prepared for approval

The prepared script invokes the real Command Center `/api/run` route in the isolated checkout with Veeam, step `searches`, workstation `agency_dhs_mda`, and `LILA_LLM_ROUTE=max`. It reuses the approved engagement and daily full extract. New paid Apollo/contact-graph collection is disabled. The model receives the analytical Veeam profile (NAICS fields omitted), source passages and contact candidates. The saved Apollo-target adapter sets the structured `phone` field to `None` and includes an email only when it is unlocked; that assignment is not a payload-wide redaction because the saved record is also retained as source evidence. The separate precollected research supplement preserves three MDA contact records with email and phone values (two explicitly mobile, one direct line). Government-published contact fields and source passages may also contain phones. The saved-target adapter, supplemental verified contacts and public source text are distinct input paths. It does not approve Assess, activate outreach or publish a report.

Automatic approval review rejected the earlier launch over payload authorization. On September 18 the user explicitly approved sending the prepared inputs and requested commit, merge and push. The approved Command Center run started successfully; its outcome must be recorded separately from the earlier same-input replay. No narrative is hand-repaired to improve the count. The user subsequently requested a possible future OpenAI route change and explicitly said not to change it yet; this run and the code continue to use Claude Max.

Code is uncommitted on `codex/qualified-leads-20260917`, based on `7d009ec49c078daad2e5365165a6b8bd2f367653`. The operating checkout and main branch were not edited by this work. Private research inputs and runtime fixtures are outside the code patch.

Local evidence package: `/Users/wtjohnson/Documents/Codex/2026-09-16/files-pasted-by-the-user-execute/outputs/qualified-lead-proof-062`.
