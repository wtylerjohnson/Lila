# Federal Opportunity Pre-Assessment - internal signal-board format

External status: **NOT A CLIENT PRODUCT.** This frozen legacy format may feed
the Federal Market Map, but it cannot override or compete with LILA's sole external product contract in `docs/MARKET_MAP_CONTRACT.md`.

The client deliverable's committed gold standard is
[`docs/reference/federal_opportunity_signals.reference.html`](reference/federal_opportunity_signals.reference.html)
(operator-**LOCKED** 2026-07-13, "this is how every report must look in format,
down to every detail" — LILA's standardized output). Generate to THIS, exactly.
It supersedes the prose-heavy capture brief. Design system: `sb-*`
("signal board"). See also [lila-opportunity-assessment-doctrine] in memory.

## Non-negotiable ethos
- **No prose essays.** Zero thesis essay, zero fit-paragraphs. Every unit is a
  labelled figure, an ID, a link, or a name. The sanctioned narrative is the
  hero's 2–5 sentence executive synopsis, one terse evidence-bound **Client
  relevance** line on each competitor, teaming, and Horizon card, the single
  terse **Recommended sequence** action line at the close, and the footer
  provenance line. These lines are composed deterministically from the bound
  records and may introduce no new claim.
- **Exact figures, honestly qualified.** `$71.23M` not `$71M`; and every figure
  carries its precise, self-limiting scope: `OBLIGATED`, `NOT PIPELINE REVENUE`,
  `NOT MARKET SHARE`, `DIRECT AWARD RECORDS`, `AS LISTED IN SAM.GOV`, `LATEST`,
  `CURRENT`. Precision + honest scope reads as authority, never hedging.
- **Every claim is a linked record.** Award/notice IDs resolve to
  usaspending.gov / sam.gov. Links REINFORCE authority; they are never an audit
  trail or "verify later."
- **Agency seals are identification only** ("MARKS IDENTIFY SOURCE AGENCIES ONLY
  · NO AFFILIATION OR ENDORSEMENT").
- **Authority + motion are locked.** The header carries a self-contained,
  client-owned logo. The active-award ticker uses a duplicated continuous tape,
  pauses on hover/focus, and becomes a static wrapped list for reduced-motion
  users. Missing logo or motion/accessibility behavior is a release-lint failure.
- **Generated from pipeline data** (assessment + contact graph), never hand-authored.
- **The public product name is exact.** Every client-facing title, hero,
  Command Center label, and export name says `Federal Opportunity
  Pre-Assessment`. Historical/internal `signal_board` identifiers and stable
  file stems remain implementation details only.

## Organization-mark fallback

Every explicit client, company, or agency identity surface—including every
structured identity inside a Prospective horizon card—carries one stable,
inert `data-brand-*` key. A horizon card such as `Riverbed via RedSky` therefore
keeps Treasury, Riverbed, and RedSky as three independent mark targets; no key
is inferred from shortened title copy. Cached client marks, partner/company
marks, and agency seals fill those slots first. If a mark remains missing or
needs replacement, the Command Center's internal preview activates the same
slots as file and web-image drop targets.

Every client/entity target also exposes a bounded, persistent `Size` control.
Repeated instances of the same normalized identity resize together and the
saved percentage is embedded on the next sanctioned refresh/press. The GTM
publisher certification mark is a fixed template constant, not a client/entity
target: the editor labels it as locked and never makes it replaceable or
resizable.

An operator drop stores validated image bytes beneath
`clients/<slug>/assets/presentation/` and updates
`clients/<slug>/signal_board_presentation.json`; it never hotlinks and never
patches the certified HTML. Uploads accept PNG, JPEG, and WebP only; active SVG
content is deliberately outside this operator route. The editor requires its
local Command Center session token, and its multipart body is bounded before
parsing. The press trail and QA certificate bind the exact presentation
manifest plus referenced asset bytes, so touching a certificate cannot bless
stale HTML. Any digest change blocks release until the sanctioned refresh/press
embeds the new bytes and issues a matching certificate. The exported artifact
remains offline, script-free, deterministic, and self-contained. Company
overrides are client-scoped; repeated instances of the same normalized entity
update together. The header companion copy beside the client logo is also a
validated, client-scoped presentation value in that manifest. Its `Edit label`
control exists only in the loopback Command Center preview; the saved value is
embedded and certified on the next sanctioned refresh/press, while downloaded
HTML remains script-free. Faces use the same preferred-source cache and
manual-drop fallback. Every portrait target is keyed by the exact tuple
`name + organization + source system + source record`, not by display name.
Automation embeds only a hash-pinned local image carrying an HTTPS portrait
source URL for that exact tuple; a name-only match is forbidden. A missing or
invalid image renders initials plus `PORTRAIT NOT SOURCED`, while retaining the
same drag/drop and 50–200% size controls. This means a namesake can never
silently occupy the slot and report rendering never makes a network call.

When a selected SAM notice publishes a contact, the people band is titled
`PUBLISHED SOLICITATION CONTACTS` and shows the exact notice, published role,
record identity, and deadline. When no notice-level POC exists, an optional
separate fallback may show at most four `RELEVANT DECISION MAKERS TO VALIDATE`.
Those rows require an exact title, organization, official biography URL,
retrieval timestamp, and a relevance rationale tied to a route displayed in
the report. The band explicitly says `NOT SOLICITATION POCS`; an agency leader
is never relabelled as a procurement contact. With neither evidence class, the
band names the absence rather than inventing a person.

Reviewed fallback rows persist across compose runs in
`clients/<slug>/signal_board_people.json`:

```json
{
  "schema_version": 1,
  "decision_makers": [{
    "name": "Exact official name",
    "title": "Exact official title",
    "organization": "Official organization",
    "bio_url": "https://official.example/biography",
    "retrieved_at": "2026-07-20T00:00:00Z",
    "route_kind": "usaspending-award",
    "route_identity": "CONT_AWD_EXACT_GENERATED_ID",
    "route_label": "Displayed report route",
    "relevance_rationale": "Why the official role maps to that route"
  }]
}
```

An optional automatic face adds all three fields together:
`portrait_source_url`, a repo-relative `portrait_asset` below
`data/reference/portraits/` or `clients/<slug>/assets/portraits/`, and the
asset's `portrait_sha256`. Missing any one fails schema validation. No more
than four reviewed rows are accepted.

The route is evidence, not decoration. `route_kind + route_identity` must
resolve to a direct identity on a displayed primary card; an id found only in
nested machine evidence or the evidence dock is insufficient. Compose and
direct press independently reload the reviewed people file and compare the
exact `(name, organization, source system, source record)` person key and
route binding. A post-compose swap to another valid route therefore fails
closed even if the content/trail pair is rebuilt.

## Anatomy (top to bottom)
1. **Header nav** — client mark · client-scoped companion label (default
   `FEDERAL OPPORTUNITY PRE-ASSESSMENT`) · nav (BEST FIT ·
   COMPETITORS · TEAMING · HORIZON · COVERAGE · EVIDENCE) · GTM mark.
2. **Hero** — kicker `FEDERAL MARKET RESEARCH · <date>`; title
   `Federal Opportunity Pre-Assessment`; a 2–5 sentence evidence-derived
   executive synopsis in `hero_context`; capability chips (`sb-chip`, `.hot` =
   lead capability); marquee combined figure with scope qualifier. The synopsis
   names the nearest dated signal and restates only the linked opportunity,
   award, timing, and candidate-route rows below it; it is never free-form or
   LLM-authored.
3. **Federal organizations represented strip** — department seals
   (`sb-agency-mark`) + the identification-only disclaimer. This strip names
   organizations present in the selected records; it is not source coverage.
4. **Four signal cards** (`sb-signal`) — each `sb-label` + `<strong>` big value
   + `<b>` subtitle + `<small>` detail. Canonical set: Strongest route ·
   Timing cluster · Direct <agency> proof · Published contacts.
5. **Band 01 · Priority federal routes** (`sb-band`, meta `CURRENT DELIVERY ·
   EVIDENCED SCALE · LINKED RECORDS`). Each `sb-opp` article:
   `sb-rank` + `sb-seal-wrap` (agency code) + `sb-opp-title` (`Buyer × Incumbent`)
   + `sb-opp-account` + `sb-evidence-links` (award records) + four `sb-cell`
   columns, each `sb-label` + `<strong>` + `<small>`:
   **Contract scale** · **Current ends**/​**Window** · **Fit** · **Route**/​**Entry**.
6. **Band 02 · Competitor lanes** (meta `DIRECT AWARD RECORDS · NOT MARKET
   SHARE`) — `sb-competitor` cards: `<agency> · ACTIVE|ADJACENT` + award link.
   An always-visible `CLIENT RELEVANCE` line names the funded CORE lane and
   directs replacement and follow-on capture against that incumbent account.
   `ACTIVE` and `currently funds` require a valid current/future award end date.
   The directive is decisive but evidence-bounded: that dated award is the cited
   fact, while capture is the prescribed next move, not a claim that a
   solicitation is already open.
7. **Band 03 · Candidate teaming opportunities** (meta distinguishes `N
   MATCHED PRIME-SUBAWARD RECORDS`, `N CITED AWARD-HOLDER ROUTES`, and `N
   OPERATOR CANDIDATES`) — `sb-team-card`: `<client> × <partner>` +
   `sb-team-proof` + `sb-team-target`. Entry angles state the matched
   product/capability, buyer, and award period from the cited active corridor;
   subaward angles state the selected-play NAICS and exact prime-award identity.
   A row missing those structured facts is omitted rather than filled with a
   generic angle.
   A subaward basis must belong to one selected play (never a prime aggregate
   across plays), match that play's agency/component, share at least one
   client CORE term between the authoritative notice text and subaward
   description, carry its native subaward ID and official USAspending source,
   and resolve the prime PIID to one canonical award GID. The pairing remains
   a candidate partnership, not a claim of an existing relationship or access
   to the selected opportunity. Public proof reads `MATCHED PRIME-SUBAWARD
   RECORD` when both opportunity and subaward evidence bind, or `CITED
   AWARD-HOLDER ROUTE` for an active incumbent corridor. Each card's `CLIENT
   RELEVANCE` line converts that exact evidence into a capture directive:
   prioritize the matched prime for outreach, or make the partner-or-displace
   decision on a cited award holder.
8. **Band 04 · Prospective horizon** — a record-derived reading guide always
   defines the band, distinguishes its dates from solicitation deadlines, and
   states the verification action before the cards. An all-active-award band
   uses meta `DATED ACCOUNT WATCHLIST · NOT QUALIFIED PIPELINE`; a mixed band
   uses `CITED TIMING WATCHLIST · NOT QUALIFIED PIPELINE`. The deterministic
   teaser states the active-award-period count, dated span, nearest agency
   watchpoint, and required status/ownership check. It never infers a reseller,
   competitor, or relationship role from which organization marks render.
   Every card also renders an always-visible `CITED WORK` line before its date.
   Directly beneath it, an always-visible `CLIENT RELEVANCE` line explains the
   client action: defend or shape the account across extension, recompete,
   replacement, and sunset outcomes; move pre-solicitation against a forecast;
   or position before a cited recompete decision. A period end alone never
   proves a renewal or successor action. The section-level reading guide preserves
   the distinction between cited timing evidence and an open solicitation, so
   card directives do not repeat defeating caveats.
   "Always-visible" is literal: this line is not height- or line-clamped.
   C1 derives the public `job_context` from a structured
   `machine_evidence.job_context_basis` and binds that identical basis into the
   INTERNAL compose trail. Award windows, recompetes, and expiring-contract
   rows use only their published `description`; forecasts may fall back to the
   published title. The line may never be synthesized from client capabilities,
   matched terms, a recipient name, or another award. Procurement boilerplate,
   URLs, contacts, and monetary tokens are removed deterministically for public
   display while the exact source text remains in the inert basis. Missing work
   detail and one-word descriptions are labelled honestly instead of expanded.
   `sb-horizon-card` (`.hot` = visual timing emphasis, not a qualification)
   then renders each cited transition, evaluation, channel, or award-period
   signal. The guide is derived only from those rendered records and is never
   LLM-authored.
9. **Evidence-bound people** (`sb-pocs`) — face cards for published SAM
   contacts when the selected notice exposes them. Each card carries the
   opportunity, exact published role, record provenance, deadline, and an
   evidence-bound portrait or editable initials fallback. If there is no
   selected-notice contact, the separately labelled official-leadership
   fallback described above may render; it is never POC copy.
10. **Band 05 · Research source coverage** (meta `COMPLETE SWEEP UNIVERSE ·
    CURRENT REFRESH STATUS`) — all standard `run_searches.py` lanes from the
    central source catalog, in their canonical fan-out order. Each lane is
    projected only from the stored
    sweep's `results._attempts` and reads `RETURNED`,
    `RATE-LIMITED/FAILED`, or `NOT RUN THIS REFRESH`; carried-forward payloads
    never masquerade as a current attempt. A successful task whose result
    declares a live official fallback or stale official snapshot instead reads
    `RETURNED VIA OFFICIAL FALLBACK` or `STALE OFFICIAL SNAPSHOT`; it never
    implies that the primary API returned. Future attempted lane names append
    in stable order so adding a collector cannot silently omit it. The summary
    states standard, additional, attempted, returned, failed, and unrun counts.
    Raw provider errors never render. SAM Notice Detail/Resources and SAM
    Entity Management/Exclusions are disclosed separately as connected
    capabilities **not used in this refresh**, so their availability implies
    no contribution. GSA Acquisition Gateway is an enabled child of the
    acquisition-forecast lane and retains its own attempt/limitation lineage
    within that aggregate row.
11. **Band 06 · Evidence dock** (meta `PRIMARY FEDERAL RECORDS`) — the
    consolidated, cited source records. It remains distinct from sweep-wide
    coverage: coverage says what ran; the dock says what the report cites.
12. **Recommended sequence** (`sb-label` "From signal to action") — the single
    sanctioned terse action line ordering the plays (e.g. "protect and expand
    the active footprint; prioritize the matched primes for teaming outreach;
    map the next acquisition decision and buying office"). Nothing more.
13. **Footer** (`sb-footer`) — two provenance spans: sources + `data current
    <date>`, and "Independent assessment · federal marks identify agencies only
    · no affiliation / sponsorship / endorsement".

## Map to the pipeline
- opportunities/competitors/horizon <- assessment board + pipeline windows
  (notice IDs, award links, obligated/ceiling, POP end).
- teaming constructions + POCs <- the selected stored SAM notice and, for
  legacy Target artifacts, `data/review/<slug>.contacts.json` (`known_pocs`
  name/role/email/phone/opp id). Decision-maker fallback rows are reviewed in
  `clients/<slug>/signal_board_people.json` and injected into generated content
  on every compose, so refreshing cannot erase them. Each must cite an official
  bio.
- portraits <- client-scoped presentation override, an explicit hash-pinned
  person row, or `data/reference/portraits/index.json`, in that order. All
  automated entries remain exact-record-bound and offline at render time.
- coverage strip + marquee <- buyer-map agencies + summed obligated scale.
- research source coverage <- stored sweep `results._attempts` only; the
  evidence dock remains the curated citation set.
