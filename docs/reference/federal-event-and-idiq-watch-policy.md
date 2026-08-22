# LILA standing event and IDIQ watch policy

Recorded: 2026-07-22

Status: locked scope addendum for Candidate Review v1

## Decision

Candidate Review v1 has two permanent monitoring lanes:

1. A standing federal conference and event watch universe.
2. A client-specific IDIQ and contract-vehicle watch.

The watch universe is intentionally broader than the published report. Every
run searches the standing universe plus the approved client frame, verifies
current official evidence, and publishes only the records relevant to that
client. A watch seed is not a verified event, a vehicle is not automatically a
live opportunity, and neither is an automated recommendation to spend, attend,
team, pursue, or decline.

The verified conference and event results populate the existing Federal
opportunity calendar near the end of the report. There is no separate event
directory or ninth report section. The calendar is the final client-facing
action view; the method and Evidence dock may follow it as supporting material.

This policy supplements the Candidate Review contracts. It does not change the
eight report sections, the analyst-decision boundary, or the rule that active
opportunity claims require an official current record.

## Evidence boundary

| Observed item | Meaning | Candidate Review treatment |
|---|---|---|
| Organizer or recurring event name | Discovery seed | Search internally; do not render until the current edition is verified |
| Verified current event edition | Positioning or market-access signal | Render selectively in the Federal opportunity calendar with an official link |
| IDIQ, GWAC, MAS, BPA, BOA, or other vehicle | Acquisition route and demand container | Render selectively in Vehicle, partner, and market signals |
| Ordering-period end or award end | Research clock | Calendar item only when the source establishes the date; never label it a recompete by default |
| Official on-ramp, vehicle solicitation, or task-order notice | Potentially actionable procurement record | Eligible for Candidate opportunities for review only under the normal current-notice rules |
| Award or task-order activity under a vehicle | Demand and incumbent signal | Past-award or vehicle-watch evidence; not proof of an open opportunity |

FAR 16.504 describes an IDIQ as a contract under which the Government places
orders for individual requirements. The vehicle and its orders must therefore
retain separate identities in collection, deduplication, analysis, and display.

## Standing organizer sources

These sources are searched for every client. The first nine are authoritative
only for events they organize or host. GovEvents is an aggregator and remains
discovery-only.

| Source | Canonical root | Primary watch use | Typical federal audience |
|---|---|---|---|
| NCSI | https://www.ncsi.com/ | Government conferences, DoD/IC events, and installation expos | DoD, DIA, Intelligence Community, Air Force, Navy, Army |
| Federal Direct Access Expositions | https://www.fdaexpo.com/ | On-base technology expos | Military installations, DoD, and federal agencies |
| Federal Business Council | https://www.fbcinc.com/ | Agency technology expos, conferences, and symposia | Civilian agencies, DoD, and Intelligence Community |
| FORUM Events | https://events.govforum.io/ | Executive forums, innovation events, and awards | Federal technology and contracting leaders |
| GTRA | https://gtra.org/dc-events/ | Federal IT executive summits and custom events | Federal CIO, CISO, CTO, data, and security leadership |
| ACT-IAC | https://www.actiac.org/ | Public-private collaboration and executive forums | Federal CIO community, OMB, GSA, and agency CXOs |
| AFCEA International | https://www.afcea.org/ | Defense, intelligence, cyber, communications, and technology events | DoD, Intelligence Community, military services, and industry |
| NDIA | https://www.ndia.org/ | Defense technology and industrial-base events | DoD, military services, and defense industry |
| Government Executive Events | https://www.govexec.com/events/ | Agency leadership and executive summits | Senior federal executives and mission leaders |
| GovEvents | https://www.govevents.com/ | Broad event discovery and cross-checking only | Government-wide |

Tracking parameters such as `utm_source=chatgpt.com` are never stored in
canonical source URLs.

## Recurring event-family watch universe

Names below are discovery aliases, not proof that a particular edition is
scheduled. Every edition must resolve to an official organizer, agency,
agenda, registration, or venue page before publication.

### Priority 1: search first when provider or time limits constrain coverage

- AFCEA TechNet Cyber
- AFCEA TechNet Augusta
- AFCEA WEST
- GEOINT Symposium
- Space Symposium
- ACT-IAC Imagine Nation ELC
- FedScoop IT Modernization Summit
- Intelligence and National Security Summit
- Sea-Air-Space
- AUSA Annual Meeting
- Air, Space and Cyber Conference
- CDAO Federal Ready Summit
- Public Sector Health IT Summit
- A verified Treasury, fraud, or financial-security event matched to the client

Priority controls search order only. It does not create a universal attendance
recommendation or displace a more relevant agency-specific event.

### Defense, intelligence, military-service, and national-security families

- AFCEA TechNet Indo-Pacific
- AFCEA Homeland Security Conference
- AFCEA Defensive Cyber Operations events
- NDIA Cybersecurity Symposium
- Border Security Expo
- National Homeland Security Conference
- ISC West government and public-sector programming
- DGI AI for Defense Summit
- DoDIIS Worldwide Conference
- Navy Gold Coast
- Army small-business conferences
- DHS small-business conferences

### Civilian federal IT, acquisition, and executive families

- ACT-IAC Emerging Technology Summit
- ACT-IAC Acquisition Excellence
- FedScoop AI Summit
- GovCIO Digital Summit
- GovCIO AI Summit
- MeriTalk Cyber Central
- Government Executive CXO Summit
- Government Executive AI and Data Summit
- Federal News Network cyber events
- FCW Cyber Summit
- FedInsider AI Summit
- AI FedLab Summit
- Federal Identity Forum and Expo
- DOJ Technology Day
- Treasury industry days
- Do Not Pay vendor forums
- GSA FAST Conference
- NCMA World Congress
- PSC Federal Acquisition Conference
- SBA GovCon Summit
- Government Procurement Conference
- OASIS+ industry events

### AI, data, analytics, cloud, and platform families

- AI Expo for National Competitiveness
- Data Summit
- Gartner government symposiums
- AWS Public Sector Summit
- Microsoft federal executive, AI, and cloud briefings
- Google Public Sector Summit
- Oracle Government Summit
- Snowflake Government Symposium
- Databricks Public Sector Summit
- ServiceNow Federal Forum
- DGI CDAO Government Summit

### Health, HHS, VA, and military-health families

- HIMSS federal and public-sector health programming
- Digital Health Government Summit
- VA Healthcare Innovation Summit
- DHA industry days
- CMS industry days
- HHS Office of Small and Disadvantaged Business Utilization industry days
- Military Health System Conference
- AFCEA health IT events
- Health Datapalooza government programming
- VA National Acquisition Center events

### Geospatial, space, science, and environment families

- Esri Federal GIS Conference
- NOAA industry days
- NASA SEWP conferences and industry events
- NASA industry and technology days
- Satellite Conference
- SmallSat Conference
- Earth Observation Summit
- Geospatial Intelligence Forum

### Fraud, treasury, and financial-security families

- NCFTA cyber events
- FinCyber Today Summit
- Anti-Fraud Coalition events
- Treasury and Bureau of the Fiscal Service industry or vendor events

The standing NCSI, Federal Direct Access Expositions, FBC, FORUM, and GTRA
calendars are also searched for smaller installation-, agency-, and
component-specific events that may outrank a national flagship for a
particular client.

## Event collection and selection

1. Merge the standing watch universe with the approved client capabilities,
   agencies, components, NAICS, PSC, candidate accounts, vehicles, partners,
   competitors, buyer communities, and policy themes.
2. Select applicable watch targets deterministically from normalized mission,
   buyer, agency, and capability tags. Keep non-applicable targets in the
   manifest as scope-excluded; do not search or render them.
3. Search every required organizer/source lane and record zero-result,
   partial, failed, stale, and successful coverage.
4. Normalize aliases to one recurring event family, then treat each year or
   named edition as a separate event identity.
5. Verify title, organizer, exact edition, date or official range, lifecycle,
   location or virtual status, audience, and the link role before acceptance.
6. Rank for display using client relevance, date utility, buyer access,
   capability fit, partner/prime adjacency, and proximity to a candidate or
   vehicle signal. Ranking changes visibility only.
7. Render a concise `Why it may matter` and `Validate next` statement. Do not
   import generic ROI claims, attendance estimates, sponsorship ranges, or
   `must attend` language without exact evidence and analyst adoption.
8. Use official registration and speaking-submission deadlines as separate
   calendar items only when the official page establishes them.
9. Preserve all accepted records and coverage in the Evidence dock even when
   display limits omit an event from the calendar.

The calendar keeps procurement dates dominant. It may display no more than 12
conference or industry-event rows inside the existing 32-item calendar budget;
unused event capacity does not create filler.

## IDIQ and contract-vehicle watch

### How the watch universe is built

Each run combines:

- Vehicles, schedules, pools, domains, SINs, and contract numbers approved in
  the Analyst Layer.
- Parent IDVs found in the client's, incumbents', partners', and competitors'
  award history.
- Vehicle names and access routes cited by current notices, forecasts, agency
  program pages, and acquisition plans.
- Relevant government-wide vehicles and agency-specific IDIQs from official
  vehicle catalogs and program pages.
- Active on-ramp, refresh, pool-expansion, vehicle-solicitation, and task-order
  notices found in official procurement sources.

The existing `data/reference/vehicles.json` file and `tools/vehicles.py` are
legacy rollback and discovery-seed assets. Candidate Review v1 must never append
their standing table wholesale or treat the catalog's old `verified` flag as
current client evidence.

### Sources and coverage

- SAM.gov Contract Opportunities for public active notices and modifications.
- Official agency forecasts, procurement pages, vehicle program offices, and
  industry-day materials.
- GSA eLibrary and official GSA program pages for GSA contract awards,
  schedules, GWACs, IDIQs, holders, pools, domains, and ordering information.
- USAspending award and IDV records for parent-child identities, obligations,
  awardees, agencies, and task/delivery-order activity.
- Authenticated or operator-supplied eBuy and other restricted task-order
  exports when available.

Restricted systems are never represented as comprehensively searched when no
authorized result set was supplied. Vendor, reseller, prime, and media pages
may discover a lead but cannot alone prove official vehicle status, access, an
on-ramp, or a task-order opportunity.

### Required structured fields

Every published vehicle watch record carries, when established:

- Canonical vehicle name, aliases, vehicle/program identifier, and vehicle
  class.
- Managing agency, component, and program office.
- Scope, pools, domains, SINs, NAICS, PSC, and eligible ordering organizations.
- Current ordering status, ordering-period dates, and official on-ramp state.
- Awardees or holders and exact evidence supporting holder status.
- Client access posture: direct holder, evidenced reseller/partner route,
  access not established, or validation required.
- Relevant task/delivery-order and award activity with separate record
  identities.
- Ceiling or value only with a typed source basis; never convert a ceiling into
  expected revenue or pipeline value.
- `Records show`, `What it may suggest`, and `Validate next` language.
- Official source links, evidence IDs, retrieval time, last-checked time, and
  source-coverage receipt.

Access posture is descriptive and never an automated disqualification. A
missing direct seat may increase the importance of a partner route; the analyst
decides what to do.

## Report projection

- **Candidate opportunities for review:** only current official vehicle,
  on-ramp, or task-order notices that pass the normal candidate rules.
- **Vehicle, partner, and market signals:** the client-specific IDIQ/vehicle
  watch, current access posture, holder/partner routes, and demand signals.
- **Past awards and competitive analysis:** cited orders and awards under
  relevant vehicles, without duplicating the same record as a separate
  opportunity.
- **Federal opportunity calendar:** exact on-ramp deadlines, industry days,
  forecast dates, ordering-period research clocks, and task-order deadlines.
- **Evidence dock:** every accepted source, unsuccessful source-lane coverage,
  and the vehicle-to-order lineage.
- **Report and Command Center tickers:** only material, linked changes such as a
  new official on-ramp, changed due date, newly posted relevant event edition,
  or newly observed order activity.

## Freshness and change monitoring

- Public active notices are rechecked during every run and refresh.
- Official event calendars and current event pages use a seven-day maximum
  cached age; deadlines inside 30 days are rechecked during the run.
- Vehicle program status and on-ramp pages use a seven-day maximum cached age.
- Award and order activity carries the source's data-as-of date as well as the
  LILA retrieval time.
- A prior snapshot may be used only with an explicit stale/partial coverage
  state. No report silently converts an unavailable refresh into current data.
- Material changes are diffed against the prior client-bound snapshot so the
  ticker can show what changed without manufacturing urgency.

## Chunk 3A implementation contract

Before the renderer is built, Candidate Review v1 must add:

1. A versioned, digest-bound machine-readable watch registry containing the
   organizer sources and recurring event aliases above.
2. Deterministic merging of the standing registry with each approved event
   research frame, without allowing the registry to change the client scope.
3. A pure applicability step based on normalized mission, buyer, agency, and
   capability tags; non-applicable targets remain scope-excluded in coverage.
4. A structured `VehicleWatchRecord` contract and a client-bound vehicle-watch
   collection result with source coverage.
5. Separate vehicle, order, notice, award, and event identities.
6. Projection of vehicle records into Section 3 and their exact dates into
   Section 6 without duplicating active notices in Section 2.
7. Target-addressable coverage and a deliberately sufficient coverage budget;
   the standing watch universe must not collide or truncate silently.
8. Production wiring into assessment, refresh, persistence, receipts, and
   cache invalidation; a registry file with no production caller is incomplete.
9. Tests for aliases, organizer roles, official-link requirements, restricted
   source coverage, stale data, holder/access evidence, vehicle/order lineage,
   client bleed, deterministic output, and no automated disqualification.

Chunk 3A is additive. It must not weaken the already-reviewed candidate,
calendar, event-edition, attendance, lifecycle, attribution, or evidence rules.

## Read-only audit findings to resolve in Chunk 3A

The July 22 architecture audits found four implementation gaps. These are not
client-visible defects yet because Candidate Review v1 has not reached its
production renderer, but they must be fixed before that renderer is built.

1. The candidate engine rejects vehicle-only seeds, while the serialized
   document validator currently uses a weaker anchor predicate. One shared
   candidate-anchor rule must reject vehicle-, event-, legislation-,
   regulation-, news-, and discovery-only candidates at both boundaries.
2. Current SAM.gov notices do not yet carry a typed notice role. Add roles for
   end-user requirement, task-order requirement, vehicle establishment,
   vehicle on-ramp, and vehicle administration. Only end-user and task-order
   requirements can become current candidates.
3. Section 3 currently uses a generic evidence-bound claim. Add typed vehicle
   identity, parent-IDV/task-order relationship, access posture, lifecycle,
   checked time, evidence, and candidate cross-reference fields.
4. The calendar currently has candidate and event origins only. Add a vehicle
   signal origin and typed dates for on-ramp open, on-ramp close, ordering
   period end, and vehicle option-end research clocks. Ordering and option ends
   never become recompetes without separate explicit official evidence.

Vehicle signals must appear in Section 3 and the end-of-report calendar without
incrementing candidate counts or numbering. Deleting a candidate may remove a
cross-reference, but must not erase an independently evidenced vehicle signal.
An unnamed parent PIID must remain unnamed; the system cannot heuristically map
an identifier to a branded vehicle name.
