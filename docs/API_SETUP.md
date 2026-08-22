# LILA — Next Round of Data Sources: Exact Setup

All free. Ordered by effort. Every key goes in the repo's `.env` file (same one
`tools/env.py` already loads). After adding keys, restart the server.

---

## Tier 0 — nothing to set up (work the moment an adapter ships)

| Source | Base URL | What it adds |
|---|---|---|
| USAspending subawards ✅ WIRED | `https://api.usaspending.gov/` | which primes buy client-type products → teaming targets |
| Federal Register ✅ WIRED | `https://www.federalregister.gov/developers/documentation/api/v1` | program-formation signal 6–18 months pre-RFP |
| Grants.gov search ✅ WIRED | `https://www.grants.gov/api/` | assistance-side demand; title-supported, scope-valid rows can enter Prospective Horizon as PROGRAM evidence, never as contract solicitations |
| SAM.gov daily extract ✅ WIRED | Data Services → Contract Opportunities (falextracts S3) | THE quota-free discovery path: whole notice universe daily, unlimited local keyword+NAICS screening |
| GSA Acquisition Gateway forecast ✅ WIRED | `https://acquisitiongateway.gov/forecast` | government-wide acquisition forecasts; agency coverage is disclosed and never presented as complete |
| CISA KEV catalog ✅ WIRED | `https://www.cisa.gov/known-exploited-vulnerabilities-catalog` (JSON feed linked on page) | live threat context for cyber-client fit narratives |
| GDELT 2.0 DOC ✅ WIRED | `https://api.gdeltproject.org/api/v2/doc/doc` | machine-readable news beyond trade press |
| Trade-press RSS ✅ WIRED | IC News / CyberScoop / DefenseScoop / Federal News Network standard `/feed/` URLs | automates the capture-brief news section |
| SBIR.gov ✅ WIRED + RESILIENT | `https://www.sbir.gov/api/solicitation` | open topics with exact official narrative and dates; relevant rows can enter Prospective Horizon as PROGRAM evidence after API → Topics listing → explicitly stale official-snapshot continuity |
| DoD SBIR/STTR DSIP active topics ✅ WIRED | `https://www.dodsbirsttr.mil/topics-app/` | official Open and Pre-Release defense topics, including dates, program context, and published topic managers; PROGRAM-tier only |
| RegInfo Unified Agenda ✅ WIRED | `https://www.reginfo.gov/public/do/eAgendaXmlReport` | agency rulemaking timetable and stated action context before downstream procurement forms |
| DARPA opportunities ✅ WIRED | `https://www.darpa.mil/work-with-us/opportunities` | official defense research opportunity signals; PROGRAM-tier only |
| DoD budget exhibits ⚠️ WIRED · PARTIAL | `https://comptroller.war.gov/Budget-Materials/` | official machine-readable budget lines where JSON is published; the report names the intentionally partial exhibit coverage |
| ForeignAssistance.gov ✅ WIRED | `https://foreignassistance.gov/data` | President's Budget Request rows by exact funding agency and account; civilian scope rejects unresolved funding agencies |
| Treasury FiscalData ✅ WIRED | `https://fiscaldata.treasury.gov/api-documentation/` | MTS Table 5 agency outlays — money moving, not just appropriated |
| GAO Legal Products ✅ WIRED | `https://www.gao.gov/rss/reportslegal.xml` | bid protests + Comptroller decisions — incumbency-weakness signals |
| SEC EDGAR full-text ✅ WIRED | `https://efts.sec.gov/LATEST/search-index` | public competitors' own federal-exposure disclosures |
| FedRAMP marketplace ✅ WIRED | GSA marketplace data (GitHub) | cloud authorization status — who can sell today |
| GSA CALC+ v3 rates ✅ WIRED (rides DATA_GOV_API_KEY) | `https://open.gsa.gov/api/dx-calc-api/` | awarded labor-rate distributions — pricing intel |
| SAM Federal Hierarchy ✅ WIRED (rides SAM key, metered) | `https://open.gsa.gov/api/fh-public-api/` | agency org chart down to buying offices |

Not wired, with reasons: Challenge.gov (no verified public API — 2013-era endpoints dead),
SAM Assistance Listings (no verifiable public endpoint), SAM Wage Determinations (low capture value).

---

## Agency procurement forecasts (tools/api/forecasts/)

Forecasts are statutorily required projections of anticipated contract actions
(P.L. 100-656). They are AGENCY-STATED INTENT, not live opportunities, and are
revised or cancelled routinely. LILA enforces that distinction end-to-end:
forecast records flow ONLY into the signals layer (Early Signals in the brief),
never the live-opportunity flow, and the lint gate fails any build that renders
one outside a signals-labeled context.

### DHS APFS — investigated 2026-07-05, chosen approach: public JSON API

Probed https://apfs-cloud.dhs.gov/forecast/. Finding: the Angular frontend is
backed by a PUBLIC, unauthenticated JSON API — no account, no scraping needed:

    GET https://apfs-cloud.dhs.gov/api/forecast/?format=json&page=N

Returns a JSON list (~50 records/page). Verified record fields include:
apfs_number, requirements_title, requirement (description), naics
("541512 - ..."), organization ("CBP"), dollar_range {display_name},
award_quarter ("Q4 2026"), fiscal_year, estimated_solicitation_release_date,
anticipated_award_date, small_business_set_aside, small_business_program,
sbs_coordinator_* and requirements_contact_* (names/emails), contract_type,
contract_vehicle, current_state, published_date, previous_published_date.
Record detail page: https://apfs-cloud.dhs.gov/forecast/<id> (the source URL
carried on every ForecastRecord).

Politeness: identified User-Agent, throttled paging (0.6s), hard page cap,
response cached daily under data/cache/forecasts/. Adapter `dhs_apfs` ships
enabled=False until verified against the live site from the operator's
machine: set LILA_ENABLE_DHS_APFS=on in .env after `python3 run_api_test.py`
shows it green.

Adding another agency (State, HHS, GSA forecast tool): one file in
tools/api/forecasts/, one @register_source decorator, mapped to ForecastRecord.

The GSA Acquisition Gateway adapter is enabled by default beneath the aggregate
`forecasts` lane. It is broader than DHS APFS but does not claim every agency is
complete. Child-adapter attempts and coverage limitations remain attached to
the stored forecast payload and surface in the report's Research source
coverage band.

Verify any of them in 10 seconds, e.g.:
```bash
curl -s "https://www.federalregister.gov/api/v1/documents.json?conditions%5Bterm%5D=threat+intelligence&per_page=3" | head -c 400
```

---

## Tier 1 — one key you already have (SAM.gov)

Your existing `SAM_API_KEY` also works on:

1. **Contract Awards API** ✅ WIRED (adapter: tools/api/contract_awards.py — recompetes + incumbents into every FactPack). The adapter makes one combined SAM call. If the key is absent or that call fails, it uses the live, keyless USAspending award-search endpoint and labels every result `live_usaspending_fallback`; the primary SAM failure, endpoint, retrieval time, and narrower coverage are retained in `source_attempts` / `provenance`. The fallback screens the top 100 awards by dollars per NAICS lane, limited to awards acted on in the last five years, so it can miss an untouched long-running award and is subject to USAspending's 90-day DoD publication delay. It is continuity coverage, not a disguised SAM response.
   Docs catalog: https://open.gsa.gov/api/ → "SAM.gov Contract Awards". Note: FPDS.gov
   is decommissioned and its ATOM feed retires summer 2026 — this is the go-forward source.
2. **Entity Management API** ✅ WIRED + 3. **Exclusions API** ✅ WIRED (one adapter: tools/api/entity_vetting.py — every Target-stage org gets a SAM VETTED / EXCLUDED / NOT IN SAM badge).

If a call returns a role error, log into https://sam.gov → Profile → API keys and
confirm the key's role; public data roles are granted on request, no cost.

Verify:
```bash
source .env && curl -s "https://api.sam.gov/entity-information/v3/entities?api_key=$SAM_API_KEY&legalBusinessName=Carahsoft" | head -c 400
```

### SBIR maintenance continuity

The official SBIR API currently carries a maintenance advisory and has returned
HTTP 429. `tools/api/sbir_gov.py` does not retry around or evade that limit:

1. one documented `open=1&rows=50` all-open API pull (paged only when a full
   50-row page proves another page may exist), shared across all keyword lanes;
2. on failure, the current server-rendered `https://www.sbir.gov/topics?status=Open`
   listing, paged politely and labeled `live_official_topics_listing`;
3. if both live official surfaces fail, the newest dated official snapshot,
   labeled `stale_official_cache` with retrieval date, age, and both live errors;
4. if no official snapshot exists, a named failure rather than a false empty set.

SBIR.gov itself warns that its topic copies may trail the participating agency.
That limitation rides in the provenance of the listing fallback, and every row
links to the official SBIR topic/agency solicitation for operator verification.
The independent DoD DSIP adapter runs in the same sweep, so current official
Defense Open and Pre-Release topics remain available even when the SBIR.gov
service is rate-limited. DSIP records remain forming-demand PROGRAM signals;
they can never be promoted into the SAM.gov live-opportunity ledger.
SBIR.gov and Grants.gov use the same rule: collection alone never earns a card.
The current client taxonomy must match an exact official title or narrative,
the agency must pass the engagement scope, and a future stated open/close date
must exist. DSIP wins a duplicate DoD topic; SBIR.gov remains its fallback.

---

## Morning refresh — the repeatable Command Center path

Use the client already configured in Command Center and choose **Refresh Signal
Board**. Keep Mark43 on **All Federal** and Riverbed on **Civilian**. That one
action invokes the sanctioned seven-stage chain in order:

1. re-sweep every deterministic catalog lane and preserve the intentionally
   skipped manual/LLM-bearing lanes;
2. run deterministic relevance calibration;
3. compose the fresh board content;
4. re-pull every cited award;
5. record the verification observation;
6. press and gate the Federal Opportunity Pre-Assessment; and
7. persist the comparable snapshot and delta.

The editable preview is the same pressed artifact with `logo_editor=1`; logo,
seal, face, size, and header-label edits persist in the client presentation
manifest and survive the next refresh. Research source coverage lists every
cataloged lane from the current run. A failed, partial, fallback, stale, scoped-
out, or not-run source stays named rather than becoming a false empty result.

### SAM.gov point-of-contact fields (verified 2026-07-05, for the contact graph)

Verified against the recorded fixture `data/raw/sample_sam_response.json`, the live
parser `tools/api/sam_gov.py::_parse_contacts` / `map_notice`, and the POC data actually
retained on disk. **Do not assume beyond this list — these are the only POC fields SAM
returns and the only ones we persist.**

Each `opportunitiesData[]` notice carries `pointOfContact`: a **list** of dicts (multiple
POCs per notice — a notice routinely lists a primary and a secondary). Per-POC keys:

| Field      | Meaning                          | Notes |
|------------|----------------------------------|-------|
| `fullName` | person name as published         | may be `null` on a stub POC |
| `title`    | job title                        | often `null`; this is where **"Contracting Officer" / "Contract Specialist"** appears |
| `type`     | POC role designator              | observed values: `"primary"`, `"secondary"` — **only** these two; it does NOT encode CO/CS |
| `email`    | official-capacity email          | may be `null` |
| `phone`    | official-capacity phone          | may be `null` |
| `fax`      | fax                              | usually `null`/`""`; not currently mapped into `OpportunityContact` |

**Role semantics (important):** the API expresses role in *two* orthogonal places —
`type` (primary vs. secondary reachability) and `title` (the actual job: KO / Contract
Specialist). The contact graph must capture BOTH; neither alone is the "role."

Notice-level context available for each observation (from the same notice dict):
`noticeId`, `type` (notice type, e.g. `"Solicitation"`, `"Justification"`), `naicsCode`,
`fullParentPathName` (dot-delimited agency→office path, e.g.
`COMMERCE, DEPARTMENT OF.NATIONAL OCEANIC AND ATMOSPHERIC ADMINISTRATION.DEPT OF COMMERCE NOAA`),
`uiLink` (source URL), `postedDate` (original posted date — the historical context to
preserve alongside harvest date).

`OpportunityContact` (agents/schemas.py) already parses `name/title/email/phone/contact_type`;
the parser **drops** any POC with no name/email/phone (so all-null secondary stubs are
correctly excluded).

**On-disk POC availability — backfill v1 sources (verified, zero live API calls):**

| Source on disk | POC-bearing? | Notes |
|----------------|--------------|-------|
| `data/cleaned/searches_<client>.json` → `results["sam.gov"][].raw_payload.pointOfContact` | **YES — primary source** | the original notice is preserved under `raw_payload`; POCs live there |
| `data/cache/sam/*.json` (raw search cache) | **NO** (currently) | the 11 cached files are empty probe/healthcheck responses — 0 opportunities, 0 POCs |
| `data/cache/sam_notice/*.json` (detail cache) | **NO** | holds `description`/`attachments` only — no POC block (expected) |

Coverage is **sparse and client-dependent**: e.g. `searches_planetiq.json` has 1 sam.gov
notice with 2 usable POCs (Suzanna Espinoza / Cherron Bennett-Pettus at NOAA);
`searches_recorded_future.json` has 300 sam.gov notices but 0 with POCs in `raw_payload`
(that search path did not expand POCs). Backfill must therefore **log a coverage gap and
continue** for any notice lacking on-disk POC data — live re-enrichment is out of scope for
this feature.

**Conclusion:** the on-disk data fully supports the `ContactObservation` schema as spec'd
(person, title, primary/secondary role, agency/office path, email/phone channels, notice
ID, notice type, NAICS, source URL, posted date). No blocker; proceeding to schemas.

---

## Tier 2 — one signup covers two sources (api.data.gov, ~2 minutes)

1. Go to **https://api.data.gov/signup/** — first name, last name, email. Key arrives by email instantly.
2. Add to `.env`:
   ```
   DATA_GOV_API_KEY=<your key>
   ```
3. That single key authenticates BOTH:
   - **Regulations.gov v4** ✅ WIRED · 🔑 KEY LIVE (verified 2026-07-03 — key in .env, 11,827 docs on "threat intelligence")
   - **GovInfo** ✅ WIRED · 🔑 KEY LIVE (adapter: tools/api/govinfo.py — bills/committee reports/public laws full-text; the brief's funding-proof citations)

Verify:
```bash
source .env && curl -s "https://api.regulations.gov/v4/documents?filter%5BsearchTerm%5D=threat%20intelligence&api_key=$DATA_GOV_API_KEY" | head -c 400
```

---

## Tier 3 — two more 2-minute signups

**Congress.gov** (appropriations/NDAA language for capture-brief dollar anchors):
1. Sign up: **https://api.congress.gov/sign-up/** — email, key arrives by email.
2. `.env`: `CONGRESS_API_KEY=<your key>`
3. Verify: `curl -s "https://api.congress.gov/v3/bill?api_key=$CONGRESS_API_KEY&limit=1" | head -c 300`

**NVD** (optional key; raises rate limit from 5 to 50 req/30s for vulnerability enrichment):
1. Request: **https://nvd.nist.gov/developers/request-an-api-key** — name, email, org; key by email.
2. `.env`: `NVD_API_KEY=<your key>`

---

## Tier 4 — Apollo REST API (account you already pay for)

Closes the loop from handoff file → actual contact retrieval inside LILA.

1. Log into **https://app.apollo.io** → Settings → **Integrations** → **API** → Create key.
   (Docs: https://docs.apollo.io/)
2. `.env`: `APOLLO_API_KEY=<your key>`
3. Verify:
   ```bash
   source .env && curl -s -X POST "https://api.apollo.io/v1/auth/health" \
     -H "Content-Type: application/json" -d "{\"api_key\": \"$APOLLO_API_KEY\"}"
   ```
   Expect `{"is_logged_in":true}`.

---

## .env template (append)

```
# --- next-round data sources ---
DATA_GOV_API_KEY=
CONGRESS_API_KEY=
NVD_API_KEY=
APOLLO_API_KEY=
```

## Build order recommendation

1. SAM Contract Awards adapter (existing key; forced migration + biggest FactPack upgrade)
2. USAspending subawards (no key)
3. Trade-press RSS news layer (no key — feeds the capture-brief news section automatically)
4. Regulations.gov + Federal Register (one shared keyword-lane adapter)
5. Apollo API (turns the Target stage from handoff into retrieval)

---

## Entity resolution fields (partnering profile auto-resolve) — verified 2026-07-07

What the resolve-then-confirm profile can source per entity, probe-verified
against live responses (entity: CHUGACH INFORMATION TECHNOLOGY LLC, UEI
LP58KKM2GJF8). Every resolved value must cite source + retrieval date; the
human confirmation step is what turns any of it into attestation.

| Field | Source & endpoint | Verified state |
|---|---|---|
| Own award history → award_band | USAspending `POST /api/v2/search/spending_by_award/` with `recipient_search_text` | ✅ VERIFIED live: 59 own-award rows, exact-name recipient, per-row `NAICS` (code+description) and `Award Amount` → floor/median/ceiling computable and citable ($253 / $554K / $26.5M for the probe entity) |
| Entity business categories (incl. size + 8(a)-participant signals) | USAspending `POST /api/v2/recipient/` (keyword → id + **UEI**) then `GET /api/v2/recipient/<id>/` → `business_types` | ✅ VERIFIED live: returns e.g. `small_business`, `8a_program_participant`, `self_certified_small_disadvanted_business` (sic, API's spelling), tribally/minority-owned. ENTITY-LEVEL, not per-NAICS; derived by USAspending from SAM registration, no explicit as-of date on this endpoint — provenance must say "derived from SAM registration, retrieved <date>" and per-NAICS size claims from it are LOW-CONFIDENCE drafts at best |
| Size per NAICS + socioeconomic self-certifications (authoritative) | SAM Entity Management `GET /entity-information/v3/entities` `includeSections=entityRegistration,coreData,assertions` (rides SAM_GOV_API_KEY, **metered**) | ⏳ BLOCKED this session: shared key quota exhausted (429 until 2026-07-08 00:00 UTC); no cached entity payload exists in the repo (the one vetting artifact is itself all 429s). Expected fields per the API contract — `assertions.goodsAndServices.naicsList[].sbaSmallBusiness` (size PER NAICS), businessTypes socioeconomic list, `registrationDate`/`expirationDate`/`lastUpdateDate` for as-of — MUST be probe-verified at the next quota window before the resolver treats them as real. Until then: certifications stay human-entered (the no-non-SAM-inference rule stands), per-NAICS size resolves only as low-confidence USAspending draft |
| Vehicles held | USAspending IDV query (`award_type_codes: IDV_*` + `recipient_search_text`) | ⚠️ PARTIAL: the entity's own IDV contracts ARE queryable (16 rows: award IDs, agencies, `Last Date to Order`), but **no vehicle NAME field exists** — mapping PIIDs to catalog names (SEWP VI, 8(a) STARS III…) would be heuristic, not citable. Per design rule: `vehicles_held` stays **confirm-or-enter**; the resolver may attach the citable IDV list as context for the confirming human, never as resolved vehicle names. GSA eLibrary has no wired adapter (future source) |
| Entity keys (UEI/CAGE) | USAspending recipient search returns UEI today; SAM entity lookup returns UEI+CAGE (blocked, above) | ✅ UEI obtainable keylessly now; CAGE pends the SAM probe |

Ambiguity note (verified): USAspending recipient search returns one row per
recipient LEVEL (`C` child / `P` parent) for the same UEI — hierarchy
disambiguation, not distinct-company ambiguity; distinct-company collisions on
name-only search remain the human-selection case.
