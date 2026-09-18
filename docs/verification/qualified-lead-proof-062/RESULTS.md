# Qualified lead proof 062: implementation and observed results

The code now evaluates source-bound early buying events without requiring a published solicitation. The September 18 ordinary Command Center run demonstrated **0 newly qualified buying events**: baseline 0, same-input replay 0, fresh run 0. Business improvement in qualified-lead yield is not yet proven. The implementation and test-isolation correction were committed, fast-forwarded into operating main, and pushed to GitHub as `95c0d203476e4cb5ce0a243e96ab33b7e527cda9` and `a3e309305382ad24718f690c27ae0f936a24b475`. No client release or outreach occurred.

## Requested work and result

| Step | Completed | Remaining evidence |
|---|---|---|
| 1. Connect research to qualification | Optional early buying event, five source-bound claims, eligible supplier route, exact owner, verified email/mobile and counterevidence checks feed the native T2 path | A real buying event satisfying these requirements |
| 2. Investigate MDA, CBP, TSA and ICE | Official sources refreshed; buying gaps and next questions retained; additional bounded discovery performed | Current requirement, purchase decision, supplier eligibility and opportunity-specific owner |
| 3. Opportunity-to-action fields | Native path retains who, why now, fit, route, first question and source evidence; additional Apollo collection completed after the report run | New contacts are routing candidates, not proven owners |
| 4. Fixed comparison | Same rubric across baseline, replay and fresh run; unique buying events separate from contacts; zero-denominator ratios remain undefined | Positive qualified-event yield; historical timing and dollar-cost data |
| 5. Ordinary-run proof | Real Command Center `/api/run` job `98ef02888b2f` finished and emitted its own hash-bound Assess, Press, scorecard and HTML | Overall synthesis timed out; required source coverage remains incomplete; no client release |

## Ordinary September 18 run

The staged Command Center run took **1,136.47 seconds (18 minutes 56 seconds)** end to end. This includes collection and model work; it is not an isolated report-render benchmark. Eleven subject investigations produced eight complete results and three requiring adjudication. MDA and TSA hit the automatic reversal cap; another subject requires explicit source-bound adjudication for positive reinstatement. The overall Claude research-picture call then timed out after 300 seconds. The runner retained its source data and generated the qualification companion; a successful process exit does not erase that synthesis failure.

The local September 18 SAM daily full extract scanned **84,810 rows**, returned 18 focused candidates, and passed independent post-read SHA-256 verification. Its SHA-256 is `ced91785f6afb927b68d71f56e1bbac3a29ab34a4aec0911a24de411bde19212`. No live SAM opportunity API fallback was used. Triage recorded 0 pursue, 0 monitor, 11 discard and 7 unscreened; unscreened is not a clean negative result. Other required collection lanes remain partial or failed, including Federal Register, forecasts and sources needing auxiliary API credentials. These conditions remain explicit in the coverage receipt and are not waived.

The native output has four research priorities and zero qualified rows. Its SHA-256 is `354688720443d886ecb8eb0329224eb7aa926f7cc43961e166dc7a2145a05ae8`; the input sweep SHA-256 is `d1ba609ed7982b02edd28ba6ddcae00d1bf6efa0cc671377352bbbd2ab1e6b88`. This is an observed before/after comparison with changed sources, not isolated causal attribution to the code. Per-run Claude Max dollar cost was not supplied; time/cost per accepted event is undefined at zero, not zero dollars.

Manual review found an MDA draft saying FY2027 had begun October 1, 2026 during a September 18 run. The draft was already withheld for adjudication; that statement is absent from the generated native HTML. It was not manually rewritten to improve the outcome. Temporal accuracy in withheld drafts remains a known limitation.

## Four investigation outcomes

| Candidate | Retained evidence | Unresolved buying facts | New Apollo mobiles |
|---|---|---|---:|
| MDA MD30 | Published recovery/continuity planning | Remaining need, purchase decision, eligible Veeam supplier and MD30 owner | 2 |
| CBP biometrics | Current program activity and published program route | Remaining recovery work, tooling decision and technical owner | 7 |
| TSA backup infrastructure | Forecast covers servers, VM hosts and backup media | Current procurement status after forecast date, software scope, owner and supplier | 11 |
| ICE backup equipment | Forecast amendment describes VMware/NetApp to FlexPod | Current procurement status, remaining recovery software, owner and supplier | 5 |

The earlier bounded September 17 discovery scan retained 23 matching notices across 12 solicitation keys: ten excluded and two biometric procurements left as research because attachments and recovery requirements were unresolved. Neither scan supports an exhaustive zero-market conclusion.

## Approved Apollo collection

After explicit approval of the exact 34-person batch and credit costs, all 34 people matched and **25 mobile numbers** returned. Two returned mobiles are DNC-listed and were withheld from active inputs. Twenty new records have both an Apollo-verified email and a valid mobile without a DNC listing; three other mobile records lack a verified email. Missing DNC data remains unknown. Nine contacts had no mobile returned; other phone types are never substituted for mobiles.

The four request receipts charged **34 matching credits + 272 phone-reveal credits = 306 credits**. The account balance changed from 3,706 to **3,400**, agreeing with the request totals. No dollar conversion was supplied. Apollo reported valid-number/email statuses; the observation time is recorded, and no internal provider validation timestamp is invented.

Existing saved contacts were reused separately. Two CBP records dated 2024 remain stale; a TSA saved record dated August 17 remains within the channel-age window. The combined local supplement has 38 contacts across the four exact source identities, of which 23 pass current channel checks. This count is separate from qualified buying events. Provider-reported roles still require current-employment and opportunity-ownership confirmation.

The supplement was validated through the real input reader and installed in both checkouts only after the ordinary report run finished. Its SHA-256 is `642c85cb782872d47f302f04294e4a89fd84f4e23650fb9aaf8fe0b08d9d057b`; the prior input snapshot is preserved. These are next-run inputs and were not retroactively inserted into the completed report. Contact values and raw provider results remain in local evidence files, excluded from Git. No outreach was sent.

## Validation and preservation

- Strict offline suite: **6,280 passed, 40 skipped, 22 warnings, 26 subtests passed in 145.69 seconds**. This proves the named regression checks, not new leads or factual perfection.
- An earlier new diagnostic-path failure (`str.with_suffix`) was corrected to use `Path`; missing persisted sweeps remain NOT RUN. Three subsequent operating-only failures were reproduced: mocked writes let the companion read an existing NETSCOUT sweep. The common test helper now binds the runner to its temporary fixture directory and asserts that output stays there.
- Eight failures in one sandboxed run were local Chromium/PDF launch errors. With browser execution permission, the unchanged strict suite passed. Post-merge operating smoke: **106 passed**. The in-process build endpoint identified the correct operating root and code revision; no live UI restart is claimed.
- The final native HTML passed four lints and browser checks: navigation, source disclosures, 390px layout, no page errors, and two actual source JSON downloads byte-equal to their originals. Desktop appearance was inspected. The renderer has no text editor or HTML-export control; neither was added or claimed.
- Both frozen baseline files and four named dirty operating files retained their recorded bytes. This is a scoped preservation receipt, not a claim that all filesystem writers were observed.
- The early-event predicate uses conservative literal source checks. Unrecognized wording remains research; it is not calibrated semantic inference.

## Corrected payload description and model route

The legacy Apollo saved-target adapter sets its structured `phone` field to `None` and includes an email only when unlocked. That is not a payload-wide redaction: the original saved record is retained as evidence. A separate research supplement preserves actual contact phone values; the report run used three MDA records, two mobile and one direct. Government-published contact fields and source passages may also contain phones. These are distinct input paths, not a claim that the legacy adapter supplied structured phone numbers.

The user approved sending the prepared profile, evidence and contacts through Claude Max. The user later explicitly approved the 34-contact Apollo batch and credit costs, resolving the prior automatic approval rejection. The possible future OpenAI switch remains deferred exactly as requested: **Claude Max routing is unchanged**.

Machine-readable aggregate receipts are in `RUN_RESULT.json`; raw run, provider, comparison, browser and preservation receipts remain in the local `outputs/qualified-lead-proof-062` package.
