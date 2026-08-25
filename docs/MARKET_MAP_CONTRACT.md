# MARKET_MAP_CONTRACT.md
## Federal Market Map · operator-locked external product contract, v4 (2026-08-23)

**LOCKED OPERATOR RULING:** this is LILA's sole external product contract.
The slot membership, names, and order below override every earlier report,
Signal Board, Candidate Review, Market Map, and planning-document sequence in
the repository. No agent may add, remove, rename, split, combine, or reorder a
slot. Only a later explicit instruction from the operator may amend it.

### Client-rendering lineage

The client-facing visual reference is the 14 August 2026 Red Hat / TD SYNNEX
diligence-ready deliverable. Its contract is black, white and red; sans-serif;
logo-led; evidence-linked; executive-first; and organized as eight fixed
product slots. The older warm-paper Riverbed shell remains an internal
studio surface while its data model is migrated, but it must not be promoted
or described as the Red Hat-standard client deliverable.

A `certified` data verdict is necessary and not sufficient for client
promotion. The frozen client HTML must also pass the static visual contract:

- a real current-client mark and the GTM mark are embedded, not transparent
  1x1 placeholders;
- each agency-bearing card or row renders its resolved authority mark or an
  explicit readable fallback;
- CSS is balanced after client export;
- every visible control has a working action;
- every internal link resolves, every HTML id is unique, and no action uses
  an empty, `#`, or `javascript:` destination;
- USAspending identities containing `_-NONE-_-NONE-` are rejected and routed
  to an exact-PIID public award search instead;
- desktop, tablet, mobile and print renders are inspected after the final HTML
  bytes are frozen.

The eight-slot renderer consumes the shared graph-backed projection directly.
Any generic or seven-section studio render may still be saved for operator
use, but it is an internal view and cannot receive external promotion state.

The client slot identities are:

| # | slot id | locked client heading |
|---|---|---|
| 1 | `priority-pursuits` | Prioritized and Actionable |
| 2 | `research-mesh` | NAICS + Keywords + Semantic (Research Mesh Deployment/Search) |
| 3 | `agency-spending` | Agency Spending (in your category) |
| 4 | `competitors` | Competitors and Landscape |
| 5 | `federal-opportunities` | Federal Opportunities Identified + Targets Specific to Each Opportunity (listed on notice and then complemented with search and enrichment) |
| 6 | `teaming-opportunities` | Teaming Opportunities + Teaming Targets |
| 7 | `future-forecasts` | Forecasts with Directional Graphs |
| 8 | `industry-days-events` | Industry Days + Events |

### Slot-filling law

The eight slots are the permanent product frame. Their client-specific
contents are filled from the governed research mesh and Federal Pursuit Graph
on each press.

- Every slot always exists and always renders in the locked order.
- Evidence changes the contents of a slot, never the slot schema.
- A slot with no qualified content states the searched universe, the observed
  zero or gap, and the next research action. It is never deleted or padded.
- Every claim, figure, target, graph, and recommended action remains bound to
  its source evidence and retrieval time.
- A record has one owning slot. Prioritized and Actionable may summarize and
  link to records owned below, but it never creates a duplicate evidence row.

The slot payloads are:

1. `priority-pursuits` fills with LILA's ranked pursuits and the evidence,
   route, timing, owner, and next action that justify each priority.
2. `research-mesh` fills with the client frame, NAICS boundary, approved
   keywords, semantic concepts, deployed source/search lanes, and search
   receipts.
3. `agency-spending` fills with category-specific agency obligations and the
   exact award records and arithmetic behind them.
4. `competitors` fills with identified competitors, their relevant awards,
   spending evidence, incumbent positions, and competitive landscape.
5. `federal-opportunities` owns every current federal opportunity. Live records
   with direct service fit render as pursuits, with a separate `prime`, `team`,
   or `verify` route action. Only unresolved service fit enters the visibly
   separate decision queue with its exact blockers and next research action.
   Each record retains a target set specific to that opportunity.
   Published notice contacts render first; governed search and approved
   enrichment complement the missing roles.
6. `teaming-opportunities` fills with evidence-backed teaming routes and the
   partner targets required to act on each route. When a Slot 5 notice requires
   a partner route, Slot 6 carries a non-owning reference back to that notice;
   it never renders the notice as a second opportunity.
7. `future-forecasts` fills with agency forecasts, budget direction, and
   relevant federal or market news, plus evidence-bound directional graphs and
   vector maps where the underlying data supports them.
8. `industry-days-events` fills with relevant industry days and events, the
   official registration or agenda evidence, and the pursuit-specific reason
   to attend.

Each canonical award, notice, forecast, contact, route, or event renders once
in the slot that owns it. Executive promotions elsewhere are internal links to
that canonical record, never duplicated cards or rows.

Technical fit, timing, and commercial route are independent decisions. A
credible current fit with unstated access remains a visible `verify` pursuit.
A credible fit with a restricted direct route remains a visible `team`
pursuit. Neither condition is permission to discard or hold the underlying
opportunity.

### Complete-bundle release law

`agents/golden_press/product_bundle.py` is the only external product press.
The Command Center launches it with step `lila_release`. A successful release
contains the scriptless client HTML, editable studio HTML, exact eight-slot
JSON, certified Federal Pursuit Graph, pressed evidence pack, captured replay
inputs, validation receipt, SHA-256 manifest, and deterministic ZIP. Promotion
is atomic and fail-closed: missing operator approval, a graph violation, a
render violation, or any changed file hash makes the current product
non-releasable. Every captured research input must carry an aware receipt at or
before the release's `classification_as_of`; snapshot assembly cannot backdate
later evidence into an earlier market view. Older output families remain
readable only under the roles in
`agents/reports/product_families.py` and cannot substitute for this bundle.

The seven-section Riverbed Federal Market Map below documents the internal
studio semantic baseline that preceded the client visual contract. It remains
useful for data invariants during migration, but the client-rendering lineage
and eight-slot sequence above control anything promoted as an executive
deliverable.

**Why this document exists at all.** The assessment family drifted. A press
carried 38 model-prose slots against a standing no-prose rule, one contact
appeared 20 times because 20 specs named the same agency, and a banned
invented word shipped an artifact uncertified. None of that was caught,
because nothing was watching for it. The bands enforce *structure*; nothing
enforced *density*. This contract enforces both.

---

## Appendix A. Legacy internal-studio contract

Everything below this heading documents the seven-section internal studio
renderer only. It is retained for migration tests and does not override the
eight-slot client-rendering contract or the manifest above.

### A0. THE THREE LAWS THIS FAMILY ADDS

Everything below is a consequence of these.

- **L-M1 SAY WHAT WE HAVE AND WHAT WE DO NOT.** Every column that could be
  empty states the gap in words instead of rendering blank. "Not in current
  research", "Enrichment needed", "Named alliance lead needed". A blank cell
  is a defect. A stated gap is a work order.
- **L-M2 NEVER REPEAT TO FILL SPACE.** A fact appears ONCE, in the section
  that owns it. A person, an award id, an opportunity, or a sentence that
  appears twice is padding, and padding is what makes a reader stop trusting
  the document. Where one row genuinely serves several motions, it is stated
  once with its motions named, never duplicated per motion.
- **L-M3 NO PROSE THAT A NUMBER COULD REPLACE.** The document is signal:
  figures, ids, links, names, dates, actions. The operator does the talking.
  Invented vocabulary is banned outright, and every heading is a plain
  declarative sentence about what the section IS.

---

### A1. LEGACY STUDIO SECTION SEQUENCE

| # | id | declarative heading | what it carries |
|---|----|--------------------|-----------------|
| 1 | `company` | This is {client} as we understand it. | description, products tracked, channels tracked, capability keywords, NAICS boundary, known federal footprint, confirm-before-next-press |
| 2 | `market` | This is the money already spent in {client}'s category. | combined cited category spend, client split, rival split, per-agency table with record counts |
| 3 | `competition` | These competitors are winning money in the same market. | rival total, competitor families with receipts, one representative award id per family |
| 4 | `opportunities` | These are the {n} opportunities worth qualifying now. | official record id + link, published value AS PUBLISHED, type and access, what to do, named POC |
| 5 | `teaming` | These are the teaming routes to work first. | paper holder, why call, who to start with, phone or stated gap, award receipt |
| 6 | `contacts` | These are the people to contact, and the contact data still needed. | ready-now count, next-research-order count, then motion / organisation+record / person / published route / phone |
| 7 | `events` | Register for the rooms where buyers and partners will be. | date, host, one-line why, official register or agenda link |

Notes:
- Section 6's heading uses a COMMA. The reference artifact used an em dash
  there, which is banned by R9 and would ship the press uncertified.
- No legacy studio section may be added, removed, or reordered without
  amending this appendix.
- A section with nothing to say renders its stated zero and its coverage
  receipt. It is never dropped, because a missing section reads as an
  oversight while a stated zero reads as a finding.

---

### A2. LEGACY STUDIO SECTION LAWS

### 1 COMPANY
Rendered from the approved capability profile ONLY. This section is what
determined the search boundary, so it is shown first and carries the
confirm-before-next-press line. Client products and channel names are
listed as such and never as competitors (see `client_identity`).

### 2 MARKET
The combined figure is the sum of the cited records and nothing else. The
client split and the rival split must reconcile to the combined total
exactly. Per-agency rows carry the record count that produced them.
Category spend is labeled as past obligations, never as market size,
pipeline, or share.

### 3 COMPETITION
One card per competitor family, each with its linked award count, its total,
and ONE representative award id at source precision. The full ledger is a
drawer, not repeated rows. Screened-but-not-promoted counts are stated.

### 4 OPPORTUNITIES
Every entry is an official record with a canonical link. Published values
render AS PUBLISHED (a band stays a band) and are never summed. Each entry
states its access route and a specific action. The prime-or-team decision
is DERIVED from award history (`prime_posture`), never assumed.

### 5 TEAMING
Every route names the award that proves the holder already holds relevant
federal paper. The person is the commercial role most likely to route the
conversation. A missing person or phone states the gap; it never renders a
blank or invents a name.

### 6 CONTACTS
Split into what is usable now and what needs research, with both counts
stated. Government-published routes are separated from partner-side
contacts. Every row carries the motion it serves, so a reader knows which
play the call belongs to. A person serving several motions appears ONCE with
its motions named (L-M2).

### 7 EVENTS
Each row is an official event or registration page. The one-line why ties
the room to an account or a route named elsewhere in this document. No
event renders without a live-verified link.

---

### A3. LEGACY STUDIO FORMAT INVARIANTS

- **Sections**: exactly the seven of Appendix A1, in order, by id, for the
  internal studio renderer only.
- **Headings**: each legacy section's H2 is a declarative sentence; the
  id-to-shape mapping in Appendix A1 holds.
- **No em dashes** anywhere in rendered text (R9, existing).
- **Banned vocabulary** (L7, existing): corridor and the invented-term list.
  Extend the list in the validator, not here.
- **No blank cells** (L-M1): a table cell is non-empty, or carries an
  explicit gap phrase from the approved list.
- **No repeated sentence** (L-M2): no normalized sentence of 6+ words
  appears more than once in the artifact.
- **No repeated row key** (L-M2): within one table, no person, award id, or
  record id appears in two rows.
- **Receipts present**: sections 2 to 5 each carry at least one canonical
  record id linked to its source system.
- **Coverage stated**: every section carries either a completeness claim or
  a named gap. Silence is a violation.
- **Client artifact**: no script, no contenteditable, no data-studio-*, no
  hidden sections.

---

### A4. LEGACY STUDIO CONFORMANCE

The internal studio render is conformant when its validator passes every
Appendix A3 invariant and every Appendix A2 required field. Client promotion
still requires the eight-slot visual contract, manifest, and rendered
checks at the top of this document.
