# LILA GOLDEN STANDARD BUILD

One prompt, frozen scope. Read fully before acting. Do not add, refactor,
or improve anything not named here.

## ONE GOAL

By end of day: Tyler redoes intake for "Riverbed" in the Command Center,
clicks approve at the single gate, and the pipeline produces a report
matching the hand-made golden reference: same 18-record evidence core
surfaced through the same research logic, same section grammar, same
inherited design (seals, logos, formatting), same data density. UX/UI
polish is OUT of scope. The report must run through and be served by the
Command Center; it does not need to look integrated.

## FIRST ACTION, BEFORE ANYTHING ELSE

Save this entire prompt to docs/plans/GOLDEN_BUILD.md. At the END of every
phase, write docs/plans/GOLDEN_BUILD_STATE.md: phase completed, files
touched, next action. Unconditional, so any fresh session resumes from
disk with zero loss.

## WHY THE PIPELINE FAILS (established by artifact diff; do not re-litigate)

1. Retrieval searches only capability keywords against notices. The golden
   report's density came from entity-name searches (client name, product
   names, competitor names) on USASpending award descriptions, plus DHS
   APFS forecasts. Those lanes do not exist in the pipeline.
2. The composer fills deterministic templates, producing boilerplate and
   identical prose across records. The golden report is a frontier model
   writing directly from full evidence context.

The fix is to reproduce the manual frontier-model process: exhaustive
retrieval, full-context composition into the golden design, a critic
loop, mechanical validation.

## GOLDEN REFERENCE

The hand-made file Riverbed_Federal_Opportunity_Assessment_CANDIDATE_
REVIEW_EDITABLE_2026-07-22 (Tyler confirms path). Copy it to
fixtures/golden/riverbed_golden.html. It serves three roles: recall
benchmark, format contract, and design source. Its embedded design is
inherited verbatim, never regenerated. Existing editable logo/seal slots
remain; Tyler places client assets manually today.

## TIMEBOXES AND CUT ORDER

Phase 0: 30m. Phase 1: 1h. Phase 2: 2h. Phase 3: 3h. Phase 4: 1h.
If a phase exceeds its box: write the state file, report the overrun and
cause, then apply cuts in this order: (1) drop lane L4 forecasts,
(2) critic loops 3 to 1, (3) Phase 4 becomes press-writes-file plus CC
link only. NEVER cut: the L2 entity-award lane, the full-context
composer, the mechanical validator. Those are the standard.

## PHASE 0 - PREFLIGHT

a. Locate the composer's current LLM call(s). Report model string,
   max_tokens, temperature, and whether output is JSON-schema-constrained.
   If it is a non-frontier model or token-capped: FIX NOW, in this phase.
b. Verify API access per lane and apply this table:
   - USASpending unreachable OR Anthropic API dead: HARD STOP, report.
   - SAM.gov key missing: proceed, L1 degraded. The golden report contains
     zero SAM notices; L1 cannot block golden density.
   - APFS blocked: proceed, L4 degraded; sufficiency reachable via L1-L3.
     The recall bar adjusts per DEFINITION OF DONE.
   - OpenAI dead: proceed; critic loop replaced by one Anthropic
     self-critique pass with the same output contract.
c. Confirm golden reference copied to fixtures/golden/.

STOP. Report Phase 0 findings in one block, then continue per the table.

## PHASE 1 - RESEARCH STAGE EMITS ENTITIES

The company research stage (web search, site ingest, reasoning) must
output, alongside keywords/NAICS/PSC: client product names, competitor
names, and known reseller/channel partners. These feed retrieval.
Tyler is redoing Riverbed intake fresh, so this runs live, no cached
shortcuts. Expected on Riverbed if research works: products including
Aternity, SteelHead, NetProfiler, AppResponse, SteelCentral; competitors
including SolarWinds, Gigamon, Dynatrace. Do NOT hardcode these names
anywhere in code or prompts; if live research cannot surface them, report
the gap. The raw research output is a Checkpoint 1 deliverable.

## PHASE 2 - RETRIEVAL LANES, DEDUP, SUFFICIENCY

Keep the existing notice lane. Build:

- L1 notices: SAM.gov capability-keyword search (exists today).
- L2 entity awards: USASpending award-description search on client name,
  product names, competitor names. Capture full fields per record:
  recipient, seller/sub, agency, obligated dollars, period start/end,
  vehicle/parent IDV, description, award ID, source URL.
  Relevance filter, REQUIRED: "riverbed" is a common noun in federal
  contracting (dredging, streambank stabilization, environmental work).
  Every entity search must be paired with a relevance screen: tech-relevant
  NAICS/PSC on the award, or recipient-type screening, or capability-term
  co-occurrence in the description. Records with no capability relevance
  are excluded from the evidence pack and NEVER count toward sufficiency.
- L3 expiring clocks: L2 results flagged where award period end falls
  within 18 months (the "research clocks").
- L4 forecasts: DHS APFS records matching keywords and NAICS.

Dedup: unique on contract number / notice ID across all lanes; a record
appears once, in its best-fit section.

Integrity rules: retrieval queries use generic entity and keyword terms
only. The golden report's record IDs must NEVER appear in any query or
fetch; they are used only after retrieval, to score recall.

Sufficiency gate: 15+ unique relevant records across 3+ lanes. If below,
escalate: broaden entity terms (resellers, adjacent products), re-score.
If still below after escalation: proceed anyway with a machine-readable
scarcity note for the composer. Never pad, never duplicate.

CHECKPOINT 1 - STOP. Deliver in one block: the deduped record list (ID,
title, agency, dollars, lane), the raw API queries per lane, the raw
research-stage entity output, and recall vs the golden dock's 18 IDs with
an explanation for any miss. Tyler verifies externally before Phase 3.

## PHASE 3 - COMPOSER REPLACEMENT

Unwire the templated composition path from the golden flow (leave code in
place, just disconnected).

a. Split: code splits fixtures/golden/riverbed_golden.html ONCE into
   skeleton (doctype, head, CSS, JS, edit chrome, kept verbatim) and
   content region (report sections 01-08). The skeleton is reused as-is;
   this is how seals, logos, and formatting survive untouched.
b. Evidence pack: one JSON containing every retrieved record with all
   fields, the research-stage output, and any scarcity note.
c. Compose: ONE call, frontier Anthropic model, maximum output budget, no
   JSON schema constraint. Input: evidence pack, the golden CONTENT REGION
   as format contract, and instructions to write the complete content
   region with every section populated from evidence: exec summary with
   computed totals, corridor clustering where records support it,
   per-record "why it surfaced" citing that record's own dollars, seller,
   vehicle, and dates, keyword families in product language, evidence dock
   listing every record exactly once. SHOW-YOUR-WORK LAW (2026-07-25, same
   standing as the linkage law): every headline and band aggregate is
   composed WITH its justification, natively, never retrofitted. Each
   aggregate carries (a) a QUALIFIER CAPTION directly beneath it stating
   what it counts and what it is not ("Obligated across N cited active
   award corridors · not pipeline revenue"; forecasts: "published lower
   bound · not a total"), (b) a VISIBLE toggle reading "Show the work +" /
   "Hide the work −", never a bare invisible disclosure, and (c) an
   expanded panel titled "How the total was built" (SUM), "How this was
   identified" (superlative), or "How the floor was built" (forecast lower
   bound). SUM panels carry one row per component: leading "+", a linked
   label in the pattern AGENCY × CLIENT via SELLER anchored to that
   record's pack source_url, then the value at display precision, closing
   with a terminal "=" row reading "Exact total" and the figure, matching
   the headline character for character. SUPERLATIVE panels carry the
   winning record row, then the full qualifying set with every ID
   anchored, and the set count must equal the N stated in the caption; if
   it does not reproduce, restate the caption to the count that does and
   NEVER change the dollar figure. FORECAST panels carry one row per
   forecast with its published band and the summed lower bound, labeled a
   floor, not a total. HARD RULE: any figure that does not reconcile to
   the penny gets no panel and is reported unreconciled; never fudge a
   component, never adjust a stated figure. LINKAGE LAW: every record
   identifier and record title, everywhere it appears, renders as a live
   hyperlink to its canonical primary-source URL (USASpending award page,
   SAM.gov notice, APFS record). Reuse the existing canonical URL
   builders from the link integrity gate; the composer never
   hand-constructs URLs, it uses the source_url field carried on each
   pack record. ONE SANCTIONED ADDITION to the golden section grammar:
   a "Federal opportunity calendar" section, inserted after the
   Assess | Target | Execute section and before the Evidence dock.
   Content: every dated item in the evidence pack, chronological
   ascending, one row each: date, type tag (response deadline / award
   clock / on-ramp close / forecast window), record title, agency, and
   the record's source link per the linkage law. Styled in the golden
   design language (same type, navy, ochre tags). This is the "we have
   an eye on these dates for you" section. Nothing else about the
   golden grammar changes. Output must end with the sentinel
   <!-- LILA:COMPLETE -->.
d. Critique: OpenAI call. Input: draft, golden content region, and a
   rubric covering records used vs evidence pack, per-record specificity,
   repeated-prose detection, section completeness vs golden grammar (the
   Federal opportunity calendar is a sanctioned addition, not a
   deviation; flag it only if missing or if any dated pack record is
   absent from it), and
   hollow sections. Output contract: exactly "PASS" or a numbered fix
   list, each item naming the section, the specific deficiency, and which
   evidence-pack data fixes it. No other prose.
e. Revise: draft plus fix list back to Anthropic. Max 3 loops or PASS.
f. Splice and validate (code, not LLM): splice content into skeleton,
   then check:
   - sentinel present; document parses as well-formed HTML;
   - every dollar figure in the output either exists verbatim in a pack
     record OR is verifiable as an arithmetic aggregate of pack values.
     The validator computes lane and cluster sums from the pack and checks
     stated totals against them. Computed totals (exec summary sums,
     corridor sums, forecast lower bounds) are correct behavior, not
     violations;
   - every date and contract number in the output exists in the evidence
     pack;
   - no repeated prose strings across records;
   - every evidence-pack record appears exactly once in the dock;
   - LINK CHECK: every record identifier in the output is wrapped in an
     anchor whose href exactly matches that record's source_url from the
     pack; a record rendered without its link, or with a URL that does
     not match the canonical builder for its source system, is a
     violation;
   - AGGREGATE JUSTIFICATION (mandatory, same standing as LINK CHECK):
     every headline or band aggregate carries a qualifier caption, a
     visible Show-the-work toggle, and a titled panel whose components
     are anchored to pack source_urls and whose terminal figure restates
     the headline character for character. A missing caption, missing
     toggle, untitled panel, unanchored components, or a terminal that
     does not reconcile to the penny is a violation;
   - minimum lane representation.
   On failure: one revision attempt with violations listed; second failure
   means the press FAILS LOUDLY. No silent ship.

## PHASE 4 - COMMAND CENTER WIRING

Default and sufficient: the press writes the report HTML file and the
Command Center exposes it via link or iframe after the approve step. Any
deeper integration is tomorrow's work. No server redesign.

CHECKPOINT 2 - STOP. Deliver: the generated report file, critique
transcript per loop, validator output, and the recall score. Tyler diffs
externally against the golden reference.

## RULES

- Frozen scope. No refactors beyond files these phases require. No new
  features, no design improvements, no fixing unrelated things noticed
  along the way.
- The 5 open test failures: triage only if they touch these files.
- Any blocker (keys, rate limits, dead endpoints): report precisely, stop
  that lane per the Phase 0 table, continue the others. Never work around
  a blocker silently.
- Your own assessment of quality is not verification. Checkpoints 1 and 2
  are verified outside this session. Nothing is done until the external
  diff says so.

## DEFINITION OF DONE

Fresh Riverbed intake through the Command Center produces a report with:

- All live lanes represented, golden section grammar 01-08, design
  inherited verbatim from the skeleton, record-specific prose with zero
  repeated strings, every record identifier click-resolving to its
  primary source, a Federal opportunity calendar carrying every dated
  pack record, EVERY headline and band aggregate carrying its qualifier
  caption, visible Show-the-work toggle, and penny-reconciling panel
  (show-your-work law), validator clean.
- Recall bar: with all four lanes live, 16+ of the 18 golden record IDs,
  misses explained by the raw queries and live data. If L4 is degraded or
  cut per the Phase 0 table or the cut order, the bar is 14 of the 14
  non-forecast golden records, and L4 becomes tomorrow's first task.
- Deltas attributable to live federal data as of July 24 (expired awards,
  new records) are correct behavior, not failure.
