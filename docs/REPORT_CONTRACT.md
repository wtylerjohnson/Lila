# REPORT_CONTRACT.md
## Federal Opportunity Pre-Assessment - internal compatibility contract, v1

External status: **NOT A CLIENT PRODUCT.** This executable legacy format may
feed the Federal Market Map, but it cannot override or compete with LILA's
sole external product contract in `docs/MARKET_MAP_CONTRACT.md`.

Status: DRAFT for Tyler's edit. On approval, this file is committed to the repo,
the validator enforces it, and no press ships a report that deviates from it.
The validator reads this contract's band table; the band list is defined ONCE,
here (the current triplication across validate.py, compose.py and render.py is
retired in favor of this file as the single source).

Spec sources: the Riverbed rework (the client-approved structure), Ed's doctrine
(four-part report, federal capture vocabulary, figures called by their specific
name), and the standing laws already in force (linkage, dock-exactly-once,
number justification, absence discipline, no LLM claims).

---

## 0. GLOBAL LAWS (apply to every band)

- L1 LINKAGE: every dollar figure, date, contract/notice/forecast id resolves to
  a canonical primary-source link (USAspending award page, SAM notice page,
  APFS /record/{id}/public-print/, official agency event page). A claim that
  cannot link does not render.
- L2 CLAIM BINDING: renderable claims are generated from records; the renderer
  refuses a claim with no record id. LLM output never contains a digit, $, %,
  or proper-noun assertion (the existing _prose_usable rule).
- L3 EVIDENCE CLASSES: primary evidence (federal record pages), context
  (official agency pages, published contacts), navigation (in-page). Vendor
  marketing pages are NEVER cited as evidence. Class totals are disclosed in
  Band 08.
- L4 ABSENCE DISCIPLINE: a band with zero qualifying content renders its zero
  as a stated finding with the screening work shown (N screened, lanes, date).
  No padding, no borrowed content, no band deletion to hide a zero.
- L5 FIGURE NAMING (Ed): every aggregate is called by its specific name
  ("past obligations in category", "published lower-bound floor", "obligated on
  cited records") and carries a show-the-work toggle reconciling components to
  the total, each component linked.
- L6 LABELED HISTORY: historical obligations are labeled history, never
  position, footprint, or market share.
- L7 VOCABULARY: federal capture vocabulary only. Banned invented terms:
  corridor, research clock, 360° assessment, candidate opportunity for review,
  preliminary match. (Extend the banned list in the validator, not here.)
- L8 FRESHNESS: report date is the press date, real clock. Any clock (period
  end, deadline, event date) earlier than the report date renders with an
  explicit EXPIRED/PASSED flag, never silently. No undated "TBD" prose in date
  slots; the fixed TBD token only.
- L9 SEALS: every named agency and organization carries a mark. No official
  mark on file → deterministic monogram fallback + the org is listed in Band
  08's gap disclosure. A mark slot never renders empty.
- L10 DETERMINISM: bands render from pack + rule output. LLM writes brief prose
  slots only, at the existing 3.2%-class budget.

---

## 1. BAND SEQUENCE (fixed order; conditional bands marked)

| # | id | name | required | zero-state |
|---|----|------|----------|-----------|
| 00 | hero | Hero | always | n/a (headline derives from Band 01 count) |
| 01 | decisions | Account decisions | always | stated zero w/ screening receipt |
| 02 | forward | Forward lane | always | stated zero w/ forecast screening receipt |
| 03 | clocks | Decision clocks | always | stated zero |
| 04 | paper | Who holds the paper | always | stated zero |
| 05 | agencies | Past obligations by buying agency | always | stated zero |
| 06 | competitive | Account-by-account competitive picture | always | stated zero w/ completed-screen receipt or validation posture |
| 07 | events | Where to show up | always | stated zero w/ events screening receipt |
| 08 | method | Show the work | always | n/a (always has content) |
| 09 | targeting | Targeting | always | stated zero w/ enrichment receipt |
| -- | footer | Provenance footer | always | n/a |

Notes:
- The old golden-band grammar (forecast → candidate-review → signals → accounts
  → capabilities → leadership → method → calendar → events → evidence) is
  RETIRED. Mapping: candidate-review content feeds 01; forecast feeds 02;
  calendar feeds 03; accounts/signals feed 04-05; leadership is CUT from the
  client artifact (contacts return in the Apollo phase as a distinct product
  surface, not this document); capabilities is CUT (the client knows what they
  do); evidence dock functions move into 08 + inline linkage.
- No band may be added without amending this contract. The sanctioned
  historical additions: calendar/events (now Band 03/07), the competitive
  band (Band 06, amendment v1.3), and the targeting band (Band 09,
  amendment v1.4).

---

## 2. BAND SPECS

### 00 HERO
Fields: client name, artifact name ("Federal Opportunity Pre-Assessment"),
report date (press date, dd MMM yyyy), headline = the Band 01 R1 card count
with its definition sentence ("N account decisions where {client} and a named
rival are funded in the same buying environment"), context paragraph stating
the evidence basis (count of primary records; the no-unbacked-claims sentence),
chips = one per Band 01 card + one per Band 02 lane, each with its verb
(defend / map / gate / qualify) and its nearest date.
Zero-state: if Band 01 is zero, the headline states the strongest true count
available (records cited) — never a manufactured decision count.

### 01 ACCOUNT DECISIONS  (spine band; R1 + R2 output)
Content: R1 decision cards in rank order (nearest dated period end first).
Per card: eyebrow (rank, account, verb), title (the one-sentence tension),
2-3 bound claims (client-side with record link, rival-side with record link,
the clock), R3 vehicle context line when it fires, "Do now" action sentence
(imperative, deterministic template from rule output). R2 displacement alerts
render inside this band with their verbatim sentence receipts.
Selection rule: printed in Band 08, with named exceptions (a card that fails
half the rule is labeled a deliberate exception with its reason; a candidate
that meets the rule but is excluded is named, never silently dropped).
Leadership order strip: one sentence ordering the cards (defend X now, map Y,
gate Z).
Actionability requirement: every card carries verb + route + deadline. Owner
(named contact) is deferred to the Apollo phase by explicit gate decision.

### 02 FORWARD LANE  (R4 output)
Content: forecasts/shapeable notices at agencies where the client holds paper.
Per entry: the official forecast record link, estimated window, band/value AS
PUBLISHED (labeled lower-bound if summed — but forward values are NEVER summed
into any headline), the adjacency evidence (client-paper records that qualify
it), label "qualify — not pipeline", the next proof to collect, published
gov contact if on the record.
Inclusion rule: adjacency evidence required. A forecast with no client-paper
adjacency does not render, and the count of cut forecasts is stated in 08.

### 03 DECISION CLOCKS
Content: one table, ONLY clocks belonging to accounts present in Bands 01-02.
Columns: period/deadline end, account, scope + prime, obligated (on the cited
record), linked id. Expired clocks stay, flagged (L8). Sorted ascending.
Out-of-scope clocks are removed, and the removal is stated in 08.

### 04 WHO HOLDS THE PAPER
Content: top-N prime recipients over the cited record set. Columns: rank, mark
+ prime name, cited obligations, record count, one "why it matters" sentence
(deterministic template: recurrence, largest current renewal, both-sides
visibility). Caveat line: recipient = route on the record, not partner status;
validate vehicle access before outreach. Vehicle column from Task-1 derivation.
"Routes to work first" callout: max two primes, chosen by deterministic rule
(largest current client renewal holder; most recurrent current holder).

### 05 PAST OBLIGATIONS BY BUYING AGENCY
Content: collapsible per-agency record tables (buyer, prime, scope on record,
linked id), agency mark, per-agency total labeled per L5/L6. Explicit lede:
historical buying context, not market share/footprint/pipeline; forecast and
event values excluded from all figures. Placed BELOW the decisions by design.
Tone tags per agency: current decision / funded-rival / historical — derived
from rule output, rendered as the colored-edge convention.

### 06 ACCOUNT-BY-ACCOUNT COMPETITIVE PICTURE  (competitive engine output; added by amendment v1.3)
Content: contested buying accounts from the pre-composition competitive
build's account picture, rendered ONLY from the pack's own research slice
(the six competitive sidecars are the underlying record, so a saved-inputs
replay reproduces the band with no re-research). Per account: buyer + mark,
the deterministic recommended motion, and the cited seats: client paper,
channel holds, rival paper, rival history, forward signals, every record id
linked canonical. The motion rationale is stated once per motion. Research
mechanics (rounds, ledger cells, review states) and raw lane ids stay in
the sidecars; the band speaks the client lane vocabulary.
Seat filter: a rival record the composer attested WRONG DOMAIN loses its
rival seat here exactly as it loses its Band 01 displacement card. The
evidence stays in the pack and the report's other tables; the seat is
silently removed and the account's motion re-derives over the surviving
seats through the engine's own motion table.
Zero-state by coverage, never an untested zero (L4 + the
competitor_zero_untested rule): a screened lane states the completed-screen
receipt (comparison set read, evidence lanes, as-of date, accounts read); a
supplied-but-unscreened lane states COMPETITIVE VALIDATION PENDING; an
engagement with no named rivals states COMPETITIVE VALIDATION NEXT. A pack
pressed before the engine states that the picture was not computed, which
is not a zero. A mailto: link is never a cited record and never anchors a
seat.

### 07 WHERE TO SHOW UP
Content: max 3 featured events, each with: date/host, official event page link
(+ agenda link when published), what the agenda actually says (no
extrapolation), an "arrive with" question tied to a Band 01-02 account, and
marks. Monitor strip: up to 5 more, links only, explicitly "no claim attached."
Inclusion rule: an event renders featured ONLY with a stated reason tied to an
account in this report; otherwise monitor strip or cut. Event dates obey the
verified-date rule (event-date candidates only from "will be held / scheduled
for" sentences; registration deadline is the structured lead).
Zero-state: the screening receipt (notices screened, event-language candidates,
kept) — the honest thin-yield rendering.

### 08 SHOW THE WORK
Required blocks, in order:
1. Link-class legend with counts: primary evidence / contacts / official event
   pages / vendor marketing (must display 0 by L3).
2. The selection rule for Band 01, verbatim, with named exceptions and named
   near-misses.
3. Marks coverage: count with official marks, monogram list (L9 gap
   disclosure).
4. Manual-content disclosure: count of hand-entered (studio-edited) links,
   flagged as not machine-verified — populated by the export path from
   data-studio-link-edited; zero-state sentence when none.
5. "What this brief does not claim": the absence findings (e.g. no public
   solicitations for this vocabulary + the category-motion explanation),
   figure-definition notes (obligation ≠ ceiling ≠ run rate; current period vs
   potential end).
6. Screening receipts: notices screened (count, date, zero-metered-cost
   statement), term-yield summary when a lane is zero.

### 09 TARGETING  (action layer; added by amendment v1.4)
Content: the band that closes the report. Every band above it establishes
what is true; this one states who to call about it. Two layers, never
conflated:
1. TARGETING SPECS, deterministic and press-side. One spec per R1 decision
   card, R2 displacement alert, and R4 qualify row, computed from pack data
   only: the buying subtier or office the spec targets, the persona set
   sought (tiers 1 to 4), the row class (renewal / displacement /
   adjacency), and the rationale sentence, which cites the record. The
   specs render whether or not any contact was ever resolved.
2. CONTACT ROWS, enrichment. Resolved identities from the targeting store,
   written by the separate supply step, never fetched at press time.
Per contact row: name, title, organization, the persona tier it answers,
the provenance class and retrieved date, the joined record id linked
canonical, and the rationale line.

Band laws:
- JOIN LAW: every contact row joins at least one record_id already rendered
  in the evidence bands (ids `decisions`, `forward`, `clocks`, `paper`,
  `agencies`, `competitive`, `events`; bands 01 to 07 by §1 position). A row
  that joins no rendered record does not render. The rationale line is the
  cited claim and states only what the joined record shows. The contact
  identity itself is labeled enrichment and is NEVER asserted as a federal
  record: an identity is not evidence, it is a route to the evidence.
- PROVENANCE: every identity field (name, title, email, phone) carries a
  provenance class, `apollo` or `operator`, plus the retrieved date. No
  provenance, no render. A row whose provenance cannot be read is dropped
  and counted in the receipt, never rendered unlabeled.
- ZERO STATE: the band branches on the ENRICHMENT RECEIPT (attempted,
  source, timestamp, result count), never on an untested zero. A receipt
  stating attempted with zero results renders NO CONTACTS RESOLVED and is a
  valid certified state. No receipt at all means the supply step has not
  run: the band states TARGETING SUPPLY NOT RUN, which is not a zero,
  exactly as a pre-engine pack states not-computed in Band 06. A press with
  no R1, R2, or R4 row to target states TARGETING · NONE with the count of
  decision rows read. A pack pressed before the targeting rules existed
  states TARGETING NOT COMPUTED, which is not a zero either. These four
  postures are the band's complete vocabulary and the validator reads them
  from this law.
- The supply step is OUTSIDE the press. The press renders this band from
  the stored contacts only; a press makes zero Apollo calls, and that is
  test-enforced. The Apollo-ready export written beside the client artifact
  is an operator tool and is NOT part of the certified artifact.
- House style, the L7 banned vocabulary, the linkage laws, and the em-dash
  ban apply here exactly as everywhere else.

### FOOTER
Artifact name, evidence snapshot date, primary-record count, "prepared by The
GTM Group", source systems named (USAspending, SAM.gov, APFS), the
verify-before-acting sentence.

---

## 3. FORMAT INVARIANTS (validator-enforced, mechanical)

- Band order exactly as §1; band numbering rendered from position (no stale
  hardcoded numerals — the "08 inside 05" class).
- Well-formed HTML (existing gate), no em dashes in copy (existing gate),
  banned-vocabulary list (L7).
- Every record id in Bands 01-05 appears in exactly one primary location
  (dock-exactly-once successor: card/table-exactly-once) and links canonical.
- Client artifact contains: no <script>, no contenteditable, no data-studio-*,
  no hidden sections (physically removed), no localStorage references.
- All 10 house-style tokens (palette, type stack) from the Riverbed rework CSS;
  studio build = same document + studio chrome; the client build is generated
  from studio state by the export path, never authored separately.
- Aggregate figures: every one carries its L5 name + show-the-work toggle whose
  components sum exactly (existing aggregate laws).

---

## 4. CONFORMANCE + RELIABILITY GATE

A press is CONFORMANT when the validator passes every §3 invariant and every
§2 required field against this contract. The reliability gate (pre-Apollo) is:
five consecutive conformant presses on five distinct clients (incl. one cold
intake) off one commit, zero live SAM calls each, no human intervention
mid-press. Anything less yields a named defect list, not a shipped report.

## 5. OPEN ITEMS FOR TYLER (edit these, then approve)

- [ ] Featured-event max (3) and monitor max (5): confirm or change.
- [ ] Top-N primes in Band 04: Riverbed rework used 10. Confirm.
- [ ] Leadership/contacts band: confirmed CUT from client artifact until the
      Apollo phase? (Contract assumes yes per today's gate decision.)
- [ ] The R1 "verb" set: defend / map / gate / qualify — approve or amend.
- [ ] Hero headline when R1=0: approve the fallback rule in §2/00.
- [ ] Name of the artifact: "Federal Opportunity Pre-Assessment" everywhere,
      or does prospect-mode get a thinner name?


---

## AMENDMENT v1.1 (2026-08-04, operator-directed: visual richness)

- Band 05 carries a deterministic inline SVG: horizontal bars of past
  obligations by buying agency, names and values printed, rendered from the
  SAME pack sums the tables cite. Static SVG only; no script, no library;
  token colors.
- Band 03 carries a horizontal timeline strip above the table: one tick per
  clock positioned by date, expired ticks in the warn color, bounded by the
  earliest and latest dates the rows themselves print.
- The hero carries the ghost-numeral watermark of the golden house style
  (the record count the hero already states).
- LAW: visuals are deterministic renderings of cited pack values. A visual
  may not display any number, name, or date the band's tables do not carry.

---

## AMENDMENT v1.2 (2026-08-04, operator-directed: optimize for actionable content)

Ruling: "optimize for actionable content." The account-binding rules in
§2/03 and §2/06 are FOCUSING rules, not suppression rules. When the spine
bands name no accounts at all (an entity-less packet, or a client with no
head-to-head paper), binding to them removes everything and the report
prints zeros while live, dated, cited opportunity sits unused in the pack.

- **Band 03 fallback.** When Bands 01-02 name NO accounts, the clocks table
  renders the cited record set's own dated clocks instead of a zero. Open
  clocks are listed first, expired ones keep their flag (L8 unchanged), and
  the lede states that the clocks are shown against the record set because
  no account carries both client and rival paper.
- **Band 06 fallback.** When Bands 01-02 name NO accounts, an event may be
  featured on the timing test alone (open registration or a future date).
  The pack-fact binding and live URL verification still gate it upstream;
  only the account half of G5 is dropped, and only when no account exists.
- **Empty bands never carry model prose.** A band that renders no rows
  takes its deterministic lede. Model prose in an empty band asserted
  forecast records, dates and contacts the band did not render, which is
  the §3 no-unbacked-claims law failing through the prose door rather than
  the figure door.
- **Unchanged:** evidence-integrity gates stay. Band 02 still requires
  client-paper adjacency, expired clocks still flag, values still render
  as published and are never summed, and no band invents a record.

LAW: a rule that would empty a band renders the strongest cited content
that rule was meant to rank, never a zero, unless the pack itself is empty.

---

## AMENDMENT v1.3 (2026-08-05, operator-directed: the competitive band renders)

The proactive competitive build (the pre-composition engine that writes six
sidecars per press) gains its client-facing band. §1 adds Band 06
`competitive` (Account-by-account competitive picture); events and method
renumber to Bands 07 and 08 by table position. Band references by id stay
authoritative across amendments: v1.2's "Band 06 fallback" reads as the
events band (now Band 07); its text is a historical record and is not
edited.

- The band renders ONLY from the pack's own research slice
  (`research.competitive`: completeness, account picture, confirmed roster,
  lanes searched), which mirrors the six competitive sidecars, so a
  saved-inputs replay reproduces the band byte-for-byte with no
  re-research. A pack pressed before the engine states that the picture was
  not computed, which is not a zero.
- LAW: an untested competitive lane never prints a tested zero. The band's
  zero-state branches on the derived coverage state: the completed-screen
  receipt when the rival lane actually ran, COMPETITIVE VALIDATION PENDING
  when a named comparison set is not yet screened, COMPETITIVE VALIDATION
  NEXT when no comparison set is named.
- LAW: a composer WRONG DOMAIN attestation removes the record's rival seat
  in this band exactly as it removes the Band 01 displacement card. The
  account's motion re-derives over the surviving seats and the record
  itself stays in the pack and the report's other tables.
- Research mechanics and raw lane ids stay in the sidecars; the band speaks
  the client lane vocabulary. A mailto: link never counts as a cited
  record.

---

## AMENDMENT v1.4 (2026-08-05, operator-directed: the report closes on the action layer)

§1 adds Band 09 `targeting` (Targeting) after 08 method and before the
footer. It is the action layer the whole assessment feeds: every band above
it establishes what is true, and this one states who to call about it.
Band references by id stay authoritative across amendments; no band below
09 renumbers.

- SUPERSESSION, stated rather than left to contradict. §1's note that
  "leadership is CUT from the client artifact (contacts return in the
  Apollo phase as a distinct product surface, not this document)" was
  written before the Apollo phase had a shape. That phase now lands HERE,
  inside this document, as Band 09. What stays cut is the old leadership
  band: a roster of names with no record behind them. What returns is
  narrower and joined: a contact renders only against a record the report
  already cites. §5's open item on the contacts band is answered by this
  amendment. The earlier note's text is a historical record and is not
  edited.
- LAW: THE JOIN IS THE ADMISSION TICKET. A contact row renders only when it
  joins at least one record_id already rendered in bands 01 to 07. The
  rationale line is the cited claim and speaks only what the joined record
  shows. The identity itself is labeled enrichment; it is never asserted as
  a federal record, never counted as primary evidence, and never enters the
  link-class primary tally.
- LAW: NO PROVENANCE, NO RENDER. Every identity field carries its
  provenance class (`apollo` or `operator`) and its retrieved date. An
  unlabeled identity is dropped and counted, never rendered bare. This is
  the per-figure provenance law of the fact registry applied to people.
- LAW: THE RECEIPT DECIDES THE ZERO. The band branches on the enrichment
  receipt (attempted, source, timestamp, result count). Attempted with zero
  results is a real, certifiable zero and renders NO CONTACTS RESOLVED with
  its receipt. No receipt is NOT a zero: the band states TARGETING SUPPLY
  NOT RUN, the same distinction Band 06 draws between a screened zero and a
  picture that was never computed.
- LAW: ZERO LIVE CALLS AT PRESS TIME, PRESERVED. The targeting specs are
  deterministic press-side rule output over pack data; the contacts come
  from a durable store written by a separate sweep-class supply step. The
  press reads the store and calls nothing. The supply step is gated OFF by
  default, fails loud on a missing key or an API error, is call-capped, and
  prints its spend in the receipt.
- The Apollo-ready export written beside the client artifact is an operator
  tool for loading a sequence. It is not part of the certified artifact and
  is never delivered as one.
