# LILA CLIENT VIEW UX BUILD

One prompt, frozen scope. Read fully before acting. This is a UI-only build. Run it in its OWN session on Opus 4.8 (not Fable — preserve Fable budget for composes), on its own branch `ui/client-view`. It may run in parallel with the golden build ONLY because the file fence below makes the two disjoint; if any step would touch a file outside the fence, stop and report instead. Merge order when both land: the golden build branch merges first; this branch rebases onto it.

## ONE GOAL

Rebuild the Command Center client view (the page shown after clicking into a client) to the approved design. The page is CLIENT-AGNOSTIC: slug-parameterized, rendering any client whose artifacts exist under data/state/ — riverbed now, redhat the moment its intake and press land. No client names hardcoded anywhere in the UI code.: identity header with capability description, clickable competitor chips, a metric-card scoreboard (REPLACES the current scoreboard), a single-line ticker, a two-pane working area (accreting key-dates calendar + reports rail), a truthful press progress rail, a hero report reveal card, and a Run targeting stub. Data comes exclusively from artifacts the pipeline already produces. Zero pipeline changes.

## FILES YOU MAY TOUCH

ui/server.py, ui/index.html, and any new files under ui/static/. NOTHING else. Never touch agents/, run_candidate_review.py, fixtures/, or any pipeline code. If the pipeline does not already write a stage status file, add a note to the state doc and render the press rail from the most recent completed run's data; do not modify pipeline code to emit it.

## FIRST ACTION

Save this prompt to docs/plans/CLIENT_VIEW_UX.md. Write docs/plans/CLIENT_VIEW_UX_STATE.md at the end of each phase: what changed, files touched, next action.

## ASSET LAW: LOGOS AND SEALS THROUGHOUT THE CHROME

The client view wears the same seals and logos as the deliverable. Source of truth: HARVEST from the client's pressed report HTML, read-only — the cc-model builder parses the report file's asset slots (class sb-route-seal / sb-award-logo), extracts the embedded image data-URIs, and keys seals by their agency label and the logo by the client. Assets Tyler placed in the report are thereby the assets the CC shows; the builder never fetches, generates, or invents an image. Placements:
- Identity header: harvested client logo replaces the initials block (initials remain the fallback). Harvest the client logo from the report's HEADER logo slot (the operator's mark slot the press neutralizes in the hero chrome) — NOT sb-award-logo, which are per-award recipient marks. assets.logo binds to that header slot; RVBD-style monogram stays fallback until the operator places the mark once.
- Client list rows: same logo, 32px.
- Calendar rows: 16px agency seal before the row label where that agency's seal exists in the harvest.
- Ticker items: 14px seal inline before the agency token.
- Reports rail cards and the hero card: client logo at 20px beside the title.
- Analyst layer and targeting stay text-only (working surfaces).
Fallback everywhere: a neutral monogram chip (two-letter agency code, inset square, 11px mono) — designed, not broken. Seals render at fixed square sizes, never stretched, never recolored.
Acceptance: with the riverbed report on disk, calendar and ticker show harvested seals for every agency present in its slots; a client with no pressed report renders monograms cleanly.

## LINKAGE LAW: EVERYTHING CLICKS THROUGH TO ITS SOURCE

Authority comes from exactness plus verifiability. Every rendered record element in the Command Center is a link to its primary source: calendar rows open the record's source_url; ticker items link (or scroll to their linked calendar row); metric-card context lines that cite record counts link to the evidence view; report cards open the pressed HTML; any news item rendered links to the article; every solicitation number, award ID, and forecast ID shown anywhere is an anchor, styled in mono with a subtle external-link affordance on hover. No dead text where a source exists. The only unlinked elements are computed aggregates (which cite their component records via the evidence view) and UI chrome. Exact dates, solicitation numbers, seals, and logos are rendered precisely as sourced; precision is part of the design language, not decoration.

## DESIGN LAW: THE PRESSED REPORT IS UNTOUCHABLE

The generated Pre-Assessment HTML keeps its inherited golden design exactly as the press produces it. This build styles the Command Center AROUND the report. The report renders in an iframe or opens in a new tab. Never restyle, inject CSS into, or re-render report internals.

## DESIGN TOKENS

Define once as CSS custom properties in the CC stylesheet. These exact values are authoritative (approved from rendered screenshots); do not derive CC theme colors from the golden report's light palette — the CC is dark by law, the report is light by law:

```css
:root {
  --lila-page:      #1A1A18;   /* page canvas (dark) */
  --lila-card:      #262624;   /* raised card */
  --lila-inset:     #1F1F1D;   /* metric card fill, inset panels */
  --lila-ink:       #F2F1EE;   /* primary text */
  --lila-muted:     #A6A49D;   /* secondary text */
  --lila-faint:     #7C7A73;   /* hints, ghost labels */
  --lila-border:    rgba(255, 255, 255, 0.10);  /* hairlines */
  --lila-border-strong: rgba(255, 255, 255, 0.26);
  --lila-navy:      #042C53;   /* brand anchor, logo block */
  --lila-navy-tint: #17334F;   /* chip fills, active-stage bg */
  --lila-navy-text: #8FBCEC;   /* chip text, links, forecast tag */
  --lila-ochre:     #D79A3F;   /* citation/clock accent */
  --lila-danger:    #E37B72;   /* deadline tag */
  --lila-success:   #7CBF5A;   /* completed stage check */
  --lila-radius:    8px;
  --lila-radius-lg: 12px;
}
```

THEME LAW: the Command Center is DARK — these exact tokens, approved from rendered screenshots on 2026-07-24. The pressed report keeps its own light golden design untouched; the contrast (dark control room, light paper deliverable) is intentional. The authoritative visual target is fixtures/ui/LILA_UI_REFERENCE.html — P1 acceptance is side-by-side indistinguishable from that file in a browser.

Typography: IBM Plex Sans for all Command Center UI. Playfair Display ONLY for the hero report card client name. IBM Plex Mono for record IDs, dollar figures in the ticker, and receipts. Two weights only: 400 and 500. No 600/700. Sentence case everywhere. Minimum font size 11px.

Aesthetic rules (this is the "look"):
- Flat. No gradients, no drop shadows, no glow.
- Hairline borders: 1px solid var(--lila-border) on dark cards.
- Card radius var(--lila-radius-lg); controls var(--lila-radius).
- Generous whitespace; padding 16-20px inside cards.
- Color encodes meaning only: navy = brand/identity, ochre = clocks, red = deadlines, blue = forecasts, green = completed. Everything else stays ink/muted/faint.

## DATA CONTRACT (read-only)

Real artifacts already exist from the golden build's Checkpoint 1 — bind to these, do not invent fixtures:
- data/state/golden_build/riverbed.research.raw.json (research output)
- the Riverbed evidence pack and selection/universe files in data/state/golden_build/
General shapes:

- Strategy packet (per client): keywords, keyword families, NAICS, PSC, research_entities, gate status.
- Research output JSON (per client): company description, products, competitors, resellers, keywords/NAICS/PSC.
- Evidence pack JSON (per press run): records with all fields incl. dates, dollars, lane, source URL.
- Pressed report HTML file path(s) per run.
- Stage status JSON if the pipeline writes one (see FILES section).
Merge rule for accretion: union of all press runs' packs for the client, deduped by contract number / notice ID (same identity rule as the pipeline). Calendar = every dated field across the merged pack, tagged by type: deadline (response due), clock (award period end), on-ramp, forecast (window). Sort ascending, past dates styled faint, not hidden.

## CLIENT LIST SHELL (the screen before the client view)

The landing/list must not be the old look leading into the new one. Bounded restyle only: apply the dark tokens to the client list — one card row per client (logo block, name, capability line 12px muted, "N records · last pressed <date>" 11.5px faint), click opens the client view. Optional if time allows: a "New client" input on this screen that posts the slug to the EXISTING intake dispatch endpoint, styled dark — no new backend, no new features. Token restyle is P4 and never cut; the New client input is the first thing cut.

## PAGE STRUCTURE (top to bottom)

1. IDENTITY HEADER
   48px navy square with client ticker/initials (existing logo drag-and-drop slot may replace it), client name 17px/500, one-line capability description 13px muted sourced from research output, right-aligned "Press new report" button (outline style: transparent bg, 1px border, hover fill --lila-inset).

2. COMPETITOR CHIP ROW
   Label "Competitors" 12px muted, then pill chips: 12px text, 3px 10px padding, 999px radius, bg --lila-navy-tint, color --lila-navy-text. Click filters the calendar and evidence to records mentioning that entity (client-side filter over the merged pack; a where-clause, not a feature). A trailing neutral chip summarizes resellers ("Resellers: Swish Data, FCN +4"), click expands the list.

3. METRIC-CARD SCOREBOARD (replaces current scoreboard entirely)
   Grid: repeat(auto-fit, minmax(150px, 1fr)), gap 12px. Each card: bg --lila-inset, radius --lila-radius, padding 14px 16px, NO border. Contents: 12px muted label, 24px/500 number, 11px faint context line. Cards and their bindings:
   - Candidate corridors: count of corridor/candidate groupings in the latest pack; context "N active awards".
   - Active footprint: sum of obligated dollars on client-entity records; formatted $X.XM; context "obligated, cited".
   - Forecast lower bound: sum of forecast lower bounds; ">$XM"; context "N forecast records".
   - Evidence base: unique record count; context "unique linked records".
   All numbers computed from the pack, rounded for display, never hardcoded.

4. TICKER
   Single-line bar: 1px hairline border, radius --lila-radius, 8px 12px padding, broadcast icon in --lila-navy-text. Content: dated records as "TYPE · DD MMM · AGENCY — short label · $X.XM" in 12px, mono for figures. CSS marquee (duplicated track, translateX keyframes), pause on hover. Click a ticker item: scroll to its calendar row.

5. TWO-PANE WORKING AREA
   Grid minmax(0,1.4fr) / minmax(0,1fr), gap 14px; stacks to one column under 720px.
   LEFT - KEY DATES: bordered card, header "Key dates" with calendar icon and subtext "accretes each press". Rows: 6px 0 padding, hairline bottom border, [date 12.5px muted, min-width 58px] [label, flex 1] [type tag right: deadline=--lila-danger, clock=--lila-ochre, on-ramp=--lila-navy-text, forecast=--lila-navy-text]. Row click opens the record's source URL in a new tab.
   RIGHT - REPORTS RAIL: bordered card, header "Reports". One inset card per pressed report: title 13px/500, meta 11.5px muted "Pressed DD MMM · N records · open report", click opens the pressed HTML. Below reports, the Run targeting stub (section 8).

5.5 ANALYST LAYER PANEL (between the working area and the press rail)
   The human gate, restored in full detail in the dark aesthetic.
   Header: "Analyst layer" 13.5px/500, subtext "The human gate; refine with sales, or approve as inferred" 12px muted, right-aligned status pill (DRAFT from intake research / APPROVED + date, 11px letter-spaced, hairline border).
   Content, bound to the strategy packet + research JSON:
   - Entity rows (110px label column + chip flow): Products and Competitors as navy-tint chips; Resellers as neutral inset chips; each row ends with a dashed "+ add" affordance.
   - Keyword families: outlined chips as the summary row, and a WORKING VIEW beneath: one row per keyword family — term 12.5px (180px min column), one-line description 12px muted ("what it covers, what phrasing it matches"), right tag KEYWORD 11px.
   - NAICS and PSC working rows, same anatomy: mono code + official short title (from a static official-title map shipped in ui/static; never invented client-side), one-line "why this code" description bound from the strategy packet where present, right tag NAICS/PSC. Bare chips are the collapsed state; rows are the open state; default open when status is DRAFT.
   - Rationale surface: clicking any chip shows its grounded rationale and source from research_entities (name/kind/rationale/source) in an inset strip: "RATIONALE + name" 11.5px faint label, 12.5px muted text. One strip, replaced per click.
   - BOUNDARY LAYER, "marginal calls, your decision": one row per boundary item — name (muted, mono for codes), one-line reason it is marginal (source frequency, adjacency, noise risk) bound from the packet, and Include / Exclude buttons per row (buttons wired in P3; disabled-rendered in P2). Renders whenever boundary items exist; the adversarial pass will feed this, and marginal inferences from intake feed it now.
   - Actions right-aligned: "Revise" outline button, and THE PRESS BUTTON — the only solid-filled element on the entire page: navy #042C53 block, #E6F1FB text, radius 8px, padding 8px 18px, two-line: "Approve and press →" 13.5px/500 over a 10px letter-spaced sublabel in --lila-navy-text reading "SWEEP · COMPOSE · VALIDATE" — no source count stated; the SOURCE WALL (section 6.5) shows the breadth instead. Hover: background lightens one step (#0A3A6B), arrow nudges 2px. No glow, no gradient — its power is being the page's only fill.
   - APPROVE CEREMONY (choreography, ~400ms total): click fires the existing press dispatch and disables the button; button enters PRESSING state (darker #0A1F38 fill, spinner + "Pressing…", sublabel becomes live "STAGE n OF 8 · <stage name>" from the status poll); the panel collapses to its one-line APPROVED summary with Reopen; the press rail materializes directly beneath with stage 1 active and the page ease-scrolls to it. Truthfulness law governs the sublabel and rail throughout. On a loud press failure the rail stops on the failed stage and the button returns as "Re-press" with the same sublabel. Double-fire impossible while a press job is live for the slug.
   Monday scope: full-detail READ render + working Approve is required. Chip add/remove through the existing revise() path is P3; if timeboxes force cuts, chips fall back to read-only and "+ add" hides. The panel itself is NEVER cut.

6. PRESS PROGRESS RAIL (visible during/after a press)
   Truthfulness law: a stage lights ONLY when the pipeline actually completed it. STATUS SOURCE, in preference order: (1) a stage status JSON if the pipeline emits one; (2) READ-ONLY parse of the existing CC job log (data/state/job_logs/<job>.log) — tail it for stage markers and map them to rail stages; never write to it, never modify pipeline code; (3) retrospective render from the last completed run, gap noted in the state doc. The live approve ceremony requires source (1) or (2); verify (2) works against the riverbed take-4 log before Monday. Never animate fake progress. Stages: Company research, Analyst layer approved, Retrieval (4 lanes), Sufficiency gate, Composing report, Adversarial critique, Fact validation, Pressed.
   Row anatomy: [status icon] [stage name 13.5px] [right-aligned receipt 12px muted]. Completed: green check + receipt from real stage output ("18 unique records · deduped"). Active: row bg --lila-navy-tint, radius, navy text, spinner icon, live receipt. Pending: faint circle icon, muted text. Failed: red icon, the stage's error message as receipt; rail stops there.
   Poll cadence 2s while a press is live.

6.5 SOURCE WALL (directly beneath the press rail)
   The long field of sources, restored from the previous Command Center. One panel, full width, dense multi-column grid (repeat(auto-fill, minmax(150px, 1fr)), gap 6px 14px), one entry per source adapter, bound to the FULL registry via the cc-model endpoint — every adapter renders, no sampling, no hardcoded list; it looks long because it is long.
   FULL SOURCE SURFACE: sweep EVERY source-definition home in the repo across all flows — procurement adapters (horizon_discovery, tools/api and peers), forecast sources, vehicle sources, the NEWS/PRESS outlet lists used by the brief/news flows, and the web research/search channels the intake uses. Render GROUPED (PROCUREMENT · FORECASTS · NEWS & PRESS · SEARCH · VEHICLES) with 10px category tags. Every row traces to a real definition — never pad. Header count = the swept total. In the P2 report, list every source-definition file found and its per-group count.
   COLLAPSIBLE: header-click toggle; collapsed = one row (Sources + count + a thin dot-shimmer strip); default expanded; state remembered; chevron; 150ms ease.
   Entry anatomy: 6px status dot + source name 11.5px (mono for API-style names), optional 10px faint category tag.
   Dot semantics (truthfulness law):
   - GREEN with a soft pulse = registered and enabled, straight from the registry. Stagger the pulse animation delays (e.g. delay = index * 137ms mod 2s) so the wall shimmers instead of strobing.
   - AMBER steady = registered but degraded when status is known (e.g. SAM quota exhausted), with the reason as a title tooltip.
   - Dots never claim live uptime that isn't measured; the pulse means "in the arsenal," not "pinged just now."
   During a live press, sources belonging to the currently active lane get the navy-tint highlight treatment (background chip behind the entry) while that stage runs, then return to rest.
   Panel header: "Sources" 13.5px/500 with the live registry count right-aligned 12px muted (the count may appear HERE — it is read from the registry, never typed).

7. HERO REPORT CARD (rail terminus)
   Centered card: 1px border (border-strong equivalent), radius --lila-radius-lg, bg --lila-inset, padding 22px 20px. Eyebrow "FEDERAL OPPORTUNITY PRE-ASSESSMENT" 12px faint letter-spaced; client name 20px/500 Playfair; stat line 12.5px muted computed from the pack ("18 records · 4 corridors · >$251M forecast demand · every figure source-linked"); "Open full report" button. When a press completes, smooth-scroll the page to this card once.

8. TARGETING LAYER + RUN TARGETING STUB
   The Targeting layer is a first-class visible section of the client page even though the capability is not built: a dark panel titled "Targeting" with status pill "NEXT RELEASE · builds from this evidence base", containing four dashed ghost cards (170px min): Buying-office personas ("Who buys this, per corridor"), Account ownership ("Hypotheses from seller patterns"), Verified contacts ("Named people, checked live"), Outreach sequencing ("Meetings from the evidence"). Non-functional, honest, demo-speakable. Placed after the hero/press area.
   The small RUN TARGETING STUB in the reports rail remains as the teaser and scrolls to this panel on click.

   Original stub spec:
   Dashed 1px border card below the hero/reports: title "Run targeting" 13.5px/500, subtext "Personas, account owners, and outreach map from this evidence base" 12px muted, right-aligned "Run" button with arrow icon. Click opens a small honest panel (no fake data): "Targeting builds from this evidence base: buying-office personas, account ownership hypotheses, verified contacts, outreach sequencing. Next release." Dismissible. Never dead-ends, never pretends to run.

9. EMPTY / PARTIAL STATES
   Fresh client after intake only: header, description, and chips populate; scoreboard cards show an em-less ghost "—" with context "populates on first press"; ticker hidden; calendar shows any research-sourced dates or "No dates yet — press a report"; reports rail shows only the targeting stub, dashed. Partial is a designed state, not a broken one.

## PHASES AND TIMEBOXES

P1 (1h): tokens + static layout with hardcoded Riverbed pack data, pixel-matched to spec.
P2 (1.5h): data binding to real research output + merged evidence packs, INCLUDING the analyst layer panel full-detail render; accretion merge + calendar extraction; competitor filter.
P3 (1h): press rail wired to status polling (or retrospective mode), analyst-layer Approve wiring + chip editing via revise(), hero reveal scroll, targeting panel.
P4 (30m): empty states, responsive stack, dead-link sweep.
If over a timebox: write state, report cause, cut in this order: competitor filter interactivity -> New-client input -> ticker click-to-scroll -> responsive stack. NEVER cut: client-list token restyle, analyst layer panel (read-only fallback allowed), metric-card scoreboard, calendar, reports rail, hero card, truthful rail.

## ACCEPTANCE

- Riverbed client page renders from real pipeline artifacts with zero hardcoded content values.
- SECOND CLIENT: the same page renders redhat from its artifacts with zero code changes (verify once the redhat press lands; until then, prove it with a copied artifact set under a test slug).
- A fresh client (intake only) renders the partial state cleanly.
- Press rail shows only truthful stage states; a simulated mid-press status JSON renders active/pending correctly; a failure status stops the rail on the failed stage.
- The pressed report opens with its own design untouched.
- Analyst working view: every keyword family and every NAICS/PSC code renders with its description row; boundary items render with reasons.
- Press button: page's only solid fill, two-line with sweep sublabel; approve ceremony runs (collapse, rail materialize, ease-scroll, live stage sublabel); double-fire blocked; failure returns Re-press.
- Targeting layer panel renders with its four ghost cards and next-release pill.
- Client list renders in dark tokens; live rail proven against a real job log (or stage JSON) before Monday.
- Source wall renders every registry adapter with staggered green pulses; degraded sources amber with reason; active-lane highlight works during a press.
- Asset harvest proven: riverbed seals/logo appear in calendar, ticker, header, and report cards; monogram fallback clean on a report-less client.
- CLICK-THROUGH AUDIT: an automated pass over the rendered page confirms every record identifier, calendar row, ticker item, and report card resolves to a non-empty href matching its pack source_url; zero dead identifiers.
- No pipeline files modified (git diff proves it).
- Screenshots of: full Riverbed page, partial-state page, mid-press rail, hero card. Delivered with the state doc for external review.

## RULES

- Frozen scope. No framework migrations, no React rewrite, no server redesign, no new pipeline features, no fixing unrelated things.
- Your own assessment is not verification; screenshots are reviewed externally before this is called done.
