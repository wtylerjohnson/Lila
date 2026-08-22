# Riverbed claim-baseline audit — Phase 1.5 close-out and Codex handoff contract

Date: 2026-07-31 · Author: golden-build integrator session · Status: HANDOFF (no production mutation performed)

## 0. Audited artifacts

| artifact | path | sha256 |
|---|---|---|
| Delivered report (Desktop copy is byte-identical) | `data/state/candidate_review_v1/riverbed/riverbed.golden_report.html` | `80f2cb5cfe37cd6cea3c017c55c49b85aad83770840908efb711e57990ab3ff2` |
| Evidence pack | `data/state/candidate_review_v1/riverbed/riverbed.golden_report.evidence_pack.json` | `c8a6c0534ddaf4a0298cb8cd2222effb48caf30752438303673cd2c78183919f` |
| Prose sidecar | `data/state/candidate_review_v1/riverbed/riverbed.golden_report.prose.json` | `94df6ec76c3fd69946a840547fb01ec5b8fdbd7e259a8bd31ffb9365b6edd0fb` |

Audit/link-check timestamp: `2026-07-31T21:19:18+00:00`.
Machine-readable ledger (gold fixture, schema documented inside): `docs/reviews/riverbed-claim-baseline-2026-07-31.json` (256 claim units).

## 1. Claim-unit rules and the reconciled denominator

The initial audit reported 301 stated / 234 itemized. The entire 67 gap sat in one
band group (hero + ticker + forecast), where the auditor tallied ELEMENTS but
itemized roll-ups. One denominator now, under deterministic rules:

- **U1** One rendered record-row = one claim unit (ticker item, calendar row, dock row, forecast card, candidate card).
- **U2** One stat tile = ONE calculation claim; its rendered show-your-work input rows are evidence display of that claim, not separate units.
- **U3** A card's label/treatment/why sub-elements collapse into that card's claim unit(s); prose sentences split on assertion boundaries.
- **U4** aria-hidden content (ticker mirror set), labels, chrome: non-claims.
- **U5** The same record rendered in several bands = one semantic claim with several occurrences.

**Accounting for the 67 elements:** 24 ticker rows (INCLUDED as units; mechanically
field-verified below; they subsume 2 previously itemized roll-up entries), 26
anchor-tile show-your-work input rows (EXCLUDED, rule U2 — they are the rendered
working of the max-of-26 calculation claim), 17 card sub-element duplicates
(EXCLUDED, rule U3 — six forecast cards x label/treatment/why collapse to six
units; five hero chips collapse to one).

**Reconciled denominator: 256 claim units** (234 − 2 roll-ups + 24 ticker rows).

## 2. Reconciled scores (numerators and denominators explicit)

| score | numerator | denominator | value |
|---|---|---|---|
| Weighted evidence coverage | 921 weighted pts supported | 999 weighted pts total | **92.2%** (95.5% with half-credit partials) |
| Citation accuracy | 231 fully supported units | 256 units | **90.2%** |
| Source quality | 222 primary/derived cited | 222 evidence-bearing units | **100.0%** — CAVEAT: 33 units carry evidence_class `none` and 1 `navigation`; they are outside this denominator and are the B-class defect surface |
| Link health | 51 healthy | 51 unique external URLs | **100%** at 2026-07-31T21:19:18Z |

Population: 215 fact · 23 inference · 15 calculation · 3 recommendation.
Assessment: 231 supported · 21 partially supported · 4 unsupported.
Defects: **A=12 · B=40 · C=0 · D=7 · E=2 · none=195.**

## 3. Retrieval conclusion (reframed)

**No retrieval defect was identified within the reconciled audited claim
population under the current report scope.** This is a statement about the 256
audited units of THIS artifact, not a general claim: per-claim treatment review
(section 4) independently assigned zero `retrieve additional evidence`
treatments, and zero `remove the claim` treatments — every non-supported unit is
repairable from evidence already held in the pack. Claims the current scope
excludes (DoD, GWAC task orders, subaward flow) were not audited and no
retrieval conclusion extends to them.

## 4. Every non-supported unit, with its treatment (25 units)

Treatment vocabulary: bind to existing evidence · expose calculation working ·
weaken/correct the language · label as inference/recommendation · retrieve
additional evidence · remove the claim.

| band | assess | defect | claim (clipped) | treatment |
|---|---|---|---|---|
| sb-hero + news ticker +  | partial | B | Almost all of that money reaches the government through resellers on GWAC and schedule vehicles | weaken the language + expose working (85.4% by dollars; show direct-award counte |
| sb-hero + news ticker +  | unsuppo | D | LARGEST OF 26 ACTIVE AWARDS (qualifier: '26 cited active records') | correct the language (derive ACTIVE from date fields; exclude expired) |
| sb-hero + news ticker +  | unsuppo | D | 34 CORRIDORS (metric-one note) | expose calculation working (use canonical selection.corridor_agencies; reject va |
| sb-hero + news ticker +  | partial | D | >$500.65M — Summed forecast lower bound — Summed published lower bounds of 10 forecast records · | correct the language (labels derived from acquired fields) |
| sb-hero + news ticker +  | partial | D | 26 CORE · 10 RIVAL · 10 FORECAST (metric-four note) | correct the language (labels derived from acquired fields) |
| sb-hero + news ticker +  | partial | B | the Technology Acquisition Center NJ is separately running market research for a FedRAMP High an | bind to existing evidence (per-slot citations channel) |
| candidate-review | partial | B | These records were pulled first because each one either names a Riverbed product in current perf | bind to existing evidence (per-slot citations channel) |
| candidate-review | partial | B | The nearest are the Internal Revenue Service award ending 31 JUL 2026, the National Science Foun | expose calculation working (comparative computed over pack clocks) |
| candidate-review | partial | B | This is the live renewal that carries the Service's installed base forward and the nearest large | expose calculation working (comparative computed over pack clocks) |
| candidate-review | partial | B | ...making it one of the nearest Riverbed clocks in the pack. | expose calculation working (comparative computed over pack clocks) |
| candidate-review | unsuppo | B | ...the nearest clock in this assessment... | expose calculation working (comparative computed over pack clocks) |
| signals+accounts | partial | A | BLS_FY2025_68 - Annual Software Support Renewal · OASAM-Office of Chief Information Officer · Be | expose calculation working |
| signals+accounts | partial | E | The Office of Procurement Operations obligated $1,141,948 to CARAHSOFT TECHNOLOGY CORP for Dynat | schema extension + label as inference until representable |
| capabilities+leadership | partial | B | Cory Rasnic and Kimberly Sandoz at the NASA IT Procurement Office | bind to existing evidence (small_business_poc field on the forecast record) |
| capabilities+leadership | partial | B | Each is published in a procurement role for one requirement, so confirm the person still holds i | bind to existing evidence (per-slot citations channel) |
| method, calendar | partial | A | Every figure in this document derives from the cited records; each identifier links to its prima | expose calculation working |
| method, calendar | partial | B | Read the calendar as three clocks on one line. Award clocks come first and are close... | bind to existing evidence (per-slot citations channel) |
| method, calendar | partial | A | 1 APR 2026 · FORECAST WINDOW · ***4/9/26 - Solicitation has been released, in accordance with re | correct rendering (strip APFS editorial stamp in ALL render paths) |
| events+evidence | partial | B | AFCEA WEST runs 16 FEB 2027 to 18 FEB 2027 and Sea-Air-Space runs 04 APR 2027 to 07 APR 2027, bo | bind to existing evidence (per-slot citations channel) |
| events+evidence | partial | B | HIMSS Global Health Conference runs 05 APR 2027 to 08 APR 2027 in Chicago, IL, which is the heal | bind to existing evidence (per-slot citations channel) |
| events+evidence | unsuppo | A | GOVERNMENT-HOSTED · CORRIDOR-RELEVANT | expose calculation working |
| events+evidence | partial | D | AFCEA / USNI · Department of the Navy audience · your Department of Homeland Security corridor c | correct the language (labels derived from acquired fields) |
| events+evidence | partial | E | HIMSS · Department of Health and Human Services audience · your VETERANS AFFAIRS, DEPARTMENT OF  | schema extension + label as inference until representable |
| events+evidence | partial | B | The dock below carries every record cited in this report with its source link, so each sentence  | bind to existing evidence (per-slot citations channel) |
| ticker | partial | D | CLOCK · 30 JUL 2026 · National Science Foundat RIVERBED MAINTENANCE · $276.1K | correct the language (derive clock liveness from period_end vs generation date) |

Treatment totals: bind=8 · expose-working=8 · correct-language=5 (incl. the
ACTIVE label and 34-corridor count) · weaken=1 · schema+inference-label=2 ·
correct-rendering=1 · retrieve=0 · remove=0.

## 5. Ten false-confidence examples

The feared class (vendor homepages, search URLs as evidence) is ABSENT: 320/328
anchors are machine-bound `data-source-link` to canonical federal record URLs.
The real false-confidence pattern is verified-looking staleness and
unreproducible aggregates: (1) "largest of 26 cited ACTIVE records" — 11/26
past-period including the $49.73M anchor (ended 2024-09-10); (2) "34 CORRIDORS"
vs canonical 32; (3) NSF clock expired at generation rendered live; (4) "almost
all ... through resellers" at a measured 85.4% with counter-evidence unshown;
(5-10) the nearest-clock superlatives, TAC-NJ market-research inference, NASA
contact attribution, BLS below-SAT row, and two conference corridor lines whose
phrasing exceeds the cited record's fields. Full text of each is in the gold
fixture (`nonsupported_with_treatments`).

## 6. Reproduction

```bash
# denominator + scores + defect counts from the gold fixture
.venv/bin/python - <<'EOF'
import json
from collections import Counter
g = json.load(open('docs/reviews/riverbed-claim-baseline-2026-07-31.json'))
cs = g['claims']
assert len(cs) == 256
print(Counter(c['defect_class'] for c in cs), Counter(c['assessment'] for c in cs))
print(g['scores'])
EOF

# mechanical ticker verification against pack (24/24, 1 expired-at-generation)
.venv/bin/python - <<'EOF'
import json, re
html = open('data/state/candidate_review_v1/riverbed/riverbed.golden_report.html', encoding='utf-8').read()
pack = json.load(open('data/state/candidate_review_v1/riverbed/riverbed.golden_report.evidence_pack.json'))
by_url = {r.get('url'): r for r in pack['records']}
body = html[html.find('<body'):]
first = body[body.find('sb-news-set'):]; first = first[:first.find('aria-hidden')]
hrefs = re.findall(r'href="([^"]+)"', first)
text = ' '.join(re.sub(r'<[^>]+>', ' ', first).split())
segs = [s.strip() for s in text.split('CLOCK ·') if s.strip()][1:]
MON = {m: i+1 for i, m in enumerate(['JAN','FEB','MAR','APR','MAY','JUN','JUL','AUG','SEP','OCT','NOV','DEC'])}
ok = exp = 0
for seg, url in zip(segs, hrefs):
    m = re.search(r'(\d{1,2}) ([A-Z]{3}) (\d{4})', seg)
    d = f"{m.group(3)}-{MON[m.group(2)]:02d}-{int(m.group(1)):02d}"
    a = re.search(r'\$([\d.,]+)([KMB])?', seg)
    rec = by_url[url]; pe = str(rec['period_end'])[:10]
    mult = {'K':1e3,'M':1e6,'B':1e9}.get(a.group(2) or '', 1)
    val = float(a.group(1).replace(',', '')) * mult
    ok += (pe == d and abs(val - rec['obligated_dollars'])/rec['obligated_dollars'] < 0.01)
    exp += (pe < '2026-07-31')
print('field-verified', ok, 'of 24; expired-at-generation', exp)
assert ok == 24 and exp == 1
EOF
```

The semantic (prose entailment) portion of the ledger was produced by a
multi-agent audit pass; its OUTPUT is frozen in the gold fixture and reviewable
per claim. Deterministic checks above reproduce without any model.

## 7. Typed Claim contract (authoritative origin BEFORE render)

Claims originate at the earliest truthful stage; the renderer assigns DOM
occurrence IDs and serializes — it never infers the claim/source relationship
from final HTML.

```python
class Claim(BaseModel):                        # new: agents/golden_press/claims.py
    claim_id: str          # semantic identity: sha256 over (client, claim_kind,
                           #   subject identity, predicate signature) — NOT text,
                           #   NOT band, NOT layout position
    occurrence_ids: list[str]  # renderer-assigned DOM ids (data-claim-occ)
    claim_kind: Literal['record_fact', 'calculated', 'model_composed',
                        'manually_authored']
    claim_type: Literal['fact', 'calculation', 'inference', 'recommendation']
    text: str              # rendered text THIS version; not identity
    band: str
    client: str
    source_record_ids: list[str]
    source_field_paths: list[str]   # e.g. 'records[2032H519F00718].obligated_dollars'
    cited_passages: list[str]       # verbatim spans for document-class sources
    calculation: Optional[dict]     # {method, inputs: [record_id.field], value}
    evidence_class: Literal['primary_federal_record', 'primary_supporting_document',
                            'derived_evidence', 'corroborating_authority',
                            'commercial_context', 'navigation', 'discovery', 'none']
    relevance: Literal['entailed_deterministic', 'entailed_model', 'unevaluated',
                       'failed']
    confidence: Optional[str]; caveat: Optional[str]
    source_health: Optional[dict]   # {url: {status, checked_at}} from cache
    evidence_version: str  # sha over sorted (record_id, field, value) tuples
    report_id: str; pack_sha256: str
    superseded_by: Optional[str]; created_at: str
```

Origins: `record_fact` claims are minted by the SELECTION/shape stage from
record+field-path; `calculated` by the calculation code with complete inputs
(the existing `_pack_dollar_candidates` aggregates become named calculations);
`model_composed` by compose with a per-slot permitted-source + returned-citation
channel (slot keys `why_<record_id>` / `why_corridor_<slug>` are the migration
bridge ONLY — the citations channel is the contract); `manually_authored` only
via Studio edits, marked as such.

## 8. Identity and refresh law

| event | claim_id | occurrence | evidence_version | work-item |
|---|---|---|---|---|
| wording changes, same meaning+evidence | stable | new | stable | stable |
| claim moves bands | stable | new | stable | stable |
| source fields refresh, same values | stable | stable/new | stable | stable |
| canonical URL changes | stable | new | NEW | stable |
| evidence set changes | stable | new | NEW | stable, flagged `evidence_changed` |
| calculation inputs change | stable (same predicate) | new | NEW | stable, flagged |
| incorrect claim superseded | RETIRED; successor links `superseded_by` | new | new | carried to successor with history |
| opportunity absent from next report | stable, no new occurrences; state `dormant` | none | frozen | PRESERVED — work history never deleted by presentation |

Semantic claim_id / rendered occurrence / evidence_version / work-item are four
separate identities; only occurrence is report-local.

## 9. Reuse/change table

| existing component | already covers | missing | proposed extension | tests affected |
|---|---|---|---|---|
| `agents/golden_press/link_health.py` (246 ln) | anchor extract, classify, canonical expectation vs pack, bounded-concurrency verify | persistent cache w/ checked_at; press consumption | add cache read/write (schema from link_integrity CACHE_VERSION pattern); NO new module | test_link_health* extend |
| `tools/recheck_links.py` | CLI re-verify | claims-aware output | point at cache; report per-claim | small |
| `agents/reports/links.py` | deterministic canonical builders (SAM notice, award) + workspace rewrite | APFS/Gateway/conference builder registry entry-point | `builder_for(source_system)` registry | canonical URL tests per type |
| `agents/reports/link_integrity.py` (965 ln) | LinkClass, LinkCheckResult w/ checked_at+cache, client link gate | golden-press wiring | reuse result/cache types verbatim in golden press | existing suite |
| `agents/reports/facts.py::Fact` | per-figure provenance: id, source_system, source_record_id, retrieved_at, freshness, disputed, competing_values | claim_type/occurrences/calculation/work-state | Claim embeds the same provenance field names (do NOT collide with F{n} contract surface — new id namespace `C:`) | fact tests untouched |
| QA certificate (`.qa.json`) + `release.py` | certified/release derivation | four scores | additive keys in press verdict sidecar | release tests additive |
| Studio source contract (`inventory`, `_require_source_contract`) | byte-survival of hrefs/edit-ids/evidence rows | claim-occ attributes | add `data-claim-occ` to preserved inventory | studio tests extend |

## 10. Work-state persistence decision table

Traced mechanics: Studio drafts = browser localStorage keyed by report-id
(version-3 snapshots; new pack hash ⇒ new key ⇒ drafts orphaned on refresh);
download = DOM deep-clone (edits baked in, no state store travels).
**Conclusion: existing draft/download mechanics DO NOT provide durable work
state.** Decisions:

| surface | decision |
|---|---|
| Evidence drawer in standalone downloaded HTML | works fully offline from the embedded claims JSON; read-only evidence |
| Browser-local interaction | work-state buttons write localStorage under `claim_id` (NOT report-id), so state survives refresh locally |
| Server-backed AE state | authoritative store `data/review/<slug>.claim_work.json` via Command Center API (same family as flag_decisions.json); local state syncs when the CC serves the report |
| Multi-user/shared | server file is the single writer via CC; report embeds read-only snapshot at press time |
| After refresh | claim_id-keyed state re-binds to new occurrences; `dormant` claims keep history |
| Corrected/superseded/removed claim | work item carries to successor via `superseded_by`; history immutable |
| Export/download | AE work-state is intentionally EXCLUDED from downloaded client HTML (client-facing artifact carries evidence, not the AE's pipeline) — embedded only in the internal/studio variant |

## 11. Named acceptance cases (gold tests, not patches)

| case | current rendered | records | corrected treatment | deterministic test |
|---|---|---|---|---|
| AC1 ACTIVE label | "Largest single award of 26 cited active records" | the 26 incl. `2032H519F00718` (ended 2024-09-10) | label derived from `period_end/potential_end_date >= generated_at`; expired renders "26 cited records (14 in period of performance)" | fixture with expired anchor must not render ACTIVE |
| AC2 corridor count | "34 CORRIDORS" | `selection.corridor_agencies` (32) | count = canonical population; variant-dedupe | fixture with NASA×2 spelling variants ⇒ one corridor |
| AC3 expired clock | "CLOCK · 30 JUL 2026 · NSF" live | `49100425F0066` | expired-at-generation renders CLOSED/EXPIRED tag | generation date after period_end ⇒ no live CLOCK |
| AC4 'almost all' | "Almost all ... through resellers" | 36 L2 vehicle fields (85.4%) | language thresholds documented ('almost all' ⇒ ≥95%); show direct-award counter-evidence | 85.4% fixture fails 'almost all', passes 'most', requires counter-line |
| AC5 aggregate working | rival-stack tile OK; others lack it | tile inputs | every `calculated` claim renders/embeds inputs+method | calculated claim without inputs fails validation |
| AC6 APFS stamp | "***4/9/26 - Solicitation has been released" in calendar | APFS record | `_APFS_STAMP` normalization applied on EVERY path incl. calendar `anticipated_solicitation` | stamped fixture never renders `***` or the stamp date |
| AC7 person attribution | "Cory Rasnic and Kimberly Sandoz at the NASA IT Procurement Office" | forecast `small_business_poc` | typed contact sub-claim bound to the field; renders what the field states | contact claim requires source_field_path to a *_poc field |

## 12. File ownership

**Codex-owned:** `agents/golden_press/claims.py` (new: Claim model, minting at
shape/selection + calculation sites), `agents/golden_press/compose.py`
(citations channel + verification), `agents/golden_press/render.py` (occurrence
stamping + serialization; AC1-AC6 fixes), `agents/golden_press/link_health.py`
(cache), `agents/reports/links.py` (builder registry),
`agents/golden_press/validate.py` (claim gates + four scores),
`agents/golden_press/press.py` (claims sidecar + embed), tests:
`test_claims_*.py`, acceptance `test_claim_acceptance_cases.py`, canonical-URL
tests. **Fable-owned (after contract lands):** Studio drawer + affordance CSS/JS
(`studio.py` runtime additions consuming embedded claims JSON), work-state
buttons + CC endpoint UI (`ui/server.py` additive route + `ui/` assets),
`data/review/<slug>.claim_work.json` write path via existing review-API family.
Fable consumes `claims.py` as-is; any schema need goes back to Codex. Codex
reruns all gates after Fable's pass.
