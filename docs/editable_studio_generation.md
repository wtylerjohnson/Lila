# Editable Studio generation contract

## Outcome

The operator still presses once:

```bash
python run_candidate_review.py --client "<CLIENT>" --with-watch --live
```

That one command now ends in a self-contained Editable Studio HTML and a
machine-readable Studio receipt. The browser editor is a delivery capability,
not a content-generation stage.

## What the Riverbed reverse engineering established

The approved report came from two ancestors:

- The July 21 artifact supplied the visual grammar: navy/orange/blue, white
  report paper, sticky utility bar, hero, ticker, headline metrics, and numbered
  reading order.
- The July 30 artifact supplied the evidence corpus, source links, edit IDs,
  seal/logo slots, and the original text/logo/download editor.

The later Riverbed files were useful design reviews, but their ordered DOM
mutation scripts are not a production architecture. Their durable decisions
are now represented as renderer code, doctrine configuration, local assets,
and testable editor behavior.

## The shortest reliable pipeline

```text
approved client boundary
  -> canonical evidence pack
  -> deterministic analytics and Ed-doctrine view model
  -> bounded prose slots
  -> deterministic HTML render
  -> Editable Studio compiler
  -> evidence validation + Studio receipt + browser reopen test
```

This is one operator step and six internal stages. Combining the internal
stages further would blur responsibilities and make failures harder to name.

### 1. Canonical evidence pack

Awards, notices, forecasts, contacts, events, source URLs, dates, values,
recipients, vehicles, scope receipts, and search receipts remain structured
data. HTML is never used as the database.

### 2. Derived analytics

Code calculates buyer totals, top buying accounts, prime-recipient totals,
competitor totals, acquisition clocks, forecast floors, and campaign groups.
It also assigns the limited account motions used by the executive map. Models
never calculate a displayed number.

### 3. Doctrine configuration

`agents/golden_press/doctrine.py` owns the plain-speak federal-sales sequence
and the reader question for every load-bearing section. The default prose
grammar is:

1. what the evidence says;
2. why it matters;
3. what to verify or do next; and
4. the external source.

Competition and the prime ecosystem are visually and semantically separate.
Prime recipients are described as awardees on linked records, never as
invented partners.

### 4. Bounded model role

Fable 5 is the analyst/writer, not the renderer. It may write short prose into
named slots after receiving the exact facts for that slot. It may not:

- write or repair the full HTML;
- invent or recompute figures;
- choose source URLs;
- select seals or logos;
- reorder evidence records; or
- turn a forecast into a bid-ready opportunity.

If the prose call fails, deterministic factual fallback copy is used and the
report still renders. Codex owns the renderer, compiler, regression tests, and
future component migrations.

### 5. Deterministic report renderer

`agents/golden_press/render.py` renders evidence-bearing DOM once. It now
includes:

- a source-linked top-buying-account action map and complete account ledger;
- live notices before award-period research clocks;
- agency forecast records and the documents to request;
- a distinct competitive landscape and prime-awardee ecosystem;
- keyword-first, NAICS-second search boundaries;
- contacts, calendar, event, coverage, and evidence-ledger sections; and
- stable edit, logo/seal, unit, and source-link contracts.

### 6. Editable Studio compiler

`agents/golden_press/studio.py` runs after the complete report is rendered. It
adds only the generic Studio stylesheet, runtime, and state envelope. Before it
writes anything, it inventories the source. After compilation it proves that
the exact multiset of hrefs, image sources, edit IDs, logo/seal slot IDs, and
evidence rows is unchanged.

Every generated artifact receives a report identity bound to the client,
evidence-pack date, and evidence-pack hash. That prevents browser drafts from
bleeding across reruns.

### 7. Integrity behavior

The root document carries:

- `data-studio-source-sha256`: the exact pre-Studio report source;
- `data-studio-integrity="pristine"`: no analyst changes; and
- `data-studio-integrity="analyst-edited-unverified"`: any text, layout,
  link, or image change has occurred.

Embedded state must match the report ID and source SHA. Bad or mismatched state
sets `data-studio-boot="error"`; it is not silently ignored.

## Output set

For `<slug>.golden_report`, the press writes:

- `<slug>.golden_report.html` — the self-contained Editable Studio;
- `<slug>.golden_report.evidence_pack.json` — canonical evidence;
- `<slug>.golden_report.prose.json` — hash-bound bounded prose;
- `<slug>.golden_report.validation.json` — evidence/figure/link verdict; and
- `<slug>.golden_report.studio.json` — Studio preservation receipt.

## Required tests

Static tests fail on duplicate edit/logo IDs, missing editor actions, a second
Studio injection, any changed proof/link/image/edit/slot inventory, or an
invalid press.

The real-Chrome regression opens the generated file, downloads an untouched
copy, reopens it in a fresh browser context, adds a section and content, embeds
a seal, removes a section, downloads again, and confirms that all changes and
all original evidence survive. It also verifies that the edited copy no longer
claims pristine integrity.

## Replay without research or a model

`rerender_golden_press()` consumes the sealed evidence-pack and prose sidecars.
It performs no retrieval, live verification, or model call. Two replays from
the same sidecars must be byte-identical and their output SHA must match the
Studio receipt.

## Change policy

New visuals must be implemented as deterministic components over canonical
records, then covered by both semantic and browser tests. Do not add another
late script that rewrites a client HTML file. A client-specific override may
provide brands, terms, or editorial judgment, but it may not bypass the shared
evidence, renderer, Studio, or QA contracts.
