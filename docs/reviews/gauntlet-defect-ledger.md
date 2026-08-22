# Gauntlet defect ledger · Federal Market Map vs GOLDEN 87

Benchmark: `fixtures/reference/golden_87.html`
(sha256 `652c84a9e5716b7691729a17446319c0b3a15b4449fbe59e74c447290fa15e50`,
bound by `tests/test_market_map_press.py::test_benchmark_fixture_hash_is_bound`).
Machine contract: `fixtures/reference/golden_87.contract.json`, extracted by
`tools/reference_contract.py` (same extractor grades every pressed artifact;
Gate A is `parity(reference, candidate)`).

Production path under test: Command Center `POST /api/run
{step: candidate_review, args: {golden_only: true}}` → `run_candidate_review
--golden-only` → `golden_press` → market-map leg
(`agents/golden_press/market_map_press.py`) → `<slug>.market_map.*` family.

## Iteration 0 · baseline (pre-wiring artifact, Desktop 2026-08-07)

| # | Category | Severity | Defect | Root cause | Fix | Regression guard | Status |
|---|----------|----------|--------|-----------|-----|------------------|--------|
| 0.1 | architecture | fatal | Market Map pressed by ad-hoc scripts, not the CC path | renderer never wired into `golden_press` | additive fail-soft leg in `press.py`; artifact family `<slug>.market_map.*` | `test_press_writes_the_artifact_family`; CC job e5c2f9d814fc wrote the family | fixed |
| 0.2 | determinism | fatal | replay from the pack changed factual output (Gate D) | projection live-read notice store + targets store at render time | `capture_inputs()` + `<stem>.inputs.json` sidecar; projection consumes the capture | `test_replay_is_byte_identical` | fixed |
| 0.3 | parity | major | six benchmark sections absent (lifecycle, evidence-method, pursuit-thesis, pursuit-board, research-gates, vocabulary) | renderer stopped at the seven-section contract | six deterministic renderers, benchmark CSS ported (client strings scrubbed) | parity harness returns zero findings | fixed |
| 0.4 | parity | major | unknown facts carried no visible mark | gaps rendered as prose only | `missing-value` post-pass over gap phrases | parity `unknowns` check | fixed |
| 0.5 | validator | major | validator required the retired `class="mm"` vocabulary; every press read as missing all sections | stale `_SECTION` regex | id-based section matching, v2 | press certifies; `test_market_map_press` | fixed |
| 0.6 | validator | major | client-artifact rules judged the studio build (scripts/editing are legitimate there) | one build validated with the wrong rule set | client export derived by removal; client rules judge the client build | `test_client_build_carries_no_studio_surface` | fixed |
| 0.7 | density | major | one requirement rendered as twin rows (VA PIVOT LoopBack/Extension) | family collapse keyed per lane; posting-mechanics words split families | cross-lane family collapse; parentheticals/digit tokens/mechanics words stripped; merged row names both ids | `repeated_sentence` validator rule active on every press | fixed |
| 0.8 | density | major | boilerplate shape action repeated verbatim across records | one static action sentence in two emitters | per-record actions naming the record's own published contact | same rule | fixed |
| 0.9 | evidence | major | market tables asserted sums with no record link | rollup/rival moneys carried no evidence refs | per-row references, largest first; last cell links the top record | `missing_receipt` validator rule | fixed |
| 0.10 | coverage-language | major | sections stated neither completeness nor a named gap | CoverageStates existed but never reached the page | per-section coverage line derived from the projection | `coverage_unstated` validator rule | fixed |
| 0.11 | evidence-integrity | major | 15 pack rows (incl. all 10 forecasts) silently rejected by the capability gate; lifecycle read "0 forecast records" as if the market were empty | rejection without disclosure | rejection ledger in receipts; lifecycle truth line; Rejected row in evidence-method; typed `forecast_repull` research order | `test_no_raw_database_key...` + press certification | fixed |
| 0.12 | client-safety | major | rejection ledger printed raw 32-char notice ids | disclosure emitter bypassed the identifier law | titles lead; hex keys never render | `test_no_raw_database_key_is_shown_as_an_identifier` | fixed |

## Open (next iterations)

| # | Category | Severity | Defect | Proposed correction |
|---|----------|----------|--------|---------------------|
| O.1 | research coverage | major | forecast source pull predates the capability frame: 10 screened forecasts are all wrong-domain (IT ELAs), so the client has NO in-frame forecast lane | re-pull agency forecasts under the approved frame (research order now filed in-document); wire the forecast source sweep to `coverage_families` phrases |
| O.2 | intelligence | major | ~~partnering routes thin~~ FIXED:every  route now carries `client_role` (what the client performs) and `buyer_value` (why the combination helps the buyer), derived from the route's bound requirement's matched capability terms | landed 2026-08-07; style contract caught and forced the styled carrier |
| O.3 | AE speed | minor | pursuit board sits deep in the document; Gate F requires 90-second answers from the top | after grading, consider a compact top strip pointing at board ranks 1-3 (benchmark order kept for parity) |
| O.4 | performance | minor | `fetch_store_candidates` runs ~70 LIKE scans over 330k rows (~2 min per press) | add a title index or FTS table to the notice store |
| O.5 | typography | trivial | pursuit-name truncates mid-word at 90 chars | word-boundary truncation |

## Receipts

- Suite: 4252 passed, 2 skipped (full), then 4253-equivalent after disclosure
  fixes; focused market-map set green.
- CC press #1 (job e5c2f9d814fc): certified, 0 violations, 7-section build
  (code as of job start). CC press #2 (job 45bf538d4371): certified, 0
  violations, 14-section build, delivered to the Desktop 14:13 under the
  fail-closed promotion law. rc=0, 879.8s.
- CSS port defect found and fixed: the first benchmark port flattened
  media-inner rules into global scope; re-ported media-scoped. Mobile
  pursuit tape verified rendering in the browser (375px).
- Parity: PASS, zero findings, against the hash-bound benchmark contract.
- Replay: byte-identical from pack + `<stem>.inputs.json`.


## Iteration 1 · grading round 1 (workflow wf_92d10af9-a0d: 8 graders, 54 majors, 8 refuted by adversarial verify)

Fixed this iteration, each with the certifying press + suite green (4252/2):

| # | Category | Severity | Defect (confirmed by verifier) | Root cause | Fix |
|---|----------|----------|-------------------------------|-----------|-----|
| 1.1 | clock | fatal | nine of ten rows commanded "Reply ... before responses close" on CLOSED windows; headline said "What is open now."; motion badges said Shape on dead rows; the one live record ranked 5th | personalisation, motion and ranking never consulted response_date vs the press date | clock derived first; closed windows become one-sentence identifier-bearing successor asks; live-first ranking with enrichment within class; honest headline "{live} live; {closed} in successor research"; board badges Research on past rows |
| 1.2 | evidence | fatal | headline $281,185,258.63/125 receipt had empty sources; Section 2 untraceable to pack; replay claim false | rival-footprint cache was a side channel outside pack + capture | cache captured into `<stem>.inputs.json`; projection consumes the capture; total carries 40 sampled per-rival references |
| 1.3 | evidence | major | all five competitor cards displayed one award id and linked another; 17/19 usaspending links 404 | id and url picked from two differently-ordered lists; CONT_AWD_<piid> URLs fabricated | id+link from the SAME largest-first reference; fabricated award URLs removed globally (award ids render as text unless a canonical url was captured); `id_link_binding` validator rule (usaspending-scoped) |
| 1.4 | qualification | fatal | AERMOR (nuclear cruise missile) and FCN (IBM/Red Hat licenses) stood as teaming routes | route admission never consulted the capability contract | holders must have >=1 record clearing the same gate that judges opportunities; routes drop otherwise; teaming renders its research order and the receipt rule exempts stated research orders |
| 1.5 | honesty | major | "Category spend" labelled a five-rival award sum; market source never named visibly; rejected list silently truncated | labels/wording | "Rival award footprint"; competition deck cites the operator-supplied market source; "and N more in the press receipts" |
| 1.6 | robustness | major | Riverbed press died entirely on one legacy profile entity kind ('client') | reader validated row-by-row fatally | loud-tolerant reader: skip + name the row, never sink the press |

Remaining from round 1 (queued, highest first):
- client build ships dead receipts (zero JS): convert work drawers to scriptless `<details>` receipts (the 65 "Edit mode" tooltips + data-edit-link residue are FIXED: client export now strips them; `test_client_build_carries_no_studio_surface` extended by press run mm26)
- domain gate for events (4/5 wrong-domain) and the borderline qualified records the panel flagged (visitor-vetting Omni; TRICARE medical claims review; FIAR CPA-services family) -> controlled-family rules, operator disposes
- contacts provenance: 12/17 rows untraceable to pack alone (they ride the captured targets/store inputs; Method must name the capture beside the pack, and each row its source)
- per-record `<details>` evidence cards (benchmark has 25 with match/action panels)
- "proposed and not yet searched" vocabulary line is false; derive from the term ledger
- thesis/second-cover hierarchy, print page-breaks, richer headline voice
- forecast re-pull under the approved frame (research order ships in-document)


## Iteration 2 · Gate E generalization (four clients beyond Apex, zero client patches)

The fail-closed law worked: Riverbed's first production press was PARKED,
not delivered, on 8 violations. Every fix below is general code.

| # | Client that exposed it | Defect | Root cause | Fix |
|---|------------------------|--------|-----------|-----|
| 2.1 | riverbed | press died on one legacy profile entity kind | fatal row-by-row validation | loud-tolerant reader; client-kind rows held out of retrieval by design |
| 2.2 | riverbed | 10 identical shape actions; 5 identical undated actions | pack-lane boilerplate never personalised | every clock class personalises with the record's own identity |
| 2.3 | riverbed | 4 route sentences identical after "INC." | a legal suffix's period splits sentences | `_prose_name()` drops trailing dots in prose; display cells keep the registered name |
| 2.4 | riverbed | 2-4 identical buyer-value lines per agency | fallback line ignored the route | route organisation and cited record ride every route sentence |
| 2.5 | riverbed | raw 32-hex key in an action sentence | new personalisation bypassed the identifier law | `_prose_ident()`: database keys never reach prose |
| 2.6 | riverbed | parity flagged conference organiser links | harness demanded .gov for events | events are sourced by their organisers (the source-role law itself); fact domains still .gov/.mil-only |
| 2.7 | thinklogical | receipt rule fired on a figure-free rival set | rule scope | a section stating no dollar figure owes no record-id receipt |
| 2.8 | thinklogical | internal file path rendered in client prose | profile source field | renderer sanitises path-like sources to "the operator-stated rival set" |
| 2.9 | mark43 | ticker echo + shared fact cells tripped the density rule | chrome and derived data judged as prose | ticker and `record-facts` cells excluded from the sentence pass; prose rules unchanged |
| 2.10 | (test) | alerts test pinned to pre-press disk state | live state as fixture | re-pinned to current disk truth with the re-press recorded |

**GATE E OFFLINE: 5/5 certified, parity PASS** (apexanalytix, riverbed,
thinklogical, varonis, mark43) from saved packs + captures. Production
re-certs through the Command Center in flight: riverbed 7c012822c862,
thinklogical a61b4d2eaa79, varonis 258c62c6914c, mark43 4f6f49dc8c9e.


## Iteration 3 · scriptless receipts + test-design correction

| # | Category | Severity | Defect | Fix |
|---|----------|----------|--------|-----|
| 3.1 | client build | fatal | every receipt dead in the client file (scripts stripped, 20+ inert buttons under an "every amount opens its receipt" promise) | receipts appendix: every drawer entry as a native `<details class="inline-work">` block with sources; every former work-trigger becomes an in-page anchor to its entry; print forces entries open. The appendix mirrors drawer data, so the density rule excludes it exactly as it excluded the `<script>` JSON it replaces |
| 3.2 | tests | major | alerts test pinned live press state (broke twice in one day BECAUSE Gate E re-presses worked) | asserts the mechanism: both alert schemas tolerated, every legacy row migrates, no ghosts, displacement specs produced |

Sweep after: **5/5 certified + parity PASS, client builds carrying 16-23
working receipt blocks each.** CC jobs launched before these fixes will
park FAILED copies where the old code violated (mark43 did, correctly);
all four Gate E clients get a final production re-cert on the settled code.


## Iteration 4 · coverage receipt + recall correction

| # | Category | Severity | Defect | Fix |
|---|----------|----------|--------|-----|
| 4.1 | completion artifact | major | no per-run coverage receipt (a required gauntlet artifact) | `<stem>.coverage.json`: declared sources + freshness, query plan (terms searched, candidate rows inspected), dispositions (qualified / rejected with named rows and reasons / family collapse / forecast screening), blind spots from the typed research demand, model_calls=0 |
| 4.2 | recall | major | pack-lane gate judged the finished row's empty `fit` note, so SDIB (a must-recall receipt) was rejected on its bare title | the gate moved into `_opportunities`, judging title + the RECORD's own description at the one point the description exists; SDIB recovered; apex rejections 15 -> 11, all genuinely wrong-domain |
| 4.3 | determinism | minor | an inputs sidecar captured before the rival lane joined silently fell back to a live cache read | press-seam backfill: missing capture keys are filled once, and the sidecar, document and coverage receipt describe the same inputs |

Sweep: **5/5 certified + parity PASS.** Final production presses fired for
all five clients on the settled code (jobs 94562a85fe36 / 62a8d034ae69 /
9d0c8ac5c435 / c7de301a5359 / 38cbff764bcf).


## Iteration 5 · final-press round trips

| # | Category | Severity | Defect | Fix |
|---|----------|----------|--------|-----|
| 5.1 | lint scope | major | varonis failed certification because a government RFI says "robust" (quoted mined phrase judged as our voice) | mined phrases render as `.source-quote` chips; the filler/vocabulary lints exclude verbatim source quotes; L-M3 still polices every word WE write |
| 5.2 | style tooling | minor | a CSS comment placed above a rule is swallowed into its selector by the style-contract parser, unregistering the rule (source-quote read as unstyled everywhere) | comments moved beside rules; parser quirk documented here |
| 5.3 | ops | minor | editing the worktree while CC presses run leaks new code into their render legs (function-level imports) — thinklogical's final press failed on the mid-flight `source-quote` before its CSS landed | sequencing discipline: code settles, then presses fire; the fail-closed gate contained both incidents |
| 5.4 | tests | minor | fresh presses store zero R2 alerts, so the disk-recovery test asserted state that no longer exists | skips honestly on empty state, re-arms when a press stores alerts; synthetic-legacy fixtures keep the mechanism covered |

Final state: offline sweep **5/5 certified + parity PASS**; production
finals certified for apexanalytix, riverbed, mark43; thinklogical + varonis
re-certs in flight on the settled code.


## Iteration 6 · the cross-client vocabulary leak (caught by reading the Gate E table, not by any gate)

| # | Category | Severity | Defect | Root cause | Fix |
|---|----------|----------|--------|-----------|-----|
| 6.1 | cross-client leakage | fatal | four different clients pressed IDENTICAL qualified sets (24 opps / 11 contacts): payment-integrity RFIs and VA/Navy contacts in a network-performance client's map | the store-lane fallback carried a hard-coded 11-phrase payment-integrity list plus a shared synonyms tier: one client's vocabulary baked into shared code (Gate D violation certification cannot see, because each document is internally consistent) | `_exact_terms` derives from THE CLIENT'S OWN surfaces exclusively (approved discoveries + its capability terms); a thin vocabulary yields a thin honest lane and a research order, never another company's market. Guard: `tests/test_cross_client_leakage.py` (unit: no foreign phrases, disjoint vocabularies, empty profile searches nothing; disk: pressed clients must not share store-lane query plans) |

After the fix, the same five packs press as five DIFFERENT companies:
apex 14 opps/17 contacts (contract-driven, unchanged) · riverbed 15/0
(honest thin lane) · thinklogical 30/29 (its own 3 terms, 25 rows) ·
varonis 15/1 · mark43 16/5. Leak-free production presses fired:
42e233ea10e9 / ea7bd75a64b8 / 929cab3c861d / 45a62ccef856.

Lesson recorded: certification judges one document; leakage lives BETWEEN
documents. The cross-client comparison is now a standing test, and the
"identical counts across clients" smell is the signature to watch.


## Iteration 7 · operator frame rulings encoded (2026-08-07 evening)

The three frame calls came back with rulings; each is now machinery, not
memory:

| Ruling | Mechanism |
|--------|-----------|
| Omni vendor vetting = wrong domain | `_ACCESS_CREDENTIALING` semantic rejection in `coverage_families.qualify` (personnel credentialing / visitor management / badging / facility, base, installation access), guarded by `_SUPPLIER_OBJECT` so supplier/payee/bank-account/vendor-master/invoice/payment vetting NEVER rejects. Record retained in ledger and rendered surface as `rejected_wrong_domain`; operator-ruled rejections sort FIRST in the visible rejection line |
| TCRS = recompete watch | new controlled family "Healthcare claims review" (trigger: healthcare/reimbursement context AND payment-integrity language; generic claims never qualifies) + record ruling reclassifying to `recompete watch` with the do-not-contest action and promotion conditions |
| FIAR = keep, split motion by route | `route_motion()`: pure attest -> partner (never auditor of record); clearance/set-aside -> partner: access required; NAICS 54121* or CPA text -> partner under CPA prime; past window -> successor watch; open software-addressable -> direct shape. Fit NEVER reduced; access is a separate dimension |

New shared mechanism: `clients/<slug>/record_rulings.json`, operator-owned,
applied AFTER the clock pass (rulings are final; personalization had
stomped three of them back to "status check"), receipted in coverage.json
under `dispositions.operator_rulings`. Family-aware positive context gate:
a family may declare `context_gate` (the global supplier gate had rejected
the whole FIAR family, whose words are audit/remediation).

Defect of note: my Python-heredoc edit wrote `\b` as literal backspace
bytes into two regexes, silently killing both word anchors; caught by the
regression test, repaired byte-level, and the lesson recorded: write regex
through the Edit tool or verify the compiled pattern, never trust an
unasserted heredoc.

Tests: `tests/test_record_rulings.py` (11 tests). Suite 4267 passed,
3 skipped. Offline sweep 5/5 with all seven ruling assertions passing.
