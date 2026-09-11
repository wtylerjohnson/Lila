# CONVENTIONS.md · patterns discovered from the code (audit 2026-07-10)

Reality in the code wins. When you extend a pattern, extend it here too.

## Storage map

- `data/cleaned/searches_<slug>.json` · the ALL-SCOPE sweep artifact; a
  focused gate mints `searches_<slug>.agency_<abbr>.json` instead (L19) and
  the whole artifact family (draft/qa/report/internal/Desktop) carries the
  same designator in dot-segment 2. Resolution is gate-driven via
  `agents.review.sweep_artifact_path` / `artifact_stem`; scoped-gate-with-
  missing-artifact fails loudly, never falls back.
- `data/reports/` · deliverables and their sidecars:
  `<slug>.federal_opportunity_assessment.html` (+ `.pdf`),
  `<slug>.assessment.{client,sales,internal}.html` (three views),
  `<slug>.agency_<a>.assessment.client.html`,
  `<slug>.federal_opportunity_assessment.draft.json` (resume-never-rebuy),
  `.qa.json` / `.qa.md` (DRAFT/RELEASE state source of truth),
  `.arbiters.json`, `<slug>.sweep_brief.html`. QA-failing files carry a
  `.DO-NOT-SEND` stamp in the name and never reach the Desktop.
- `data/review/` · human-gate artifacts: `<slug>.review.json` (strategy
  approval), `<slug>.qualify.json`, `<slug>.horizon.json`,
  `<slug>.live_requirements.json` (exact SAM requirement + attachment
  decisions), `<slug>.assess_approval.json` (activates Produce),
  `<slug>.flag_decisions.json` (flag adjudication), and Step 1 mastery
  sidecars `<slug>.dossier.json`, `<slug>.intake_yield.json`,
  `<slug>.intake_adversarial.json`, `<slug>.intake_readiness.json`,
  `<slug>.intake_retrieval.json`.
- `data/state/` · runtime: `job_logs/<job>.log` (disk-backed; server
  restarts cannot kill jobs), UI prefs, and agency-report composition cache
  `agency_report_content/<slug>.agency_<a>.content.json`. The cache stores a
  schema-validated, render- and lint-clean `CaptureBriefContent` keyed by a
  SHA-256 of the exact scoped artifact, client, agency, approved strategy,
  and capability profile. Failed prose is never promoted into the cache.
- `clients/<slug>/profile.json` · REQUIRED capability profile (no profile,
  no sweep). Scaffold: `python3 -m tools.capability scaffold --client "Name"`.
- Delivery: gate-clean artifacts copy to `~/Desktop/<Client>/` with pretty
  names (`<Client>_<Agency>_Focus_<date>.html`).

## Pursuit dossier attachment research (2026-09-09)

- `agents/decisions/dossier.py` carries the existing sweep's captured attachment
  text and file provenance in a separate `attachment_research` analysis context.
  It verifies the discovery producer's full-text/file-ID/file-hash fingerprint
  before including text. Original URL and retrieval metadata travel unchanged;
  a checksum does not attest their freshness or authenticity.
- The prompt includes at most 14,000 captured characters, original length and
  truncation status, plus a bounded relevance excerpt only when every segment
  occurs in the captured text. Text is associated with the file bundle, not
  falsely attributed to an individual file. Known notice/inventory mismatch
  yields a named diagnostic without usable text. An unreconfirmed inventory
  is explicitly labeled, never silently treated as current.
- This is unreviewed research. `full_description`, persisted `source_depth`,
  strict Assess evidence, human requirement/inventory reviews, and lead tiers
  remain owned by their existing paths. The dossier's model verdict cannot
  approve a requirement or clear a hold. The existing sweep retains the source
  research; no new persistence or approval surface is introduced.

## Search-plan derivation (keyword doctrine, 2026-07-12)

- The operator's approved capability, technology, and explicit search terms
  ARE the SAM.gov opportunity text screen. Agency, set-aside, and NAICS
  keywords remain procurement lenses and never enter full-text matching.
  `agents/review.py::effective_query_terms` is the one owner: sam.gov specs
  always search the current capability vocabulary (revise/amend_terms also
  persist it into the spec so the editor shows what will run); a stale packet
  cannot override an edit at search time. usaspending specs keep their curated
  vendor/reseller vocabulary and web specs their credential probes on
  purpose; forcing capability keywords into those lanes corrupts award
  matching. This flips the earlier "curated terms are left alone" compromise
  for sam.gov only.

## Keyword Strategy Workshop (2026-07-12)

- The strategy gate's keyword editor is a three-lane workshop (In the search /
  Needs your judgment / Kept out). It rides the EXISTING `revise()` /
  `amend_terms()` contract; no new endpoint or approval state. `kept_out` is
  an editable field (`EDITABLE_FIELDS`), and `Keyword` carries additive
  provenance (`origin` system|consultant|edited, `edited_from`, `source`,
  `note`) that `_clean_keyword` preserves round-trip. The intake composer
  emits none of these; defaults keep every legacy packet valid.
- Removal is non-destructive: `×` moves a term to `kept_out` (label "Move out
  of search", never "Delete"), restorable, with an Undo. The near-miss tray
  (`near_misses`, already produced by intake) is the "Needs your judgment"
  lane; its Use/Edit-first/Keep-out actions promote to keywords or kept_out.
- The "Approve search vocabulary & review NAICS →" action SAVES the vocabulary
  and advances to NAICS review; it is not a new approval leg. Keyword approval
  stays distinct from NAICS and scope, and the single strategy lock remains
  the one gate. Classification bands (core/adjacent/buyer/mission/structural)
  are a display grouping of the real `KeywordCategory` enum, not a migration.

## NAICS Boundary Workshop (2026-07-12)

- The strategy gate's NAICS editor is a three-lane workshop (In the boundary /
  Needs your judgment / Kept out), the same shape as the Keyword Workshop and
  on the SAME `revise()` / `amend_terms()` contract, no new endpoint or
  approval state. `inferred_naics: list[str]` remains the AUTHORITATIVE search
  boundary; two additive fields ride alongside it:
  - `naics_meta: list[NaicsEntry]` — per-code consultant metadata (`code`,
    `title`, `role` core|boundary, `origin` system|consultant|restored|edited,
    `rationale`, `note`). A boundary code with no entry reads as
    system-inferred. The persistence owner binds metadata to
    `inferred_naics`, so the two never drift; `naics_meta` is derived from the
    workshop's in-boundary lane on save (`inferred_naics == [e.code]`). New
    intake generation uses a strict output subtype and emits one ordered,
    grounded entry per inferred code. Legacy packets may still omit metadata;
    the workshop recovers only exact-code rationale already retained in the
    packet and otherwise labels the explanation as missing rather than inventing
    one. A normal save requires every in-boundary code to have an explanation;
    the vocabulary-to-NAICS navigation may preserve an honestly incomplete
    legacy state so the operator can resolve it in the workshop. Persistence
    retains a blank rationale verbatim; it never substitutes a generic phrase
    that the UI could mistake for evidence.
  - `kept_out_naics: list[NaicsEntry]` — codes moved out of the boundary but
    preserved for restoration (never destructively deleted). Restore recovers
    code, title, role, rationale, provenance, and note.
  Both default empty at the storage schema, so every legacy packet loads
  unchanged. New intake output populates `naics_meta`; both kept-out lanes begin
  empty and fill only through operator decisions. New inference also rejects
    duplicate/empty boundaries and must retain every valid six-digit NAICS code
    supplied on the form; omission fails the generation schema rather than
    receiving a generic post-model rationale. New keyword output likewise
    begins with system provenance and no invented operator edit/note history.
    `NaicsCode`, `NaicsEntry`, and both fields live in
  `agents/decisions/schemas.py`; `_clean_naics_codes`,
  `_clean_naics_entries`, and `_reconcile_naics_state` in `agents/review.py`
  own validation and the active/kept-out invariant.
- “Grounded” new-intake NAICS metadata has two enforcement layers: the output
  contract requires a substantive, distinct rationale per code plus a nonblank
  strategy source trail; the inference prompt and unchanged human review gate
  own the semantic judgment that each rationale is actually supported by that
  evidence. The validator does not pretend that character counts prove truth.
- Validation before persistence: every boundary, metadata, kept-out, and search
  spec NAICS code is exactly six ASCII digits; role and origin are closed literals.
  Malformed or null containers, non-string entries, codes, roles, and origins raise
  `RevisionError` (HTTP 400) in both `revise()` and `amend_terms()`, never a
  successful revision that silently drops the decision. Duplicates dedupe by
  code. The UI additionally WARNS (never auto-changes) on same-family overlap
  (shared 4-digit family) and materially-broadening additions (a new 2-digit
  sector).
- `_reconcile_naics_state` is the single persistence owner:
  `inferred_naics` wins membership, active and kept-out lanes are disjoint,
  omission cannot erase kept-out history, an inferred-only move-out preserves
  the prior record, and restoration recovers that record. A restore can replace
  the historical metadata in the same revision only with explicit
  `origin=edited` provenance; a generic Add placeholder cannot erase it. Both
  revision paths journal complete before/after `naics_meta` and
  `kept_out_naics` state.
- `_reconcile_searches` keys on stable source identity, not prior list
  truthiness: SAM.gov and USAspending specs always mirror edited
  `inferred_naics`, including empty-to-restored transitions; persisted web
  specs are always cleared of NAICS.
- The former hard-coded MDR/541513 nudge is generalized into a data-driven
  `NAICS_RECONSIDER_RULES` table (keyword pattern -> code + reason) that
  surfaces a code as a "Needs your judgment" candidate; a rule NEVER adds a
  code. NAICS remains a coarse boundary filter; the workshop copy and inspector
  never imply NAICS establishes capability fit.

## Step 1 company mastery (2026-09-06)

- Name-only entry is first-class: `run_intake.py --client "Name"` and
  `POST /api/intake` / `POST /api/run {step: intake}` with only
  `client_name`. The intake form is optional enrichment.
- Identity is resolved **before** deep scrape or structured web probes
  (`agents/intake/identity.py`). A website-guess below
  `IDENTITY_BIND_MIN_CONFIDENCE` (0.72) is not scraped. Two official-domain
  candidates abstain with one question rather than contaminating the dossier.
- `run_step1` follows the existing `engine or research_engine()` convention:
  if `do_web` and no research engine was injected, it constructs
  `research_engine()`; if no strategy engine was injected, it constructs
  `DecisionEngine()`. `--no-websearch` still takes the honest offline
  identity path. Forgetting to pass engines is not a reason to abstain.
  True two-domain ambiguity still abstains.
- Sidecars beside the review packet (additive, legacy packets unchanged):
  `<slug>.dossier.json`, `.intake_yield.json`, `.intake_adversarial.json`,
  `.intake_readiness.json`, `.intake_retrieval.json`.
- Yield at intake wraps `tools.query_terms.term_yield`. An empty or
  unreadable notice store is named empty; it is never a list of false zeros.
- The review gate mechanism (`request_approval`, `decide`, `approve.py`,
  `load_approved`) is preserved. Auto-passthrough is explicit and default ON
  via `LILA_ENABLE_INTAKE_AUTO_APPROVE` / `is_enabled("intake-auto-approve")`.
  It only calls `decide()`. It does not invent `scope.preset` and does not
  launch searches. `--no-auto-approve` or the env set to off restores the click.
- Adapters (`agents/intake/adapters.py`) copy evidenced dossier fields onto
  `IntakeStrategy` and a hybrid.frame_lanes-shaped retrieval sidecar.
- After identity bind, website-first deep ingest drives the dossier
  (`tools/scrape/site.py` `SITE_MASTERY_MAX_PAGES` = 24). `run_intake.py`
  `--max-pages` and `research_company` default to that budget; a CLI 5
  is floored back up. The crawl seeds `/en/` and bare hubs (products,
  solutions, customers, case studies, partners, compare/alternatives,
  about, industries, product-family paths) and follows in-domain
  nav/footer/product-hub links. Interstitial / JS shells are
  `render_failures`, not "pages read". E2 passes only on usable
  rendered pages (or an honest no-site bind). Render fallback chain:
  JS render (realistic desktop Chrome UA, stealth init, settled-content
  wait, not networkidle-as-success) then usable static HTML, then
  site-anchored web research that must cite official-domain URLs for
  products/customers/competitors. Wikipedia/Crunchbase blurbs stay
  secondary. Sitemap.xml / robots.txt Sitemap + HTML nav discover
  product, customers, case-studies, partners, and compare/vs URLs
  under the bound domain. Env flags (defaults in
  `tools.scrape.site.js_render_config`): `LILA_INTAKE_JS_RENDER`
  (on), `LILA_INTAKE_JS_STEALTH` (on), `LILA_INTAKE_JS_WAIT_MS`
  (20000), `LILA_INTAKE_JS_MIN_CHARS` (80),
  `LILA_INTAKE_JS_RENDER_CAP` (8). Missing browser or a WAF-empty
  body fails closed via `render_failures`. Generic SERP blurbs must
  not invent offerings or competitors when the official site is thin.
- Structured probes are site-anchored after bind: products, competitors,
  and customers start on the official domain. Probes also seek named
  product families (CloudVision AGNI / Guardian for Network Identity,
  DANZ Monitoring Fabric, 7050X), stated NAICS, and namesake collisions.
  Extract cannot recall strings the site/probes never captured.
- Competitors and customers are first-class dossier lists with
  evidence_ids from official-site text. Retrieval copies them onto
  `tier1_rival_names` and a customers list. They are not buried only
  in probe prose.
- Dossier offerings, keywords, and retrieval units are short discrete
  product or capability names (EOS, CloudVision, AGNI, 7050X). Probe
  essays, homepage load-errors, sentence fragments ("Also has a
  telemetry"), nav glue (CaseStudies), third-party IdP names
  (OneLogin), schedule/ticker/header fragments, GSA / SEWP / award
  IDs (47QSWA18D008F, 0119Y), bare numbers, and the placeholder
  excerpt "citation" never become offerings or ledger text.
  Customers are buying orgs (Microsoft, US Army, Barclays,
  Citigroup, Morgan Stanley, Hardis), not story titles
  (Customer Success Story, Going Big), job titles (Group VP),
  solution-brand phrases (Cognitive Campus), industry segments
  (hedge funds, financial services), proof-page junk (proof
  points, PDF, troubleshoot workloads, ease of deployment, bare
  numbers), nav chrome (Login, Toggle Navigation, Series Spine,
  Wi-Fi), or social widgets (Meta, Facebook, Yahoo). Customer
  extract is windowed around proof language and capped
  (`MAX_CUSTOMERS` 24): fewer correct orgs beat hundreds of
  chrome tokens. Section labels, truncated names, geography-only
  tokens, and all-caps role phrases (Named Customers, Revenue
  Share, MARKET DATA FEED PROVIDER, Costa Rica, Cloud Titans,
  Hardis Grou) are not customers. Allowlisted banks (Barclays,
  Citigroup, Morgan Stanley) and Activ Financial / Hardis Group /
  Microsoft promote from official evidence even without a
  customer-hub URL. Nav/people/page titles (Management Team, Senior
  Management, Platforms page, Detection and Response Overview)
  and case-study customer codes (JCT600, RACSA, Intuit) are not
  offerings. Slogan / datasheet / antithesis / numbered-header
  titles (CloudVision Data Sheet, SolutionBrief, From network
  security to secure networks, Secure Networks vs. Network
  Security, 1. Operating System, `&amp;` titles) are not
  offerings. Competitor names are rival companies (Cisco,
  Juniper, Aruba, Darktrace), not page titles (Darktrace
  Comparison) and not English openers (Here, This, What). Rival
  evidence must be a compare / vs / alternative / competition
  claim, including product-page sentences such as "Competition
  for the Cisco Nexus 1000V". Promotion reads official scrape
  pages even when the competitor web probe returns no findings.
  A darktrace host plus 10-K / HPE body is still dropped. A
  named-other-rival path (ndr-darktrace-comparison) never
  evidences Cisco or Juniper, even with a strong Unlike-Cisco
  excerpt. Cisco and Juniper must still be claimed when a
  competitor-comparisons hub names them: promote from that
  scrape page text (not the short evidence excerpt alone),
  including when the hub is already in evidence. Do not
  recall Cisco/Juniper from a Darktrace URL. Prefer
  historically-dominated sentences; a generic compare hub
  that names Cisco/Juniper is enough if the excerpt does
  not deny them. `/en/products/network-detection-and-response/competitor-comparisons`
  is a seeded hub. A /news Broadcom VMware blurb is not
  a VMware-rival claim even when the press room also says vs
  (URL≠claim). Rival names are companies, not SKU fragments
  (Cisco Nexus / Nexus 1000V). DoD, DoDIN, and DoDIN APL are
  certification program names, not offerings. Allowlisted banks
  (Barclays, Citigroup, Morgan Stanley) and Activ Financial
  promote from official proof text even in all caps. Bank
  names that no longer appear on the live site or the latest
  10-K come from the bound entity's own SEC roster
  (`agents/intake/sec_customers.py`): S-1 / 424B4 / 10-K text
  for the CIK resolved from SEC company_tickers.json, never an
  invented CIK. Promote only from customer-roster sentences
  ("customers include", "financial services organizations such
  as", "end customers such as"). Evidence URL is the filing.
  Prefer a filing that still names the roster when the latest
  10-K dropped those names. Do not stop at the latest 10-K when
  its customer section is category language only (AI Neoclouds,
  Cloud and AI Titans, financial services organizations,
  government agencies, Our Customers Our). Walk bound-CIK
  filings (submissions, older year files, EDGAR browse atom,
  full-text search) until a named roster is found; prefer 2014
  424B4 / S-1. Category and segment phrases are not customers.
  The SEC walk is deterministic and runs after scrape, before
  LLM probes, so a 300s customer-probe timeout cannot starve
  the roster or wipe site case-study names (Activ / Hardis /
  Microsoft). The customers probe runs last among structured
  probes. Compare / competitor-comparisons / ndr-darktrace
  URLs are queued ahead of CaseStudies PDF paths (the live
  `/assets/data/pdf/CaseStudies/` slug matches `casestud`,
  so those PDFs are crawlable) so Darktrace cannot fall out
  of the 24-page budget. Compare-link discovery is capped so
  it cannot starve named CaseStudies PDFs. Live filenames
  are Activ_Financial and Hardis-Group (plus Case-Study
  variants). `_fetch` must accept application/pdf and
  extract text (filename stub if parse is empty) so those
  PDFs enter the 24-page budget. A customers probe with
  no cited findings must not wipe those deterministic
  promotions.
  Rival evidence for Cisco, Juniper, and Darktrace must be a
  compare-shaped URL with a matching claim, not /products/eos
  ISE context or a 451 whitepaper PDF. ExtraHop compare URLs
  may stay. Rival vendors/products (ExtraHop, ClearPass,
  ForeScout, EyeSegment) are not offerings. Fragment customers
  (Product Testimonials, Lancaster Coun, County Government,
  Arista Extensib, Edge Threat Management) are rejected.
  Microsoft evidence prefers a customer/case-study URL when
  one exists. IPO underwriter / cover tables
  (Morgan Stanley, Citigroup, Barclays as bookrunners) are not
  buyers. News conference blurbs and synthesized "Named in
  S-1/10-K" paraphrases are not bank proof.
  `citation_is_official` stays the company domain; SEC URLs
  bind through `is_bound_sec_filing_url` for customers only.
  Soft-fail if SEC is unreachable. Support / careers / company
  chrome (A-Care, Quick Facts, Corporate Responsibility,
  Events Calendar, founder concatenations, department lists,
  Forrester Wave, Data Center Network Solutions) is not a
  customer. Prefer /case-study|testimonial|customers/ plus the
  SEC roster. Case-study PDFs and official testimonials
  (Activ, Hardis, Microsoft when evidenced) still promote.
  Truncated case-study card titles (Walsh Universi, Noodles Compa,
  PB/UAX Case Stu) and section chrome (Wireless FAQ,
  Real-World Deployments) are not customers. Customer and
  testimonial hubs are crawled before news/blog inside the
  24-page budget. Core NAICS
  334210 (telephone apparatus) does not fail E5/E8 just because
  Aviation is kept_out or the composer also listed 334210 as a
  near miss; aviation codes 336413/488190 stay parked. When official
  customer-hub or compare evidence
  already names an org or rival, promote that name with the
  matching eid. Do not empty those lists to avoid a title bug.
  Product evidence prefers official-site URLs over third-party
  PDFs. If Aviation or Arista Aviation is in kept_out, park
  canonical aviation NAICS 336413 and 488190 on
  `kept_out_naics` even when the digits never appear on the
  official site (website-first crawls do not cite them).
  A rival platform (VeloCloud) is not an Arista offering unless the
  source asserts Arista sells it. Tool-meta tokens (WebSearch),
  URL/date shards, and lone acronym scraps are not products. NAICS that
  appear in probe or page text are kept with real evidence_ids. An empty
  NAICS list stays empty when nothing is cited; E5/E8 must not treat
  that as a win while strategy invents NAICS (split-brain is a block).
  Dossier **core** NAICS (`dossier.naics`) are the search/lead-gen lane.
  Namesake industries (Aviation 336413/488190, music/records 5122xx) are
  moved to `dossier.kept_out_naics` and `strategy.kept_out_naics`, aligned
  with Aviation/Records `kept_out`. They are omitted from `inferred_naics`.
  Forecast/solicitation 6-digit fragments (685031) are not NAICS.
  Core codes that conflict with Aviation/Records excludes or strategy
  `near_misses` fail E5/E8. `kept_out` rows (OAS, Aristan, Aviation)
  require an excerpt that actually contains the name; no wrong-eid bind.
  Prefer the named offering DANZ Monitoring Fabric when evidence has it.
  Name-collision exclusions from boundary probes seed `kept_out`
  (Records / Aviation / Aristan / OAS Aircraft Support style when
  mentioned); NYSE, CIKs, section headers, and the bare company
  name stay out. Workshop edits remain the operator path. A related
  Government Sales LLC is recorded as a federal-path affiliate and does
  not break or hard-block a correct public-domain bind.

## Pipeline shape

- One pipeline per client, six steps; `run_*.py` entry points at repo root;
  the dashboard (`ui/server.py`) is a window onto the artifacts, never a
  second source of truth. Steps map to commands in `ui/server.py::_step_cmd`;
  jobs run via `start_job` with disk-backed logs.
- Everything client-visible derives from `AssessmentDocument`
  (`agents/reports/document.py`): ONE deterministic join of
  notices ⋈ triage ⋈ dossiers ⋈ qualify ⋈ market evidence, keyed on
  source_id. The LLM composes prose ONCE (`CaptureBriefContent`); the three
  renderers consume the object and never re-derive data.
- The legacy live-notice compatibility path checks provenance at every
  promotion boundary: Qualify reads only `results["sam.gov"]` records whose
  record-level source is `sam.gov`; FactPack creates NOTICE-tier opportunity
  facts only from verified SAM candidates; and `AssessmentDocument` admits
  only SAM records to the pursuit board (legacy source-less SAM rows remain
  readable, but any explicit non-SAM source is rejected). General-web leads
  remain in `results["web"]` for developing-intelligence research and never
  become live solicitations.

## Command Center home shell (2026-08-07)

- The cross-client home is implemented by
  `ui/static/command-center-home.js` and
  `ui/static/command-center-home.css`. `ui/index.html::renderHome` mounts it;
  client, calendar, target, and contact views explicitly deactivate it before
  rendering their established surfaces.
- The home shell is a composition layer over the existing observational APIs:
  `/api/depository`, `/api/calendar`, `/api/targets`, and `/api/ticker`. It does
  not post approvals, runs, scope changes, or releases and does not own a second
  copy of pipeline state.
- Stage and action controls open the existing client workspaces. The client
  workspace remains the only place where operator gates and report presses are
  taken. Navigation workspaces may summarize registered artifacts, but report
  buttons must open the exact server-provided path through `/report`.

## Scope workstations (2026-07-13)

- A scope workstation is exact client identity plus one canonical scope
  designator. `all` is explicit; focused ids use
  `agents.review.scope_designator`. Agency order is canonicalized by the
  curated agency catalog (the established DHS + CISA family remains
  `agency_dhs_cisa`), never by display order or fuzzy autocomplete.
- `agents.workstations` owns the immutable identity/read model. Scope is a
  tuple-backed typed value, labels are derived, and malformed registry rows
  fail loudly. A missing registry is read as absent and is never created by a
  navigation request. The product-default All Federal possibility is derived
  only; it is not a persisted operator decision.
- The legacy review packet contributes exactly one current workstation. A
  registry row or old artifact may establish history, but no noncurrent row
  inherits a boundary, progress, approval, report, or Target state. Historical
  sweep/report counts are separate from active progress.
- Current sweep progress requires a regular readable JSON artifact whose
  embedded exact client and `search_scope` match the workstation. The sole
  compatibility exception is an unqualified pre-workstation sweep for the
  exact legacy-current All Federal row: that older artifact may omit
  `search_scope`, which resolves only to All Federal. An explicit scope always
  wins, and focused/native sweeps never receive this default. Filename
  existence alone never advances a phase. A current sweep also proves the
  accepted keyword/NAICS boundary: new artifacts carry `strategy_revision`,
  while a post-revision legacy artifact needs an aware `generated_at` at or
  after the packet's `revised_at`. An older same-scope sweep is `stale` and
  returns the workstation to Search. If a legacy filename exists with a
  mismatched binding, the Command Center withholds the entire downstream body
  and offers an exact-scope search refresh; it does not relabel legacy truth.
- `GET /api/client/<slug>/workstations` and
  `GET /api/client/<slug>/workstation/<id>` are additive, no-store read
  surfaces. Slugs resolve through the exact review-packet identity; unknown or
  archived ids never fall back. Dormant detail payloads are strict whitelists
  with no strategy or downstream fields. Configure/Search payloads contain
  only the client foundation, exact current boundary, and pre-sweep actions;
  they never inherit old evidence, approvals, documents, sidebar targets,
  release, or Target state.
- Full legacy-current detail is assembled from one exact packet/sweep snapshot
  bound to the selected workstation. The route checks both artifact hashes
  again before responding and returns 409 on concurrent replacement instead of
  mixing scopes. Explicit malformed scope dictionaries fail; only a missing
  legacy `search_scope` retains the historical All Federal default.
- `POST /api/client/<slug>/workstations` is the sole public Tranche 3
  creation door. Its JSON body has exactly `scope` and boolean
  `clone_baseline`; the public door requires `clone_baseline: true` and
  rejects the no-clone dead end. Creation is coexistence, not migration: an
  alternate native workstation becomes independently addressable while the
  legacy-current workstation remains current. A request for the exact
  legacy-current id returns `created: false` without writing a registry,
  receipt, packet, Markdown, or journal. An orphan native prefix at that same
  id fails closed for explicit migration, and replay of an already-complete
  historical native owner is observational.
- Creation is serialized per client and commits against the exact baseline
  packet bytes. It prepares the immutable receipt, native review packet,
  rendered review Markdown, and creation journal first, rechecks the source
  packet hash, and promotes the workstation registry last as the visibility
  switch. Ordinary failures remove only files introduced by that attempt;
  a deterministic receipt-first prefix left by a hard stop can be completed
  only by an identical retry against the same source hash. Creation replay
  validates the exact first journal event and the required Markdown rather
  than silently rebuilding either. Runtime native ownership is narrower and
  intentional: the exact active registry row plus creation receipt establish
  identity, while the mutable packet has its own SHA-256 generation token.
- A baseline clone copies the complete validated strategy, not merely the
  keyword and NAICS lists. It starts a new Pending packet at revision zero and
  copies no decision, approval, sweep, evidence, report, release, pointer, or
  Target state. Existing `naics_meta` records and order survive exactly;
  missing inferred codes are appended in inferred-boundary order. Only an
  exact NAICS keyword rationale may supply an affirmative explanation. A
  near-miss reason remains negative review context and cannot be promoted into
  an in-boundary rationale; unexplained codes stay visibly blank for operator
  judgment.
- Native review Markdown names the exact workstation and directs approval
  through that selected Command Center view. It must never print the
  unqualified `approve.py` command, which still addresses only the legacy
  packet. Legacy review Markdown retains the established CLI instruction.
- Exact workstation shelves contain only exact report families. Unqualified
  working intelligence and Desktop files remain client-global, and document
  hide/order preferences are partitioned by workstation id.
- The canonical browser URL is
  `/client/<slug>/workstation/<workstation_id>`. The top segmented rail changes
  the complete payload, URL, history entry, sidebar, and operating body. It
  issues GETs only. A monotonic selection token prevents a late response from
  repainting a newer workstation.
- Every automatic full-view auxiliary request carries `workstation_id` and
  the server rebinds it to the exact current packet/sweep. Change digests must
  embed the same scope designator. Client-only recompete calendars are
  withheld from exact workstations until their schema carries a scope. A late
  competitor/recompete response cannot reopen an overlay after navigation.
- Jobs are bound to their originating workstation. `/api/run` rejects a
  posted dormant/stale id, resolves legacy omission only when one current row
  is unambiguous, records that id, and passes the exact client/id to the child
  for a second packet/scope check. Search rechecks revision and scope before
  fan-out, so a concurrent boundary edit aborts before quota or model work.
- Every native strategy mutation names the workstation and supplies the exact
  packet SHA returned by the last read or write. The server also captures the
  registry and receipt SHA tokens. Under the packet-writer and native-creation
  locks, the persistence owner revalidates both ownership tokens and the
  current packet SHA before an atomic write, then returns the new packet SHA.
  Native and legacy resolution are forced to their authorized families, so an
  orphan native-looking filename can never become an implicit fallback.
- A native Tranche 3 run must name the workstation and may launch only the
  `searches` step. The child receives exact client, workstation, packet SHA,
  strategy revision, registry SHA, and receipt SHA bindings. Immediately
  before source fan-out, search holds the same locks as strategy writers,
  rereads those generations and the exact scope, and submits the first tasks
  before releasing the locks. A native search may write its scoped sweep,
  research picture, and scoped sweep snapshot; it suppresses client-global
  forecast stores and snapshots, coverage-ledger writes, contact-graph
  harvest, Assess pointer refresh, and Outreach growth. Native Assess
  decisions, report production, release, and Target remain closed for their
  later tranches.
- Shared client foundation is a separate, client-global area in every exact
  workstation payload. Its server allowlist contains only the exact intake
  submission, capability profile, baseline strategy Markdown, and resolved
  client brand mark; reports, sweeps, approvals, outreach, and Desktop files
  cannot enter through a glob. The file reader remains confined to `data/`
  except for exact `clients/<canonical-slug>/profile.json`, rejects traversal
  and unsupported binary content, and returns allowed images as data URLs.
  The browser strips all Markdown attributes, retains only an explicit tag
  allowlist, admits only HTTP(S) links with `noopener noreferrer`, escapes
  JSON/text, and ignores late foundation responses after a workstation switch.
- **Analyst Layer** is the operator-facing product name for the judgment
  surface that shapes pursuit strategy, keyword vocabulary, NAICS boundary,
  target-agency notes, set-aside angles, and search inputs. Keyword Strategy
  and NAICS Boundary remain workshops inside that layer. Do not reduce this
  offering to utility copy such as "refine terms." Naming does not create a
  second gate: the established strategy approval and post-approval amendment
  contracts remain unchanged.
- While the legacy compatibility adapter is active, its mutable scope selector
  renders as the selected workstation's read-only binding. Scope creation and
  workstation-native strategy mutation belong to the explicit creation
  tranche; switching must never call `set_scope`.
- Dashboard GET assembly disables unresolved-entity telemetry. Merely opening
  or switching a workstation must not alter `unresolved.log`, gates, pointers,
  report bytes, registries, brand-mark caches, corrupt outreach files, or any
  other artifact. Brand adoption/rebuild remains an explicit command workflow;
  the scoreboard banner GET is render-only.
- Observational outreach reads preserve malformed curated bytes and return an
  unavailable state; they never convert corruption into an apparently empty
  rail or rename the file during navigation.

## Assess evidence ledger and release gate

- `agents/assess/contracts.py` defines three additive, typed ledgers:
  `LiveSolicitationLedger`, `OpportunityThesisLedger`, and
  `PartnerOpportunityLedger`. `agents/assess/ledger.py` deterministically
  populates them while the existing `AssessmentDocument` remains the report
  compatibility surface. Search, dossier, and Horizon runners refresh this
  sidecar best-effort after their primary artifact is safe on disk; a ledger
  failure never blocks those established outputs.
- Immutable run envelopes live at
  `data/state/assess_runs/<slug>--<exact-name-hash>/<run-hash>.json`. The
  readable slug is collision-protected so distinct client names cannot share
  a pointer or artifact directory. A scope-specific
  `<designator>.current.json` pointer is replaced atomically and carries the
  envelope hash. An existing run id with different bytes is rejected rather
  than overwritten. The Control Room exposes the validated current run at
  `GET /api/assess/run?client_name=...` and summarizes its live, Horizon,
  partner, and required-coverage state at the Review gate.
- Live records require primary NOTICE-tier evidence on an HTTPS SAM.gov host.
  Forecasts, awards, news, and web leads are structurally unable to satisfy
  that contract. Metadata-only triage can prioritize depth but cannot produce
  `BID_NOW`. That class additionally requires explicit active state, a future
  deadline with more than date-only same-day time remaining, retained
  authoritative requirement text, an exact source span explicitly reviewed
  by a human against approved core-capability terms, complete required SAM
  census, and a reconciled attachment inventory. A nonempty inventory also
  requires a human decision bound to its exact hash; zero attachments must be
  proved by a recognized SAM resources schema. Every metadata discard remains
  provisional and visible in the census with its reason.
- Developing opportunities have no minimum or presentation quota. Every
  thesis carries evidence, an inference chain, a projected window, a
  falsifier, and a watch trigger; counterevidence is explicit when found, not
  fabricated to fill a field. Moderate/strong strength requires authoritative
  primary evidence. Horizon fact-bank rows retain structured buyer scope (or
  an explicit government-wide marker); focused strict projections reject any
  affirmative fact that is not buyer-bound to the operator-selected agency.
- Partner opportunities link to one or more Assess record ids and require
  capability-specific or acquisition-access evidence; broad NAICS proximity
  cannot satisfy the contract. The v1 adapter re-joins a document play to the
  raw sweep and promotes only exact live-record links backed by same-buyer
  USAspending subaward descriptions sharing approved core-capability terms.
  Same-NAICS award pools remain context. Vehicle, certification, scale, and
  exact-incumbent blockers fail closed until their partner-specific source is
  wired; the missing source appears in required coverage.
- `AssessRun` binds the approved profile version and operator-owned scope to
  the three ledgers plus source coverage. It defaults to PENDING and can
  release only after explicit approval; mandatory-source gaps require an
  explicit partial-release approval. The default assessment plan treats the
  live census, developing-opportunity sources, Horizon review, and partner
  sources as expected attempts. Missing attempts are not equivalent to zero
  findings.
- Every Assess contract model is frozen and every nested collection is a
  tuple after validation. Constructors still accept ordinary lists and JSON
  serialization still emits arrays. `AssessRun.can_release()` round-trip
  validates defensively, so an unvalidated `model_copy(update=...)` cannot
  bypass approval metadata or ledger/scope consistency.
- The operational release gate is `agents/assess/approval.py`. Human approval
  is atomically persisted at `data/review/<slug>.assess_approval.json` with a
  canonical fingerprint of the exact gate scope, selected sweep object, and
  complete capability profile. Missing, legacy, unreadable, or stale approval
  fails closed at the Command Center run API, full/client/agency report
  promotion, and FOA HTML/PDF download boundaries. Direct runners remain
  usable but emit DRAFT or `.DO-NOT-SEND` until the current evidence is
  approved; revocation is recorded atomically rather than deleting the audit
  event. The sales teaser remains separately gated and unaffected.
- An approval click is idempotent when every approval fact except the
  regenerated timestamp is unchanged. The prior approval bytes and timestamp
  remain authoritative; a transient optional-manifest failure preserves the
  same-binding prior manifest only when all other approval facts also match.
  Changed notes, bindings, manifests, revocation state, or partial-release
  state remain substantive and write a new approval.
- `POST /api/review/assess-approve` additively returns `refresh_note` on
  successful approval and revocation. Refresh failure never rolls back the
  durable human action, but it is logged and reported in the response;
  ordinary CURRENT validation continues to hold stale projection inputs
  closed. The refresh is creation-free and cannot activate a pointer.
- The live opportunity-specific gate is
  `GET/POST /api/assess/requirements`. The operator sees the exact retained SAM
  span, evidence identity, deadline, and every attachment. The server compares
  run id, evidence id, excerpt, and inventory hash again at POST time; changed
  evidence returns a conflict and cannot inherit the prior click.
- Primary JSON inputs written by search, re-screen, dossier, market-refresh,
  qualification, and Horizon flows use `tools.artifacts.atomic_write_json`:
  serialize + validate + fsync a same-directory temporary file, atomically
  replace the canonical artifact, and retain valid old/new bytes by content
  hash under a hidden `.history/` sibling. Sidecar materialization remains
  best-effort after the primary commit.

## Fact registry

- `agents/reports/facts.py` · `FactPack`; fact ids are `F{n}` rendered as
  `[F#]` citations. Every quantitative client claim cites a fact; lint_text
  validates citations against the pack.
- Per-figure provenance (2026-07-16): every Fact carries `source_system`
  (usaspending | sam | apfs | agency_doc | analyst), `source_record_id`,
  and `retrieved_at` (UTC), stamped by ONE owner
  (`facts._stamp_provenance`) from the fact's own source URL, its value
  payload's closer stamps (buyer-map `retrieved_at`), the strict ledger's
  per-record evidence times, and the sweep artifact's `generated_at` as the
  run-metadata fallback. Unrecoverable = `freshness: UNKNOWN_FRESHNESS`,
  never a guess. Facts are never persisted; recovery happens at build, and
  `tools/fact_freshness_backfill.py` is the read-only audit over stored
  sweeps (sweep bytes are hash-bound into approvals and must not be
  rewritten). `tier` IS the verification tier; `verification_tier` is a
  read alias. The composer payload excludes all provenance fields so
  retrieval vocabulary cannot leak into client copy; the INTERNAL sidecar
  renders the per-figure trail.
- Freshness gate (`agents/reports/verification.py`):
  `FRESHNESS_MAX_AGE_DAYS = 14` (config constant). Cited figures older
  than that at render time, UNKNOWN_FRESHNESS figures, and
  disputed-and-unresolved figures are violations in all three runner
  batteries; they flag the build and force DRAFT through the existing
  state machine. The data-current date is min(retrieved_at) across
  rendered figures and is structurally never hand-set (the Signal Board's
  DATA_CURRENT token fills only from figure provenance; divergence beyond
  the threshold fails in build_model, before render). Dispute arbitration
  rules are fixed in docs/VERIFICATION_POLICY.md; resolutions are
  append-only JSONL at `data/state/verification/<slug>.resolutions.jsonl`
  (contact-graph observations pattern; latest row governs; a disputant
  can never be the decider).
- Capability evidence is PRIMARY (L1): teaming/recompete/buyer claims are
  description-verified; NAICS lane totals are boundary context only (R11).
- `client_invariance_check` (facts.py) runs on every build: lane-filtered
  evidence that would surface identically for any client is demoted.

## Entity crosswalk (L14)

- `data/entities/crosswalk.json` · one canonical door per corporate entity
  (name variants, M&A lineages, DBAs); every alias row carries a source_url,
  no sourceless lineage claims. Pure suffix/punctuation variants need no
  alias row; the normalizer collapses them.
- `tools/entity_lineage.py` · THE company-name module: `normalize_company`,
  `company_matches` (word-boundary, never substring),
  `resolve_entity`/`door_key`/`canonical_name`/`entity_note`. Every path
  that groups or tags company names uses these (competitive build,
  subaward prime rollups, product tagger, buyer-map incumbents, lint).
  Single-token aliases resolve exact-only ('Arbor' never claims 'Arbor Day
  Foundation'); containment needs a 2+ token alias; longest alias wins.
- Merge behavior: same-door rows merge with summed dollars and ONE lineage
  note ("X and Y records are a single corporate door, not two");
  `lineage_note` renders internal-only; evidence rows keep their
  as-of-contract names; the gated sales model carries neither.
- Unresolved names pass through unchanged and log to
  `data/entities/unresolved.log` (count, first-seen) — the crosswalk grows
  from real traffic. Tests are hermetic via LILA_ENTITIES_DIR (conftest).
- `lint_entity_lineage` (report battery): two rows resolving to one door
  without a merge FAIL the build.

## Forecast layer (L15) and recompete calendar (L16)

- Forecast adapters live in `tools/api/forecasts/` (one file + one
  `@register_source` per agency; `forecast_sources()` drives the sweep loop).
  Records are PROGRAM-tier agency-stated intent: they flow only into the
  signals/horizon layer, never the live-opportunity flow
  (`lint_forecast_context`). Global store
  (`data/state/forecast_store/<source>.json`) maintains record_hash/
  first_seen/last_seen and classifies change events (date_moved_closer,
  date_slipped, value_grew, disappeared) that the horizon fact bank cites.
  Coverage ledger (`data/state/forecast_coverage.json`) records every pull
  and logs agency gaps honestly (`gaps_for`).
- Recompete calendar (`tools/api/recompete.py`, step "recompete"):
  USASpending-only expiring-award pull per client profile, incumbents
  resolved through the entity crosswalk, obligated vs ceiling never
  conflated, attack value 0-100 decomposed per factor with weights in
  `data/reference/recompete_weights.json`, client-as-incumbent rows
  segregated to DEFEND. Standing artifact
  `data/state/recompete/<slug>.json`; internal dashboard view + top-N feed
  into `_recompete_facts` as tier=program. "Recompete posted" language
  without a notice ID fails `lint_notice_tier_claims`.
- Evidence tiers on facts: `Fact.tier` = "program" (stable records and
  projections, forming sections only) or "notice" (live SAM claims, gated).
- Developing Horizon is an optional report layer, never a base-assessment
  dependency. `run_horizon.py` composes/refines a DRAFT and only its explicit
  CLI/UI approval act may promote it. The report path only loads an approved,
  deterministically valid set; missing, draft, empty, unreadable, or invalid
  Horizon artifacts are omitted and the base assessment continues. Zero items
  is a valid reviewed result. Verified signal text and source must match one
  canonical fact-bank row exactly (whitespace-normalized); forecast-screen
  coverage may accompany affirmative evidence but cannot support a thesis by
  itself.
- Every new Horizon draft carries an additive binding fingerprint over the
  exact gate-designated sweep artifact, scope designator, and complete
  capability profile. Approval, refinement, and report loading recompute that
  binding. A scope, sweep, or profile change makes the prior approval
  ineligible and requires recomposition; the base assessment continues without
  Horizon. Legacy artifacts without a binding remain readable at the review
  gate but are never approval- or render-eligible.
- New Horizon drafts use backward-readable schema v2. Every selected signal
  carries its exact fact-bank evidence id; every fact row carries the bank
  retrieval time; and every non-empty item separates lifecycle stage,
  falsifier, watch trigger, and monitoring cadence. `watching` remains the
  renderer field but is assembled mechanically from trigger and cadence.
  V1 artifacts remain readable by legacy review/report paths but cannot enter
  the strict thesis ledger, including zero-item shells; strict zero findings
  require a fully validated, bound, human-approved v2 attempt. An approved
  Horizon carries a timezone-aware approval time and reviewer identity.

## Single owners (2026-07-11, post-review)

- Release verdict: `agents/reports/release.py::release_state` (see
  CONTRACT_SURFACES). Approval fingerprint + drift: `agents/assess/binding.py`
  (both the Assess and Horizon gates consume it). L19 naming:
  `agents/review.py` helpers (review_dir-parameterized; the dashboard
  delegates). Low-level atomic text replacement: `tools/atomic_io.py`;
  validated JSON with content-addressed history: `tools/artifacts.py`.
  Company identity:
  `tools/entity_lineage.py` (L14). One job per client at a time:
  `ui/server.py::start_job` refuses concurrent same-client jobs; QA sidecars
  certify their exact html via html_sha256.

## QA: remediation then lint

- `agents/reports/remediation.py` R1-R15: auto-fix / suppress / flag; render
  NEVER blocks. Flags adjudicate via `data/review/<slug>.flag_decisions.json`.
- Lint battery (`agents/reports/lint.py`): identity/terminology/counts/
  client-bleed/em-dash/white-label/contact-rendering (+ scoreboard
  populations, L13). run_agency_report chains the battery explicitly and
  stamps `.DO-NOT-SEND` on violation; the banner endpoint runs the
  scoreboard lint warn-only.
- DRAFT vs RELEASE: state lives in the `.qa.json` sidecar; RELEASE requires a
  current evidence-bound human Assess approval + zero open flags + arbiter
  consensus + the operator's `--release` switch (the dashboard Produce button
  passes it). DRAFT renders watermark + QA appendix.
- Stable-name full-federal HTML is releasable only when its same-family QA
  sidecar is at least as new as the HTML, declares `state: release`, and does
  not declare `release_eligible: false`. Missing, unreadable, stale, or DRAFT
  sidecars fail closed. The HTML remains previewable on the document shelf,
  but HTML download and PDF export return a release refusal. Across stable
  full and alternate-view families, a newer stamped build supersedes any older
  clean artifact.
- Build economics: draft saved every audit round (fingerprint
  `[n_facts, date]`); promote presses verify the prior fix list instead of
  re-rolling audits; patch loops escalate tiers and keep-best.
- Agency-focus availability: `run_agency_report.py` reuses its composition
  cache only on an exact fingerprint match. A compose/fact-pack timeout or
  exception is loud but sheds only the prose layer; the deterministic
  `AssessmentDocument` still renders as DO-NOT-SEND and never copies to the
  Desktop. A prose render/QA failure retries without prose, and a failure of
  that deterministic path emits a minimal DO-NOT-SEND safety HTML. Lint
  exceptions are blocking violations, never silent passes. Scoped sweep,
  cache, final HTML, and Desktop writes use a same-directory temporary file
  plus `os.replace`; scoped persistence and Desktop delivery failures are
  nonfatal after the in-memory/report artifact exists. `--no-compose`
  intentionally bypasses both compose and cache as before.
- An explicitly supplied `searches` dict is the `AssessmentDocument` source
  of truth and bypasses gate-based sweep resolution. Disk resolution remains
  strict when the caller does not supply evidence. Canonical SAM populations
  admit records from the `sam.gov` array only when their explicit source is
  `sam.gov` (or absent for legacy rows); explicit non-SAM rows cannot enter
  pursuit totals, watchlists, snapshots, or monitor partnering.
- Full-federal availability: after the gate-designated sweep is resolved,
  FactPack or primary composition failure sheds the optional analysis layer
  and renders `AssessmentDocument` through `render_assessment` as an explicit
  deterministic DRAFT. The visible fallback band and QA sidecar mark it
  release-ineligible even when the operator requested `--release`; it never
  exports or copies a client release. Review, retry, arbiter, and editor
  failures retain usable content and force the normal DRAFT path. Report,
  draft, review, arbiter, QA, and internal-file writes use same-directory
  temporary files plus `os.replace`, preserving the prior artifact when a
  replacement fails. The Command Center report command is unchanged.

## Templates and rendering

- Three views (client/sales/internal) via `render_assessment` +
  `render_scoreboard` (`agents/reports/views.py`). `gate_for_sales` deep-
  copies the model and PHYSICALLY removes client content (masked ids, blank
  dollars) so leak tests hold mechanically; every locked element carries a
  typed `Locked` placeholder with real verifier fields.
- Three-view availability: failure or timeout in either optional model-compose
  phase sheds the prose layer and continues through the deterministic
  `AssessmentDocument`. Every selected view is written with a
  `.DO-NOT-SEND.html` suffix, no PDF or Desktop copy is produced, and the
  command exits nonzero. Intentional `--no-compose` behavior is unchanged.
- Counts in client copy are `{{COUNT:*}}` tokens resolved at render time
  (see CONTRACT_SURFACES.md). Machine-checkable render marks:
  `data-counts-verified` on counts, `data-pop`/`data-pop-count`/
  `data-pop-name` on scoreboard population figures (L13),
  `data-entity` markers on structural elements.
- Identity assets (GTM logo, cover art, client logo slot) are fixed
  constants of the locked template; `lint_brief_identity` proves presence.
  Brand marks dropped into the Desktop folder remain inert until the operator
  runs `python3 tools/brand_marks.py --adopt` (or uses a future explicit
  operator action). Client/banner GETs never move marks or start a views job.

## Events lane: three sources, one band (tiers B and C wired 2026-07-30)

- **tier C** (`events_conferences.harvest_tier_c`) reads the curated
  conference roster (`data/reference/event_conferences.json`, env override
  `LILA_EVENT_ROSTER_DIR` for hermetic tests): AUSA, AFCEA, HIMSS and peers,
  the events that never post as SAM notices. ZERO LLM at press time -
  model-assisted URL discovery/refresh is a separate operator-scheduled
  step; the press consumes the stored roster, gates it by ENGAGEMENT SCOPE
  (the term path could ship an Army conference into a civilian engagement
  on the word "network"), and re-verifies URLs LIVE like every tier: the
  harvest returns candidates with url_verified=False, and the roster's
  quarterly verification is a schedule, never press-time proof. Stale
  roster keys ride the receipt. Locations come from
  `location_from_span` - City, TWO-LETTER-STATE only, because the first
  regex stored "Logos Give to AFA Air, Space" as a location; garbage never
  beats absence.
- **industry-day deadlines have three states** (audit fix): an EMPTY
  deadline is UNKNOWN, never past - kept while sam.gov still lists the
  notice (last_seen equals the store's newest), dropped as stale otherwise;
  a deadline closed within RECENTLY_CLOSED_DAYS (21, mirroring tier A) is
  kept and flagged registration-closed; only genuinely old deadlines drop.
  The one-comparison version treated "" as past and silently dropped 103 of
  696 matched gatherings (14.8%, measured live).
- Hermetic tests: conftest isolates LILA_NOTICE_STORE_DIR,
  LILA_EVENT_ROSTER_DIR, and sets LILA_ENABLE_ACQ_GATEWAY=off (an isolated
  forecast cache turns every test-time cache miss into a live ~5-minute
  Gateway crawl; one press test cost 301s before the guardrail). A test
  that needs the adapter re-arms it locally.

## Events lane: two sources, one band (tier B added 2026-07-30)

- `pack.events` is one list in one shape (`events.EventRecord`), fed by two
  sources that never diverge:
  - **tier A** (`events.harvest_tier_a`) reads the DAILY EXTRACT: today's
    whole active universe with full description text.
  - **tier B** (`industry_days.harvest_from_store`) reads the ACCUMULATED
    NOTICE STORE, applying the stricter industry-day doctrine that excludes
    site visits and pre-proposal conferences as procurement mechanics rather
    than gatherings.
  The store is the right source for tier B because 6.4% of notices vanish
  from the extract every three days, and the store is what accumulates them.
- THE TWO SOURCES SPEAK DIFFERENT COLUMN VOCABULARIES and nothing in the
  type system says so. The extract is CSV headers (`NoticeId`, `Title`,
  `Type`, `ResponseDeadLine`, `Department/Ind.Agency`); the store is sqlite
  columns (`notice_id`, `title`, `notice_type`, `deadline`, `agency`).
  Handing one to the other's reader matches nothing and reports a confident
  zero rather than failing. `industry_days._STORE_TO_EXTRACT` is the single
  translation point; add to it rather than teaching a screen a second
  dialect. `events.load_extract_rows` is a GENERATOR, so a second consumer
  gets an exhausted iterator: tier B materializes its rows before screening.
- `events.score_relevance` is the ONE owner of "a relevance line bound to a
  pack fact", for both tiers. Deciding what IS an event and deciding whether
  THIS client should see it are separate jobs: `industry_days.collect` does
  only the first, and measured 2026-07-30 it returned 67 distinct future
  industry days of which zero connected to the pressing client's book.
- Every URL is rebuilt from `agents.reports.links.build_sam_notice_link`
  (the store's own `url` column is the login-walled workspace form that
  `lint_sam_workspace_links` bans) and verified LIVE by `events.verify_urls`
  before shipping. Harvest returns CANDIDATES; only the caller verifies.
- Tier B never asserts an event day: `event_start` is always None and a date
  lifted from prose rides inertly in `relevance_basis`, which no renderer
  prints and `validate._pack_date_keys` does not read. The band renders TBD.
- The band is CONDITIONAL (`validate.EVENTS_BAND_ORDER`): present only when
  the pack carries verified events, because an empty band is padding. The
  disclosure lives in `pack.events_screen` instead, whose counts are all
  real, so an empty band states what it screened rather than reading as a
  hole in the report.
- Tiers fail independently and neither sinks the press; events are additive.

## FCEB coverage census (2026-07-30)

- `data/reference/fceb_agencies.json` is the CISA Federal Civilian Executive
  Branch list (102 agencies, operator-supplied 2026-07-30); `tools/fceb.py`
  is its catalog owner, following the scope-presets/source-catalog pattern:
  one reference file, one owner module, tests binding the count.
- Matching is EXACT after the owner's normalizer (comma-inverted department
  names, parentheticals, US/United-States), never fuzzy: token-subset
  matching produced a false negative (IBWC) and a collision class (SSA vs
  SSAB, NEA vs NEH) on the live store. Shapes the normalizer cannot derive
  get explicit `aliases`; an unmatched store agency is REPORTED in the
  census, never guessed onto the list.
- `census()` emits one row per FCEB agency EVEN AT ZERO, crediting notices
  by agency OR subtier (OCC and FERC live only as subtiers), so "searched,
  nothing found" and "never looked" stay distinguishable. CLI:
  `python3 -m tools.fceb` (zero-LLM diagnostic; bash is fine).
- The civilian engagement preset admits 78 of the 102 only via the scope
  resolver's fail-open rule. `test_the_civilian_scope_admits_every_fceb_agency`
  is the tripwire: tightening scope resolution fails that test with a named
  agency. Do NOT wire FCEB recognition into `resolve_department` as a "fix":
  under the civilian preset's enumerated department list, recognition
  without membership FLIPS those agencies from admitted to refused, silently
  narrowing an operator-approved gate. Scope membership stays operator-owned.
- OPERATOR RULING 2026-07-30: civilian scope means NON-MILITARY BUYERS, not
  strictly FCEB. `tools/fceb.py::NON_FCEB_CIVILIAN` names the approved
  extra-FCEB buyers (US Courts, GPO, Library of Congress, Architect of the
  Capitol, GAO, Senate, Postal Service, Smithsonian, National Gallery,
  Holocaust Museum, ODNI); the census classifies every unmatched store
  agency as excluded defense, approved non-FCEB civilian, blank, or
  UNACCOUNTED — and unaccounted is a prompt for an operator decision, never
  an automatic admission. A second tripwire pins their scope admission.

## Source-mesh disclosure rules (2026-07-30)

- CAPPED LANES SPEAK ONE DIALECT: `tools/api/base.py::cap_disclosed(matched,
  cap, key=...)` returns the kept slice, the true `total_matched`, and
  `truncated: True` only when something was actually cut. Every enrichment
  lane with an output cap (congress, dod_contracts, trade_rss, watchdogs,
  fedramp, gao_legal, sec_edgar, contract_awards incumbents) merges it into
  its payload. Order is PRESERVED, never re-ranked at the cap: re-sorting
  changes which items survive, and sweep approvals bind to exact sweep
  contents. A new capped lane uses the helper, not a hand-rolled `[:N]`.
- A LANE THAT GATHERED NOTHING IS NEVER `ok`. A missing API key returns an
  `error` key (attempt_row normalizes it to a failure and the coverage band
  reads RATE-LIMITED/FAILED), never a bare `note` that projects as RETURNED.
- STRUCTURALLY NOT-APPLICABLE lanes (hierarchy and budget_pressure when the
  engagement has no agency scope) return the `LANE_NOT_APPLICABLE` sentinel
  as their summary: the runner writes NO attempt row, so the contract-fixed
  coverage vocabulary renders NOT RUN THIS REFRESH - the truth - without
  touching the projection rules. hierarchy also spends metered SAM calls,
  so the skip saves quota.

## Always-swept market mesh (operator order, 2026-08-18)

- Three market-intelligence children ride the required source mesh and are
  attempted, receipted, and coverage-counted on EVERY sweep:
  - `sam_mirrors` (tools/api/sam_mirrors.py): category-term sweep across the
    SAM.gov mirror services HigherGov, GovTribe, GovChime, G2Xchange,
    OrangeSlices AI, and GDI Consulting (GDIC, gdicwins.com). Mirrors double
    as usaspending.gov proxy corroboration (roster `roles`).
  - `esi_reseller_catalogs` (tools/api/esi_catalogs.py): esi.mil plus the
    Carahsoft, immixGroup/EC America, and FCN vehicle catalogs. esi.mil sits
    behind an F5/TSPD challenge (measured 2026-08-18, in-browser rejection
    support id 6974556345028838376); when a catalog is walled, ESI agreement
    facts ride USASpending IDV award records instead (the Varonis BPA
    N6600122A0080, PoP end 2032-09-04 via EC America, was confirmed that
    way).
  - `vendor_press` (tools/api/vendor_press.py): vendor newsroom roster at
    `data/reference/vendor_press_sources.json` (env override
    LILA_VENDOR_PRESS_ROSTER for hermetic tests). Trade press stays on
    trade_rss/watchdog_rss; agency forecast pages stay on the
    forecast-adapter pattern (GSA Acquisition Gateway is already the enabled
    child of the forecast lane).
- A walled source is a receipt, never a silent skip: GovTribe, G2Xchange,
  and immixGroup answer scripted fetches with HTTP 403 (measured
  2026-08-18). Those rows land in the payload `errors`, project the mesh
  child as PARTIAL in the coverage band, and keep the family incomplete,
  which is the truth the caveats section must carry.
- SAM.gov opportunity pages render CLIENT-SIDE: a raw GET of a sam.gov/opp
  URL returns an empty shell, and an empty fetch is never evidence of
  absence. Notice detail corroborates from the daily-extract store first and
  a mirror second; a cited sam.gov URL rides store or mirror corroboration,
  never a bare client-side fetch.
- Bounded search budgets are disclosed, never silent: capped lanes speak the
  cap_disclosed dialect, per-site failures ride `errors`, and a sweep that
  ran narrower than intended surfaces in coverage/caveats instead of reading
  as a census.

## Forecast lane: two adapters, one screen (2026-07-30)

- `run_l4` reads EVERY registered forecast adapter it can: DHS APFS and the
  GSA Acquisition Gateway (8 more agencies) feed one unified score/IDF
  screen. Each adapter gets its own LaneQuery row with real fetched/kept
  counts; a disabled adapter is a NAMED row (`... is off`), and the lane is
  degraded only when every adapter is off. Measured on Riverbed vocabulary
  the day it was wired: DHS 777 fetched/49 kept; Gateway 7,649 fetched/116
  kept across Interior, USDA, VA, GSA, Labor, DOT, NRC - records that had
  been fetched and cached daily but never read by any press.
- The forecast coverage ledger (`tools/api/forecasts/coverage.py`) claims
  EFFECTIVE enablement (the adapter's own toggle at runtime), never
  ship-time `enabled_default`: filtering on defaults left COVERED_AGENCIES
  empty in production while 786 DHS records were being screened.
- The Gateway detail budget (`DETAIL_LIMIT`) is disclosed in
  `last_provenance` (`detail_candidates` / `detail_enriched` /
  `detail_truncated`); a pass-through record must never read as an enriched
  record that happens to lack an incumbent.

## L2 award depth rules (2026-07-30)

- SATURATION IS STAMPED: when a term fills every amount-desc page with
  hasNext still true, the LaneQuery carries a `SATURATED:` note and the lane
  runs ONE recency page (Start Date desc) through the same screen, deduped
  against the dollar pass. Measured live on NetScout: 200 capped -> 226
  kept, and the 26 recovered rows included in-flight 2026 renewals at
  $110K-$1.5M - the renewal-cadence band an amount sort structurally loses.
- THE CLIENT'S OWN AWARD PROVES ITSELF BY WHO WON IT: a client-kind row
  whose recipient IS the client survives the polysemy guard and the tier
  screen with `relevance_method="client_recipient_identity"` (the Osprey
  fix). Guard: multi-word client names use the shared incumbent_buyers
  prefix rule; single-word names require EXACT company-key equality so
  "Riverbed" can never claim RIVERBED RESTORATION LLC - and the rule is
  deliberately not gated on the wordlist-dependent `distinctive` flag.
- `Period of Performance Potential End Date` rides L2_FIELDS from the
  search endpoint (verified live 2026-07-30), so clock flagging and pack
  selection see option-extended end dates BEFORE enrichment instead of
  ranking on un-enriched data.
- `competition` / `competition_code` (extent competed) are carried from
  award detail into the pack; sole-source/8(a) direct awards never appear
  as notices, so this field is the only place that intelligence exists.
  `compose._RECORD_TEXT_FIELDS` includes it, so prose may cite competition
  language verbatim.

## Decision band: deterministic account decisions (operator spec, 2026-08-03)

- `agents/golden_press/decision_rules.py` is the one owner of the four
  account-decision rules over an evidence pack: R1 head-to-head (a buying
  agency where client paper AND named-rival paper are both current on
  `period_end >= today`; a record with no period end is EXCLUDED and
  receipted as undated-not-evaluated, never guessed), R2 displacement
  (sole-source intent or award naming a rival inside a client-history
  agency, with the VERBATIM matched sentence as the receipt), R3 vehicle
  expiry (the versioned `SEWP_V_FACTS` constant, cited to
  sewp.nasa.gov/contract_info.shtml; never asserts any prime's SEWP VI
  status), and R4 forecast adjacency (labeled qualify, never pipeline,
  never summed into a dollar figure). Zero LLM, zero network, zero metered
  quota; every output carries its evidence rows.
- Side classification is imported, never reimplemented: term derivation via
  `retrieval.entity_terms` (dict-shaped shim), affinity via
  `retrieval._affinity`, recipient identity via
  `rollups.resolve_relationship`, R2 matching via the shared `sam_lanes`
  guards (an ambiguous rival name without the vendor is rejected and
  receipted, exactly as the brand lanes reject it).
- Vehicle derivation (TASK 1 shape): every L2 USAspending URL's
  `CONT_AWD_{piid}_{agency}_{parent}_{parent_agency}` id parses onto the
  record (`parent_award_agency`, `vehicle_class`); classification is
  PREFIX-ONLY (NNG15S*@8000 SEWP V, GS*/47Q* GSA, HHSN* HHS, HSHQDC* DHS,
  no parent definitive, else IDV (unclassified) with the raw value kept).
- The rules' clock is the PACK's own generated_at date by default, never
  the wall clock (frozen-clock doctrine applied at design time); the press
  stores `pack.decisions` so replay re-renders the same band and the
  validator derives its dates and ids from the pack artifact.
- The decision band renders between forecast and candidate-review,
  UNCONDITIONALLY: a rule that found nothing renders its stated zero with
  the screening receipt, and a pre-rules pack states not-computed (which is
  not a zero). No prose slot exists for it; the model writes nothing there.
  Band order lives in `validate.GOLDEN_BAND_ORDER`/`EVENTS_BAND_ORDER` and
  `doctrine.SECTIONS`.
- ZERO-SAM PRESS: `tools/api/sam_quota.press_live_sam_enabled` (env
  `LILA_PRESS_LIVE_SAM`, default OFF) is the single flag. Off, the press
  path can construct no `SamGovSource` (live_providers) and never launches
  the metered sweep refresh (run_candidate_review discloses the stale or
  absent sweep loudly and continues on the store). The standalone search
  step keeps its own quota-guarded live behavior; the flag governs presses
  only. `run_candidate_review.py --golden-only` (Command Center step
  `candidate_review` with `args.golden_only`) presses the golden
  deliverable without a watch generation, for clients that have none.

## The vocabulary receipt (operator spec, 2026-07-31)

- Every sweep carries `results["term_yield"]`: for each wire capability
  term, `{term, count, sample_titles[:3]}` against the durable notice
  store (`tools/query_terms.py::term_yield`), printed compactly at fan-out
  and appended one JSONL line per sweep to `data/state/term_yield_log.jsonl`
  (env `LILA_TERM_YIELD_LOG`; the corpus is deliberately just an append -
  nothing is built on top until the data says so).
- THE TITLES ARE THE RECEIPT; THE COUNT IS ONLY THE INDEX. "APM: 3" reads
  as signal until the titles show a Navy sling assembly and an Assistant
  Program Manager named Tony Prudhomme. And there is deliberately NO
  dead-vocabulary alarm or health verdict: Riverbed's all-zero vocabulary
  was TRUE (its market moves as reseller renewals that never post
  publicly), and an alarm that fires on a true zero teaches distrust of
  true zeros. Adding a verdict field to this receipt is a design change
  the operator explicitly rejected.
- Recall claims about this lane are stated in outcome form only: the sweep
  returns N where it returned 0, and M of N are genuinely relevant on
  manual inspection. An N with no M is the shape of a result, not a result.

## Daily-extract outage recovery (2026-08-04)

- A floor refusal on the daily SAM extract means UPSTREAM PUBLISHED AN EMPTY
  FILE, not that the file moved. Verified 2026-08-04 during the
  07-29..08-04 outage: the Data Services tree, the FSD KB article, data.gov,
  and sam.gov's own mediated download endpoint all name the same falextracts
  key; no date-stamped daily snapshot exists anywhere in the bucket; prior
  S3 object versions are not anonymously readable. Do not repoint
  EXTRACT_URL on a refusal; the LaunchAgent retries every morning and heals
  forward on its own.
- `tools/sam_archive_backfill.py` is the gap-recovery owner (zero LLM, bash
  is fine): it floor-validates the weekly FY
  `Archived Data/FY{n}_archived_opportunities.csv` (identical 47-column
  schema, full descriptions, rebuilt Sundays), slices rows departing inside
  the gap window (ArchiveDate; PostedDate fallback for early-archived rows),
  and ingests each day under its own `as_of` so first_seen/last_seen stay
  honest and tier B's last_seen-equals-newest freshness rule is never
  distorted by a lump-day ingest. Slices ride `ingest(floor_bytes=0)`
  because their parent already passed the real floor; the default floor is
  untouched and still refuses every small raw file. Re-run after each
  Sunday FY build until the daily heals; still-active gap postings arrive
  whole with the first healed daily pull.
- Below-floor files have no place in `data/archive/sam_extract/`: the
  archive is "the only copy that will ever exist of that day", and a
  header-only stub is not a copy of anything. Remove poison members on
  sight, mirroring download_extract's own discard-below-floor doctrine.

## Native SAM consumed-source receipts (2026-09-10)

- `SamExtractSource` resets its primary/attachment census before each call.
  `extract_selection` records UTC and host-local calendar bounds around the
  unchanged daily downloader. Each actual CSV pass adds an `extract_receipts`
  row (`primary`, or `attachment_latest` and `attachment_candidates`).
- Receipt `cache_date` is parsed from the local filename and explicitly is NOT
  upstream publication time. The absolute path, exact raw bytes read, SHA-256,
  UTC read interval, and descriptor/path metadata bind what the CSV decoder
  consumed. `status=verified`, `complete=true`, `integrity=stable` require EOF,
  exact byte count and unchanged file identity/metadata. Both attachment passes
  must consume the same file/digest. Failed reads retain an honest partial hash
  and failure state; a failed download has selection evidence but no read row.
- Midnight is not silently normalized: a later attachment download can select
  the next local day's file, and each pass carries its own selection bounds.
  No snapshot option, query filter, search limit, ranking or fallback changes.
- Native sweep `results.sam_census` preserves these internal receipts. An
  attachment-stage failure retains its failed census; native live-API fallback
  retains `failed_extract_census` separately from the successful API census.
  The public-safe `_sam_census_receipt` projection remains unchanged; local
  filesystem paths do not become public coverage copy or release authority.

## Testing

- Offline doctrine: temp data dirs, Flask test client, no subprocesses, no
  LLM. `pytest.importorskip("flask")` guards UI tests.
- Reused fixtures: `tests/test_assessment_document._searches` (canonical
  sweep shape), `tests/test_ui._mk_client` (review seeding); profiles are
  injected with `monkeypatch.setattr("tools.capability.load_profile", ...)`
  (build_document imports it at call time).
- Tests carry dated doctrine comments naming the rule they enforce.
- Browser-behavioral assertions WAIT on conditions, never sample after a
  fixed sleep (2026-08-03): two NAICS-workshop focus tests raced a
  compressed timer with 20/100ms sleeps and failed deterministically under
  external machine load while the settled behavior was verified correct.
  Playwright's auto-retrying `expect(...)` is the repair shape; a fixed
  `wait_for_timeout` before an instant assertion is the event-loop cousin
  of calendar rot.
- Frozen-clock doctrine (2026-08-03, after three calendar-rot detonations in
  one week: the radar 14-day freshness gate on 2026-07-31, the strict
  Live-SAM deadline window and the press figure-freshness gate both on
  2026-08-01): any fixture whose absolute dates are compared against
  "today" anywhere downstream must either freeze the consuming module's
  clock at the fixtures' own TODAY or derive its dates relative to today.
  The in-process pattern is a monkeypatched date/datetime subclass on the
  consuming module (tests/test_radar_api.py::_frozen_clock,
  tests/test_live_report_truth.py::_freeze_strict_clock); subprocess worlds
  freeze through their hermetic sitecustomize.py seam, scoped to the one
  child that reads the wall clock (tests/test_composer_integration.py).
  Explicit `as_of`/`effective_date`/`--render-date` arguments always win
  over the frozen default, so expiry tests keep passing explicit dates.
  Freeze the STAMP clocks with the comparison clocks: an operator-action
  timestamp such as `approved_at` enters `build_assess_run`'s
  max(valid_observation_times) and a real-time stamp silently drags a
  rebuilt run past a fixture deadline. The main wall-clock owners are
  `agents.assess.live_report.utc_today` (every strict deadline cutoff,
  imported at call time by approval/facts/document), report `as_of`
  defaults, and `run_signal_board.py --render-date`'s date.today fallback.
- `tests/test_learnings_and_standards.py` gives LEARNINGS.md teeth: any
  entry PROPOSED > 30 days fails the build (promotion rule). New learnings
  ship ENCODED with their enforcement named, or PROPOSED with a clock.
- Full suite green is a shipping requirement:
  `.venv/bin/python -m pytest tests/ -q`.

## Legacy artifact naming map (2026-07-19; external status superseded 2026-08-23)

- `agents/reports/lint.py::APPROVED_CLIENT_TITLES` is THE naming map:
  approved presentation titles for the internal compatibility artifact
  families by tier. "Federal Opportunity Pre-Assessment" remains the locked
  Signal Board title, but neither it nor "Federal Opportunity Assessment"
  defines LILA's external product. The eight-slot Federal Market Map does.
- `lint_client_terminology(html, tier="assessment")` rejects "capture
  brief" everywhere client-facing in every tier. Artifact-specific render
  and release gates enforce the exact approved title for each deliverable.

## House style

- No em dashes in output-reachable strings (R9 auto-fix + lint_emdash +
  test_no_em_dash_in_output_reachable_strings). Use "·" or commas.
- Client-file doctrine (L-series, R14): verified claims + forward motion
  only; negatives convert or are deleted; the internal file
  (`<slug>.federal_opportunity_assessment.internal.md`) carries the trail.
- BANNED_PHRASES in lint.py kills LLM filler ("delve", "leverage", ...).
- White-label holds: no GTM-internal terminology in client copy
  (lint_whitelabel, lint_client_terminology).

## LLM routing

- All calls ride the Max plan via CLI: `agents/decisions/maxplan_cli.py`;
  `LILA_LLM_ROUTE` env, default `max` (`tests/test_maxplan_route.py`).
  The API route exists as the scale path; flipping it is an operator
  decision.

## Official-source Horizon discovery factory

- `agents/reports/horizon_discovery/` is a signals-only registry that maps
  supplied sweep payloads into Horizon fact-bank rows. It is deliberately
  separate from `tools.api` and exposes no `DataSource.search`, so its output
  cannot enter the live-opportunity fan-out.
- One registered adapter file owns each official source: Federal Register,
  Regulations.gov, GAO/IG, CISA KEV, USAspending budget lines, and
  forecast-store change events. Every emitted row has an official HTTPS
  citation and `tier=program`.
- Live collection owns coverage writes: fan-out records each actually
  attempted source once, including collection failures. Offline adapters only
  map supplied stored payloads and never write mutable coverage. Replaying a
  sweep, encountering an absent stored key, or reading a stored error envelope
  leaves the last real pull record unchanged. Mapper failures log, isolate,
  and emit no rows.
- Regulations.gov collection retains the source's agency identifier as buyer
  scope. Focused strict projection accepts a row only when that buyer binds to
  the selected agency; a publisher or government-wide context does not become
  the buyer by implication.
- Federal Register keyword payloads treat `_attempts`, `disabled`, `error`,
  `errors`, and `note` as reserved metadata. These keys never become PROGRAM
  evidence.
- Composition remains offline and deterministic. `build_fact_bank` consumes
  the normalized rows, while `validate_horizon`, `lint_horizon`, and
  `lint_forecast_context` remain the unchanged promotion authority. A
  disappeared forecast line is a source-change observation only and never
  evidence that a solicitation posted. A publisher is not silently treated as
  the buyer: CISA KEV is government-wide context unless the source names an
  affected buying organization.

## Opportunity-specific partner paths

- A pursue-kind `PartneringPlay` joins its pursuit card by the already-bound
  notice identity plus rank. The client and internal views render its teaming
  direction and evidence-graded candidates inside that existing card; the
  standalone dossier export uses the same join and `_teaming_block` renderer.
- The sales view keeps only path shape, direction, aggregate verifier facts,
  and the existing lock treatment. `gate_for_sales` physically removes
  candidate rows and redacts every exact candidate identity from the complete
  gated `AssessmentDocument`, including joined prose outside the partnering
  board. A cited-news row containing an exact candidate identity in any string
  field is removed whole before the three-row sales cap; later safe rows
  backfill and retain their original headlines and URLs. The masked notice-id
  plus rank join prevents collisions without restoring any paid identity.
- This is a feed into existing pursuit-card and dossier templates. It adds no
  client-facing section and changes no public document model or count key.

## Strict Live SAM report truth

- The scope-specific current Assess pointer is the per-client cutover switch
  for Live SAM report truth. `ABSENT` preserves the established legacy board
  and fact path. `CURRENT` makes the immutable `LiveSolicitationLedger` the
  eligibility, family, deadline, recommendation, and NOTICE-evidence source
  for the pursuit board, Section 01 facts, SAM watchlist, and monitor partner
  joins. `INVALID` or expired holds those live lanes closed; it never silently
  falls back. Removing the pointer is the explicit reversible rollback.
- A focused sweep whose agency label cannot form a valid scope designator
  checks the exact client's pointer directory before state is assigned. No
  client pointer means `ABSENT`; any pointer means `INVALID` because the code
  cannot guess which scope owns the malformed evidence. An unreadable pointer
  directory remains `INVALID`.
- Raw sweep rows remain presentation metadata only after the strict record is
  selected. A pursuit requires `BID_NOW` plus `PURSUE`, a future response
  deadline, and the existing exact human-reviewed NOTICE requirement span.
  Forecast timing, expiry math, program signals, and legacy triage can never
  promote a strict record into a posting.
- `verdict_totals` remains posting-level provenance, including amendments.
  Pursuit cards and `counts()["pursue_notices"]` remain solicitation-family
  deduplicated. The `counts()` vocabulary, `{{COUNT:*}}` vocabulary, fact-id
  format, and `build_fact_pack` public signature are unchanged.
- A current strict pointer adds its immutable `can_release()` leg to the
  established Assess approval gate. Required-source gaps can be accepted only
  through the separate unchecked operator action, bound to the exact canonical
  blocker rows and SHA-256. Any blocker change requires a new explicit
  approval. Normal approval never implies partial-release authorization.
- `regulations_gov` and `cisa_kev` are required Horizon coverage rows. Their
  current collectors lack run-bound completeness manifests, so even populated
  results remain `PARTIAL`. Adding these rows to an older run changes its
  blocker manifest on refresh and requires a fresh explicit partial-release
  decision against the new exact rows and digest.
- `python -m agents.assess.parity` is the offline pre-cutover comparison for
  board rows, complete counts, and posting-level verdict totals. A diagnostic
  scope override can explain pre-scope artifacts but can never persist or
  authorize a cutover.
- The report clock and the operational clock are deliberately separate
  (Cycle 5, 2026-07-12). With no explicit `as_of`, `AssessmentDocument` and
  `FactPack` stamp the operator host's local calendar date, while pursuit,
  partner-candidate, strict-ledger, dashboard, and release deadline
  comparisons use one UTC cutoff. An explicit `as_of` controls both for an
  exact offline replay. Relative sales-deadline copy follows the pursuit
  grade's comparison date. Internal same-day QA converts aware sweep times to
  the host-local date, treats naive artifact-mtime fallbacks as local, and
  keeps malformed times warning-loud. Incomplete strict coverage renders an
  explicit accepted-postings-of-census reconciliation; it never says every
  posting was accounted for or treats the market as empty. The census lint
  enforces the held-coverage disclosure in all views.
- Pointerless `ABSENT` rendering retains the legacy zero-triage landscape even
  when a SAM census is present. Census presence alone does not activate strict
  copy; CURRENT held or incomplete coverage still renders and lints its
  accepted-postings-to-census reconciliation.
- The partial-release click carries the exact displayed run id and blocker
  manifest digest. The server rejects a missing or stale pair without
  replacing the prior approval. A clean HTML whose nanosecond mtime does not
  strictly postdate the current pointer is also release-ineligible; Produce
  must rebuild and QA that scope after every material pointer change.
- Strict run identity through the report layer (Cycle 4, 2026-07-12): each
  capture press resolves the strict projection ONCE
  (`run_capture_brief._strict_projection_and_token`) and threads one token
  everywhere. Token owner: `agents/reports/compose_cache.py::identity_token`
  (CURRENT -> immutable run id, INVALID -> `strict:invalid`, resolution
  failure with a pointer on disk -> `strict:unresolved`, legacy -> None; a
  None token OMITS the key so every legacy digest and sidecar stays
  byte-identical). The token folds into the agency content fingerprint, the
  full-picture review digest, every sectioned compose digest, and the resume
  fingerprint, and is stamped into the stable QA sidecar as `assess_run_id`,
  which `release_state` requires to equal the current pointer's run id,
  resolved through the T5 seam `agents.assess.current_run_identity`
  (stat-memoized; a pointer the seam cannot validate fails closed).
  Under CURRENT, `reconcile_section01_strict` flags any composed live card
  outside the actionable NOTICE allowlist (id + evidence URL), and
  `_report_fit_traces` builds card provenance from the ledger's records;
  legacy triage rebuilds only when the press proves legacy truth. After its
  final scoped write, `run_agency_report` rematerializes an EXISTING strict
  run for that scope before any html exists (pointer-gated, creation-free),
  so a clean report always postdates its pointer.
- Creation-free refresh owner (2026-07-12):
  `tools/assess_refresh.py::refresh_current_assess_run_if_active` is THE
  ledger-refresh seam for search, dossier, qualify, market-refresh, Horizon,
  the agency runner, and the Control Room gate hooks. Pointer exists ->
  refresh (approvals and fresh evidence bind into the current envelope);
  pointer absent -> loud skip, nothing created, exact legacy behavior. First
  activation is operator-only: `python -m tools.assess_refresh --client
  "Name" --activate`. Never call materialize_current_assess_run directly
  from a runner or hook.

## Cutover decision packets

- `python -m agents.assess.parity --output <packet.json>` remains an offline,
  non-authorizing comparison. It writes the JSON parity artifact plus an
  operator-facing Markdown packet at the same stem; `--packet-output` may
  select another Markdown path. It never persists or activates a current
  Assess pointer, and a diagnostic scope override always holds eligibility
  closed.
- Parity isolates entity-resolution traffic only while it builds the legacy
  and strict offline documents. It saves and replaces even a caller-supplied
  `LILA_ENTITIES_DIR`, then restores the exact prior value before
  CURRENT-pointer validation, whose projection-input manifest must hash the
  operator's real entity crosswalk. The parity command never rewrites or
  refreshes the pointer it inspects.
- Every changed legacy pursuit-board, standing-watchlist, and monitor row is
  tied to the strict adapter's concrete eligibility leg, ledger reason, title,
  agency, and deadline. Posting-level verdict transitions are listed as the
  numerical proof behind aggregate parity. An older amendment that maps to a
  surviving strict solicitation family is labeled as superseded by the
  current family posting, not as a failed opportunity.
- A generic collection-only rejection is not an explanation. Any unexplained
  row or unreconciled board, watchlist, monitor, or verdict delta adds a cutover
  hold and forces the packet to `NOT ELIGIBLE`. The packet is evidence for the
  later operator decision; it does not change the diagnostic-override rule or
  grant authority to activate a pointer.

## Internal recurring change intelligence

- `python -m agents.change_digest --client <exact client>` builds the local,
  internal-only change digest from the sweep selected by the current operator
  gate. The sweep must carry the exact client and an explicit scope matching
  that gate. A scope change captures a fresh baseline and never reports the
  old universe as mass entries or exits.
- Each evidence lane records whether it has ever been observed. A first
  successful observation is a zero-motion baseline even if another lane was
  available earlier; temporary source unavailability preserves the prior
  baseline. Per-client state uses collision-safe Assess storage keys and the
  existing atomic artifact history pattern.
- Classification delegates to the existing differs: the watchlist differ for
  SAM census, recompete, and Horizon rows; the forecast snapshot differ for
  matched forecast records; and the approval-evidence differ for explicitly
  labeled census context. The global forecast store is read only and filtered
  to matched client identities. Forecast-store and recompete source hashes are
  retained as provenance cursors.
- Horizon movement is based on every validated eligible PROGRAM fact-bank row,
  including rows not selected into an analyst narrative. Stable evidence
  identity is independent of Horizon item organization. NOTICE and MARKET rows
  do not enter this lane.
- The digest renders only in the internal Control Room. Client and sales
  documents never receive its artifact or strings. Every lane states that
  planning signals, census movement, and expiry math do not establish a live
  procurement opportunity; pursuit-grade status still requires the separate
  Live SAM evidence gate.

## CURRENT run identity reads

- Consumers that need to stamp a sidecar call the public
  `agents.assess.current_run_identity` seam with the exact client and scope
  designator. They do not reopen pointer and run files independently or infer
  identity from a report timestamp.
- `None` means no usable CURRENT identity. Pointer absence is expected before
  cutover; a present invalid or drifted pointer is logged and fails the same
  non-throwing read closed. The four-key result is identity metadata only and
  must never be treated as a release decision.
- The pointer digest uses the repository's canonical JSON digest. The blocker
  digest comes from the existing required-blocker manifest owner, and the UTC
  persistence timestamp comes from the coherently sampled pointer inode.

## The polysemy corpus (2026-08-03)

`data/reference/polysemy_corpus.json` is the single record of measured
vocabulary collisions: one entry per witnessed notice, with term,
collision_domain, resolution, notice_id, agency, naics, psc, date_observed,
status. It is an internal quality asset and the checkable record behind
coverage claims; nothing in it is a gate value.

- Guards derive strings from it: `term_tiers.corpus_negative_phrases`
  builds the negative-context list (entry order preserved), and
  `sam_lanes._common_word_products` loads the common-word fallback next to
  its class witness. `screen_guards.json` keeps domain anchors, vendor
  phrases and attributes.
- PROVENANCE IS THE ADMISSION TICKET: an entry a guard consumes carries the
  32-hex store notice_id that witnessed the collision. Status `unproven`
  (evidence only in a session transcript) contributes nothing until a real
  id is attached. Ids are never invented; absence is recorded as absence.
  `tests/test_polysemy_corpus.py` enforces both directions.
- Recorded-but-not-consumed entries (`guard.consumers` empty) document
  collisions handled elsewhere (NAICS boundary, bare-acronym reject,
  attribute qualify) or awaiting wiring (per-term negative context for
  `middleware`). Recording one is cheap; wiring one is a behavior change
  and needs its own measured justification.
- A missing corpus weakens screening (empty negative list, dictionary-only
  entity guard) and never fails a press or scan.



## Targeting band: the action layer (2026-08-05, contract amendment v1.4)

- Band 09 `targeting` closes the report. Two layers that never merge:
  DETERMINISTIC SPECS (press-side, from pack data) and CONTACT ROWS
  (enrichment, read from a durable store). `targeting_rules.py` is the one
  owner of the specs; `targets_store.py` owns the store and the export;
  `tools/apollo_targets.py` owns the API and is NEVER importable from the
  render path (`test_targets_supply` pins that structurally).
- THE PERSONA LADDER IS DATA. `data/reference/targeting_personas.json`
  carries tiers 1 to 4 (persona, target component, titles, seniorities) and
  the row-class rules that select among them, tuned like the polysemy
  corpus: a title added there changes what the supply step searches for
  with no code change. `load_personas` validates loudly (a tier with no
  titles searches for nobody; a rule seeking an undefined tier targets
  nothing). A DEFINED TIER NO RULE SEEKS IS DISCLOSED in the band receipt,
  never silent, the same shape as a recorded-but-unconsumed corpus entry.
- ROW CLASS IS READ OFF THE EVIDENCE. An R1 card whose nearest dated clock
  is client paper is a renewal (T1); one whose nearest clock is rival paper
  is a displacement window (T2); an R2 alert is always displacement; an R4
  qualify row is adjacency (T3) and routes through the PAPER HOLDER (the
  reseller or prime on the client-history award), never the buying agency,
  because nothing has posted yet. A row whose only recipient IS the client
  has no route to work and produces no spec.
- "ALREADY RENDERED" IS LITERAL. The join law admits a call only against a
  record id the bands above ACTUALLY printed, and band 05 caps its buyer
  list at 12, so the join cannot be proved against the pack alone.
  `render_bands` therefore hands each builder the region rendered so far
  (`s["_rendered_bands"]`); only band 09 reads it, and a spec joining
  nothing rendered is withheld with its count stated.
- NO PROVENANCE, NO RENDER. Every contact row states its class (`apollo` or
  `operator`) and its retrieved date; `targets_store.admissible` drops a
  row that cannot and strips a single field whose own override provenance
  is unreadable, counting both. Contacts are ENRICHMENT: never a fact,
  never in the evidence dock, never in the primary link tally, never a
  mailto anchor.
- THE RECEIPT DECIDES THE ZERO (the competitor_zero_untested shape applied
  to people). Attempted with zero results renders NO CONTACTS RESOLVED and
  is certifiable; no receipt renders TARGETING SUPPLY NOT RUN, which is not
  a zero; a pre-rules pack renders TARGETING NOT COMPUTED. The validator's
  postures and provenance classes are PARSED from the contract's own §2/09
  laws (`contract.band_postures` / `band_provenance_classes`), so renaming
  a posture is a contract edit rather than a silent drift.
- ZERO LIVE CALLS AT PRESS TIME, PRESERVED. `run_targets.py` is sweep class:
  it reads the client's stored evidence pack for its specs, honours the
  Assess to Target operator door (reads `target_gate_status`, never writes
  it), refuses on `LILA_ENABLE_APOLLO` off (default off), on a missing
  `APOLLO_API_KEY`, and on any API error, caps its calls, and prints its
  spend. `--dry-run` plans at zero cost and needs neither switch nor key.
  The press reads only the store file and a replay reuses the sealed
  contacts on the pack without reopening it.
- The Apollo-ready CSV (`<stem>.apollo_targets.csv`) lands beside the client
  artifact as an OPERATOR TOOL. Nothing in the release path reads it and a
  failure to write it never touches the report.

## Interactive features (2026-08-04, operator-mandated)

- No interactive feature is claimed working without an automated
  interaction test from this commit forward. A drag-drop, click, picker,
  save/reload, or any other in-browser behavior ships only with a real
  browser test (Playwright, `tests/test_studio_interaction.py` is the
  pattern) that performs the interaction and asserts the resulting DOM
  and persisted bytes. Static markup inspection does not count.
- The pressed studio artifact carries exactly ONE editing system: the
  golden skeleton's own inherited editor. The press never injects a
  second runtime (`compile_editable_studio(..., inject_runtime=False)`);
  a change that adds a script to the press output must extend that one
  editor and its interaction test.

- SESSION GIT RULE (hard, 2026-08-05): no session commits to the primary
  checkout; every session gets its own worktree on a named branch; Tyler
  does every merge; never `git worktree prune`. Full text in CLAUDE.md.

## Spike reports (2026-09-06)

- Pre-implementation maps live in `docs/spikes/<topic>.md`. A spike is
  reviewable feedstock, not a contract surface and not a schema. It may
  recommend an additive file layout. It must not delete Assess models,
  change Market Map release, or ship Pydantic packages.
- Reality in the code still wins. Cite paths and field names from the
  checkout (and from an unmerged branch when that branch is the named
  authority, as with Step 1 intake on
  `cursor/step1-intake-mastery-da3f` / PR #1). If a locked product
  document is not in the repo, say so and map against the operator
  ruling that tasked the spike.

## Lead-gen contracts (2026-09-06)

- `agents/leadgen/` is an additive sibling of `agents/assess/`. It does
  not replace `AssessRun` or add lead fields to `LiveSolicitation`.
  The 2026-09-08 output-retention ruling below adds it to `lila_release`. Opportunity assessment is PARENT;
  LeadRow is CHILD. Type authority for the first cut is
  `docs/spikes/ws0_today_to_leadrow_map.md`. Package overview:
  `docs/leadgen/README.md`.
- Frozen + `extra=forbid`, same construction rules as Assess (lists in,
  tuples after validation). Version stamp: `leadgen.contracts.v1`.
- Enum namespaces must not collide. `LeadTier` values are
  `LEAD_T1|LEAD_T2|WATCH|HOLD|REJECT` and persist only on field
  `lead_tier` (display "lead T1" / "lead T2"). Targeting motion
  `rule_id` stays `T1|T2|T3` on `TargetingRuleId` (a closed copy of
  `targeting_personas.json`; `targeting_rules.py` remains the rule
  owner) and persists only on field `targeting_rule_id`. Never store a
  lead as bare `"tier": "T1"`. There is no lead T3.
- Stages reuse Assess `LifecycleStage`. Commercial motions, pathways,
  seller paths, contactability, and communication permission live in
  `agents/leadgen/enums.py`. Seller path literals reuse
  `PartnerDirection` plus fail-closed `path_unknown`.
- JSON Schema export: `python -m agents.leadgen.schema_export` writes
  `agents/leadgen/schemas/`. Persistence is a later ticket; do not
  write lead rows into `data/state/assess_runs/`.
- Draft mapper: `agents.leadgen.from_assess.draft_lead_rows` reads one
  `AssessRun` (or AssessRun-shaped dict) plus an optional
  `target_actions` projection and emits `OpportunityAssessment`
  parents plus WATCH/HOLD `LeadRow` children. Pure and fail-closed.
  Never aliases targeting `T1` to `LeadTier`. Never writes
  `data/state/assess_runs/`. The release orchestrator may consume its child output.
  Run: `python -m agents.leadgen --assess <run.json>`.
- Press Lead Gen thin real path (2026-09-07): `agents.leadgen.press.run_press`
  documents and executes Build Plan steps 1-9. Step 1 cites
  an optional client profile / intake dossier path. Steps 2-5 hand off
  to the existing search / qualify / Assess / target_actions path and
  do not reimplement discovery. Steps 6-7 call `draft_lead_rows` then
  `qualify_drafts`. HOLD remains the fail-closed default when four-leg
  receipts are missing. A small evidenced subset may promote to WATCH
  or `LEAD_T2` (at most one `LEAD_T1` when incumbent-renewal timing
  evidence is present). Incomplete required Assess coverage blocks
  T1/T2. The qualifier does not strip a vehicle cite or stamp
  `auth_only=false` to buy a skeptic D3 PASS. REJECT only with a real
  receipt. No quota fill.
  Steps 8-9 write branded HTML (primary, when `review_dir` is set) plus
  `data/review/<slug>.leadgen.json` (optional
  `data/review/<slug>.leadgen.md`). `stub` is False when the qualifier
  ran. Missing assess input fails closed. Notice-only input is refused.
  The 2026-09-08 ruling incorporates Press as a companion in `lila_release`;
  Market Map remains the primary external deliverable.
  Run: `python -m agents.leadgen.press --assess <run.json>`.
- Read-only AssessRun export for press (2026-09-07):
  `agents.leadgen.export_assess.export_current_assess_run` dumps the
  current immutable pointer under `data/state/assess_runs/` (or
  validates `--from-file`) as a press-ready envelope wrapping `run`.
  It never calls `materialize_current_assess_run`, never writes
  `data/state/assess_runs/`, and never invents live/horizon records
  from notices, Market Map, worksheets, or `source_records`.
  `run_assessment.py` / `AssessmentChain` do not write AssessRun
  JSON; first materialization of a completed sweep remains
  `python -m tools.assess_refresh --client "Name" --activate`.
  Testco-shaped demo fixtures labeled as Arista fail closed.
  Run: `python -m agents.leadgen.export_assess --client "Name"`.
- Skeptic eval harness (2026-09-07): `agents.leadgen.eval` scores
  press/draft LeadRow JSON plus parent assessments. It does not score
  Step 1 dossier hygiene and does not touch intake extract. Rubrics:
  `docs/leadgen/LEADROW_RELEVANCE_RUBRIC_v0.md` (A-F + tier/pack) and
  `docs/leadgen/OPP_PARENT_QUALITY_RUBRIC_v0.md`. T1/T2 require A-F
  PASS; solicitation-only T1/T2 fail; UNVERIFIED email cannot PASS
  C3; auth-only cannot PASS D3; REJECT rows are receipted and never
  dropped; the scorer invents zero rows (no quota fill). Overlays
  (`leadgen.eval.v0`) carry email status and auth-only facts the
  frozen LeadRow forbids. Run:
  `python -m agents.leadgen.eval.score --pack <receipt-or-eval.json>`.
  Tiny fixture: `agents/leadgen/eval/fixtures/tiny_pack.json`.
  `PACK.REJECT_NOT_DROPPED` treats only nonblank string ids as declared
  leads. `DecisionTrace.lead_id` may be null on parent-only traces (no
  emitted child); those traces are skipped from inventory joins and are
  not missing leads. Real string REJECT ids that are absent still FAIL.
- Step 1 `CompanyDossier` remains the ontology front door (PR #1). Cite
  it with optional string fields until that PR merges. Do not import
  targeting or Apollo from `agents/leadgen/contracts.py`,
  `agents/leadgen/from_assess.py`, or `agents/leadgen/press.py`.

## Vocabulary, identity and posture (2026-08-06)

The assessment family kept reporting OUR failures as market facts. Four
modules exist to stop that, and every one was written after a measured miss.

- **`term_expansion`** widens an approved term into the language a
  contracting officer actually writes, at MATCH time only. Measured: all
  eight of apexanalytix's approved core terms matched ZERO of 330,641
  stored notices. Marketing writes noun stacks ("improper payment
  prevention"); solicitations do not. The operator-owned frame is never
  edited; only how it is matched. `data/reference/term_synonyms.json` is
  the tuning surface and its law is EQUIVALENCE, NEVER ASSOCIATION: a group
  linking "onboarding" to "registration" matched SAM entity-registration
  boilerplate in every Army construction notice and had to be removed.
- **`term_discovery`** learns the vocabulary FROM the corpus instead of from
  a hand-maintained list, which is reactive by construction. Runs pre-press,
  every press, zero model calls and zero credits. Three rules earned the
  hard way: seed from the ORIGINAL frame, never the widened one (widening
  carries false positives by design); seed STRUCTURALLY by NAICS when the
  frame is too narrow to bootstrap (lexical seeding gave 3 notices, NAICS
  gave 180); and exclude the seed set from its own background, or a term
  used by every relevant notice reads as common rather than distinctive.
  Rank by LIFT, not frequency, and require a NOUN PHRASE: "information be"
  cleared 78x lift over 2,621 notices because lift cannot tell a subject
  from a sentence slice.
  IT PROPOSES, IT NEVER APPLIES. Capability terms are the ENGAGEMENT
  boundary; a candidate reaches a search only via
  `discovered_terms_approved` on the profile.
- **`client_identity`** keeps the client distinct from its rivals.
  riverbed files SteelHead, SteelCentral, AppResponse, NetProfiler and NetIM
  (its own products) under `named_competitors_and_incumbents`.
  `assert_client_resolvable` fails loudly on an entity set with no client
  term, because R1/R2/R4 all need to tell client paper from rival paper and
  would otherwise produce a confident zero. Reclassification is mechanical
  only for the client's own name; an ambiguous product is FLAGGED for an
  operator decision, never guessed either way.
- **`prime_posture`** derives prime-versus-sub from award history rather
  than a per-client switch, because a switch encodes an opinion once and
  then ages. The demonstrated ceiling is the LARGEST single award actually
  held, never a sum. Measured across the book: red_hat and riverbed are SUB
  (channel-only via Carahsoft and DLT), thinklogical PRIME at a $460,891
  ceiling, so a $56M requirement is honestly a team play. Nothing defaults
  to prime.

**THE STANDING LESSON.** A zero is only honest if the search was adequate.
apexanalytix pressed zero decision rows and read as an empty market while
the government was publishing an RFI for Audit Remediation Services at DIA.
Before reporting a zero, check the frame against the store.

## The Federal Market Map (operator lock 2026-08-23)

`docs/MARKET_MAP_CONTRACT.md` is the single source for LILA's sole external
product. Its eight slots, names, and order are operator-locked until the
operator explicitly changes them. `agents/golden_press/external_product_contract.py`
parses the table and verifies its locked digest; tests fail on any add, remove,
rename, or reorder. The seven-section and other historical renderers remain
internal migration surfaces. Their own validators preserve internal behavior
but cannot amend the external section contract.

The graph-to-product boundary is a typed projection, not renderer inference.
`external_product_projection.py` assigns every record one owning slot; slot 1
contains ranked references to records owned below, and opportunity-specific
targets remain nested beneath their qualifying slot-5 opportunity. The
standard renderer consumes only that projection and keeps all eight slots
visible, including named zero/gap states. `product_bundle.py` is the single
external transaction. It refuses before promotion unless Assess and Target
approvals are current, the graph contract is certified, the client render is
valid, and every bundle member is bound into `manifest.json`. The current
pointer and downloads revalidate the manifest, ZIP, and every member hash.
Older families are classified by `agents/reports/product_families.py`; their
continued readability is compatibility, not release eligibility.

## Operator frame tiers and the full-store re-screen (2026-08-17)

- `clients/<slug>/frame_tiers.json` is the single record of an
  operator-tiered frame (work order A1 pattern): tier 1 names, tier 2 buyer
  language, tier 3 context, plus a `screen_routing` block that resolves
  ambiguous names into their guard route. The operator's list is preserved
  verbatim under `as_ordered`; routing never rewrites it. The profile
  (`capability_terms.core` = tier 2, `adjacent` = tier 3) and the taxonomy
  carry the same vocabulary for the established screens, and
  `tools/apply_varonis_a1_frame.py` is the idempotent replay that carries it
  into the review packet through `amend_terms` (approved-state carve-out,
  prior terms restorably in `kept_out`, procurement-lens keywords untouched).
- `tools/frame_rescreen.py` re-screens the WHOLE notice store plus every
  stored forecast source against a frame-tiers file. Zero LLM, zero quota.
  It owns no judgment: tier 1 names ride `sam_lanes` matchers and
  `reject_entity_hit`; tier 2/3 language rides the `term_tiers` ladder;
  leverage and family dedupe ride `notice_join`. The keep rule is the
  operator's (tier 1 or tier 2 sentence match only), every keep carries its
  verbatim matched sentence as the receipt, and rank 4 awards are kept as
  evidence but never printed as opportunities. Artifact:
  `data/state/frame_rescreen/<slug>.rescreen.json` (atomic, historied).
- THE BARE-TOKEN RIVAL LESSON. A rival product named by a common English
  word ("Purview") must never ride as a bare distinctive name: measured
  2026-08-17, 42 of 42 sampled bare occurrences were the idiom ("under the
  purview of", "DLA's purview"), zero were the product. Route such names
  vendor-qualified only ("Microsoft Purview") and measure the bare token in
  the receipt; the witnessed collisions live in the polysemy corpus as
  recorded-but-not-consumed entries, because the guard is structural.


## Assessment output retention (operator ruling 2026-09-08)

The operator ordered weekend integration and restoration of opportunity
assessments with lead generation before any relevance cuts. This supersedes
prior instructions that kept Press outside `lila_release`.

- `product_bundle` keeps the existing eight-slot Market Map and adds the pure
  Press receipt, HTML, original AssessRun envelope, and skeptic scorecard to
  the same hash-bound ZIP. Missing child input is explicit and cannot hide
  its parent assessment. Qualification and lead promotion remain evidence-bound.
- Slot 5 retains all L1 assessment records, including HOLD, closed and
  insufficient-evidence dispositions. Slot 1 still references only qualified
  pursuits. A record being visible never asserts it is active or seller-ready.
- `product_leadgen.restore_assessment_population` joins the stored AssessRun
  population to the original evidence pack and fills missing details using the
  existing SAM notice-store adapter. It preserves existing facts and source
  dates; a rebuild timestamp cannot make an old source fresh.
- A replacement may not silently remove prior source records or blank their
  descriptions, source links, or contact fields. Such a build blocks before
  replacing `current.json`. A later relevance-cut workflow must retain audited
  exclusions; it is not part of this repair.
- Proof: `tests/test_assessment_output_retention.py` and
  `tests/test_product_bundle.py`. Command Center accepts `no_desktop` to keep
  the sealed release in its existing release directory.

- The combined `lila_release` requires current Assess and Target stage
  authorization, but not promoted-target inventory approval. This avoids a
  circular dependency between seeing an assessment and developing its leads.
  Actionable target reports retain their complete Targeting Review gate.

### Operating research and quality baseline (2026-09-08)

`data/review/<slug>.reviewed_cases.json` is a typed source-bound research input.
Import with `.venv/bin/python -m tools.reviewed_cases --client <name> --input <file>`.
The importer preserves immutable revisions under `data/review/research_history/`
and refreshes an already-active Assess pointer; it does not activate a client or
approve a requirement. Use `--notice-id`, `--disposition` and `--reason` together
to remove a weak case from priority while retaining its evidence and history.

Strict report projection reopens the casebook against its immutable input hash
and requires complete record equality for an out-of-sweep case. Supplemental
records travel in `LiveReportProjection.reviewed_research`, separately from raw
SAM `notices` and posting counts. Any existing raw row or indexed family retains
its strict join requirements. Selected reviewed research renders as an explicit
standing research entry after the existing capped watchlist, retaining its source
classification, deadline and next ask. It never enters sweep snapshots, displaces
an existing watchlist row, acquires a NEW badge, or gains a pursuit rank. Rejected
and deprioritized cases remain in the immutable assessment and lead companion.
Regression: `tests/test_reviewed_case_projection.py`.

Each normal release binds the matching `<slug>.dossier.json` and optional
`<slug>.target_actions.json`, then joins reviewed actions to exact Assess parents.
The release includes `company_dossier.json`, `reviewed_cases.json` and
`quality_baseline.json` when these inputs exist. Digest verification and the
existing eight-slot validators run before promotion. The default run location is
`/Users/wtjohnson/Lila`; `LILA.command` prints its revision and starts Command Center.

Run the full test gate with `LILA_SUITE_OFFLINE=1 LILA_SUITE_STRICT=1 .venv/bin/python
-m pytest tests/ -q`. The fresh runtime requires all packages in requirements.txt,
including NumPy for the offline dense/hybrid retrieval contract checks.


## Research Picture evidence validation (2026-09-11)

The internal Research Picture extends its existing Pydantic models with typed
`ResearchClaim` references. `agents/decisions/research_evidence.py` retains canonical
source IDs (or explicitly generated registry IDs when no source ID exists), exact
field locators, bounded passages, original URLs and identity, supplied timestamps,
retrieval state and a SHA-256 of each original record. Web evidence is discovery
only even when a search result links to SAM. The registry admits selected native
pursue/monitor rows and bounded contextual lanes in native order, at most80 records
and120000 serialized characters; truncation and source failures remain visible.

`compose_research_picture` supplies the registry separately from `source_sweep`,
then validates every generated reference against it. Source facts render cited
excerpts rather than treating a cited paraphrase as proved. Offering-fit and
cross-source suggestions may be labeled inference; procurement state, value,
incumbent and route cannot be invented by inference. Unknown opportunity IDs are
rejected without title matching, while canonical web signals remain verification
tasks. Old unsourced narratives fail closed when rendered. No evidence checks
establish semantic entailment of an inference or qualify a lead.

The current-original-notice classification requires recorded government notice
text, explicit active/type metadata, a same-day supplied retrieval timestamp,
a future timezone-bearing response deadline, and references to its own text for
state, requirements and action. It is not BID_NOW or seller readiness and cannot
replace existing human-reviewed requirement or access checks. Unknown freshness
or deadline precision stays research. Awards stay historical and channel records
cannot establish access to a particular opportunity.

The existing client-compose quarantine stays unchanged: Research Picture and
web_leads are excluded by default. Evidence registry fields never appear as a new
web-bearing sibling in source_sweep. Assess, Market Map, qualification, release,
source collectors, retrieval vocabulary and operator gates are untouched.

Targeted offline verification: `LILA_SUITE_OFFLINE=1 LILA_SUITE_STRICT=1 python -m
pytest tests/test_research_picture.py tests/test_research_picture_evidence.py
tests/test_truth_purges.py -q`. Use the configured project Python environment.


Saved Research Picture JSON and generated Markdown use the same observational
revalidation path. Native `ui/server.py::_research_picture` calls
`project_saved_picture`; `buildPicture` preserves linked research tasks and
separate current-notice, historical and channel sections, using the existing
safe Markdown sanitizer. Opening the view never recomposes a model, writes a
sweep or changes a gate. A persisted version marker cannot bless raw prose.
Explicit failed, unreadable, discovery-only, stale and unknown retrieval states
survive normalization and cannot become documented current notices merely
because a description field is present. The additive native projection change
is isolated with `tests/test_research_picture_ui.py`; its browser test uses
actual UI functions in a temporary Chrome page with all requests blocked.


## Research Picture source rebind hardening (2026-09-11, work046)

Saved `validation_version`, `evidence_registry` and hashes are audit material,
never independent authority for the same saved picture. Native projection and
Markdown must rebuild from their owning original `results`; absence fails closed
with a named gap and no opportunity/source-fact cards. `render_markdown` accepts
those results explicitly; `run_searches` and `run_picture` supply them. This changes only the
internal Research Picture rendering boundary, not a client release contract.
Display-time currentness uses current UTC rather than a model/saved as-of clock.

Whitespace and punctuation-only quotations cannot bind claims. An opportunity's
source-specific factual claims cite only that source; cross-source reasoning
remains explicitly labeled inference or narrative context. Conflicting primary
notice identities or response deadlines produce binding issues and prevent
current-notice confirmation. Equivalent aware deadline representations normalize
to UTC for conflict comparison; ambiguous deadlines are withheld, not selected
optimistically. This does not amend strict Assess, profile, scope or release gates.

Focused offline validation: `python -m pytest tests/test_research_picture.py
tests/test_research_picture_evidence.py tests/test_research_picture_hardening.py
tests/test_truth_purges.py -q`. Full regression uses the existing offline strict
suite command. Use the configured project runtime; browser checks use temporary
profiles. The synthetic probes are source-binding tests, not qualified-lead proof.

## Capability vocabulary and screening evidence (Step2, 2026-09-11)

`tools/relevance/taxonomy.py` owns optional, versioned capability evidence,
explicit buyer-language aliases and retrieval mappings. An alias scores its
canonical CORE concept once, even when several equivalent phrases occur.
Aliases require a referenced company evidence record. Conditional aliases
require a bounded same-sentence subject; the separate `guard_context` receipt includes
that context. Canonical report quotes retain their existing 60-character format;
reviewed alias supports carry the wider guard context under the separately
documented C1 contract extension. No global synonym expansion or threshold reduction is applied.

`retrieval_vocabulary()` derives SAM additions from the same definition. Existing
operator query terms remain unchanged and are receipted even when unmapped.
Exploratory retrieval entries do not themselves add screening evidence. Non-SAM
lanes retain the existing canonical capability vocabulary. The sweep's
`capability_vocabulary` records actual wire terms and the normalized definition
hash. `term_yield` retains its count/title-only shape; separate
`term_yield_population` identifies the accumulated, unfiltered notice store,
which is not the filtered daily-extract population.

`deterministic_prefilter()` keeps its three-part return interface and adds
`screening_records`, keyed by source identity, with record hash, field spans,
canonical score, source coverage, and stage/reason. Declared awards are historical
research regardless of a future deadline or capability text. They do not enter
open-opportunity model triage. Missing text and failed attachment lookups yield
`unscreened` without a model call; existing decision coverage therefore remains
incomplete. A decisive existing scope/code/false-positive exclusion may still
reject a row with a nonessential source gap. Functional kill-rules can label an
explicit functional mismatch; weak supplied-text matches are not such proof.
No new Apex functional kill-rule is inferred from a generic healthcare word.

The attachment producer records per-notice lookup results under
`attachment_record_receipts`: unattempted, failed/stale inventory, checked empty,
text unavailable, or unreviewed captured text. Absent receipt is unknown. This is
an internal discovery receipt; strict Assess evidence, attachment inventory
approval, source depth and lead qualification are unchanged. A supported alias
reaches review, not automatic qualification. Full requirement-versus-boilerplate
adjudication remains the subsequent repair step.

Verify offline with `LILA_SUITE_OFFLINE=1 LILA_SUITE_STRICT=1 python -m pytest
tests/test_capability_alignment.py tests/test_relevance_engine.py
tests/test_relevance_taxonomy.py tests/test_triage_prefilter.py
tests/test_sam_attachment_text.py tests/test_term_yield.py
tests/test_sam_extract_source_receipt.py tests/test_client_relevance_alias_contract.py -q`, using the configured runtime.
The strict full suite is `LILA_SUITE_OFFLINE=1 LILA_SUITE_STRICT=1 python -m pytest
tests/ -q -rs`. `python -m tools.relevance.audit_saved_screen --help` documents
an offline, hash-bound replay against a declared baseline, writing only an
explicit output directory. Frozen captures are read-only development inputs;
replay and synthetic tests make no independent holdout or recovered-lead claim.
