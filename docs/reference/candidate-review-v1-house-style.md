# LILA Candidate Review v1 house style

Recorded: 2026-07-22

## Decision

Candidate Review v1 keeps the locked white LILA report canvas and adopts a
more proprietary intelligence-product surface. The July 22 GovTribe and
Demandbase screenshots are visual references for typography character,
information density, and panel construction only. Their dark shells, colors,
navigation, product language, and branded components are not copied.

This file controls Chunk 5 renderer styling. It does not change the analytical
contracts, evidence rules, section order, ticker, scoreboard, editor, or
download behavior.

## Frozen visual references

| Reference | SHA-256 | Useful characteristic |
|---|---|---|
| `/Users/wtjohnson/Desktop/Screenshot 2026-07-22 at 1.27.24 PM.png` | `f1bca95b85fd0c98fcbff97f89c664d5ebe6f79865d77e22944babf877bd44e1` | Milestone rail, fact matrix, compact tabs, label/value hierarchy |
| `/Users/wtjohnson/Desktop/Screenshot 2026-07-22 at 1.26.35 PM.png` | `95cdec618047001dd1e037c9a995dc623f8400d54995d3ecde2aa599740d46b7` | Strong display type, feature-card hierarchy, dense linked record rows |
| `/Users/wtjohnson/Desktop/Screenshot 2026-07-22 at 1.19.54 PM.png` | `e37c94f8bd808a21a78e58713364ca819e688b4ef0a3079eb7949c98980ab644` | Master-detail grouping, high-x-height type, compact status treatment |
| `/Users/wtjohnson/Desktop/Screenshot 2026-07-22 at 1.27.37 PM.png` | `58eb7daccc633774254077773adf341a56831f61db43187f207dfc89aee2dbb2` | White nested panels, thin rules, integrated tabs, dense two-column information |

The screenshots do not establish exact font identity. LILA uses its own font
pairing based on their readable, confident characteristics.

## Locked foundation

- White report paper on a light-gray browser canvas.
- Existing LILA navy, orange, blue, and green palette.
- Two-column hero, report ticker, scoreboard/KPI context, agency seals, client
  and partner marks, and the exact eight-section order.
- Evidence-first hierarchy and the separation between records, inference, and
  analyst validation.
- Editable and resizable text and images, seal/logo drag-and-drop, card
  reorder/delete/undo, working links, standalone HTML, and print/PDF.

## Typography

Use an embedded, self-contained LILA font package rather than a CDN or a
generic browser default.

| Role | Family | Weight | Size/leading |
|---|---|---:|---|
| Display and section headings | Manrope LILA | 700–800 | 30–44px / 1.08–1.15 |
| Card and opportunity headings | Manrope LILA | 700 | 18–22px / 1.20–1.28 |
| Body and analytical copy | Source Sans 3 LILA | 400–500 | 15–16px / 1.50–1.60 |
| Metadata values and tabs | Source Sans 3 LILA | 600–700 | 13–15px / 1.25–1.40 |
| Labels and eyebrows | Source Sans 3 LILA | 600 | 11–12px / 1.25 |

Typography rules:

- Use tabular numerals for dates, counts, money, and scoreboard values.
- Keep body measure near 72–78 characters.
- Use sentence case by default. Uppercase is reserved for short metadata
  labels, with no more than `0.05em` tracking.
- Display headings may use `-0.02em` tracking; body copy does not.
- Preserve single-line headline enforcement and per-element text resizing.
- Bundle the required WOFF2 files into the standalone artifact after license
  verification. If fonts fail, fall back to `Avenir Next`, `Segoe UI`, and
  `sans-serif` in that order.
- Do not use Inter as the primary house face; the goal is a distinct LILA
  pairing rather than another generic application default.

## Color and surface tokens

```text
--lila-canvas:       #eef2f5
--lila-paper:        #ffffff
--lila-panel:        #f5f7fa
--lila-panel-strong: #edf2f6
--lila-ink:          #0b1728
--lila-text:         #26374a
--lila-muted:        #667789
--lila-rule:         #d9e1e8
--lila-blue:         #157eaf
--lila-green:        #13735b
--lila-orange:       #fb461f
```

- White remains the dominant surface.
- Pale neutral panels create grouping without dark application chrome.
- Existing LILA blue marks links and active states; orange marks priority or
  section emphasis; green marks verified/validated states.
- Meaning is always written in text and never carried by color alone.
- Do not import GovTribe cyan, Demandbase purple, gradients, or dark full-page
  shells.

## Box and panel grammar

Use three repeatable panel types instead of rendering every block as the same
rounded card.

### 1. Record frame

- White background, `1px` solid `--lila-rule`.
- `8px` outer radius.
- No default shadow; a primary feature frame may use
  no more than `0 1px 2px rgba(11, 23, 40, 0.06)`.
- Optional `3px` LILA-orange or LILA-blue top rule.
- `20–24px` padding.
- Contains the opportunity title, identifier/type deck, milestone rail, fact
  matrix, and evidence tabs.

### 2. Fact matrix

- Two columns on desktop and one column on narrow screens.
- Label above value.
- Hairline row and column separators.
- No separate rounded box around every field.
- Editable seal/logo slot may sit beside the agency value without changing row
  height.

### 3. Signal list

- One shared outer frame or no frame.
- Individual items are separated by `1px` rules, not floating cards.
- Each row may contain an editable mark, headline, short context, source link,
  and one restrained status/count chip.
- Pills are limited to true statuses, counts, or compact classifications.
- Do not render a chip for a state that is not explicitly present in the
  evidence.

Supporting primitives:

- **Milestone rail:** three to four equal cells for posted, updated, due, award,
  or research-clock dates, with one accent rule beneath.
- **Evidence tabs:** `Records show`, `What it may suggest`, and `Validate next`;
  the active tab uses an underline, not a filled vendor tab.
- **Summary well:** pale-neutral inset panel with report as-of and evidence
  metadata beneath it.
- **Calendar row:** date block, event or milestone title, status text, source
  link, and optional agency seal; no invented date precision.

## Density and spacing

- Use a `4 / 8 / 12 / 16 / 24 / 32 / 48` spacing scale.
- Default card padding: `20px`; hero/feature panel: `24px`.
- Default section gap: `32–40px`.
- Default row height should be compact but never force body text below 14px.
- Use scale, dividers, and whitespace for hierarchy before adding another box.

## Section application

1. **360° assessment:** editorial thesis panel plus a compact fact matrix.
2. **Candidate opportunities for review:** record frames with milestone rails,
   decision facts, exact links, and evidence tabs.
3. **Vehicle, partner, and market signals:** signal lists and small nested
   evidence panels.
4. **Past awards and competitive analysis:** dense linked rows with hairline
   rules; avoid repeated full cards.
5. **Preliminary keywords and capability search concepts:** compact grouped
   terms with restrained chips only where classification is meaningful.
6. **Federal opportunity calendar:** chronological calendar rows with typed
   status and precision.
7. **Assess | Target | Execute:** three disciplined panels with unequal content
   allowed; do not create filler for visual symmetry.
8. **Evidence dock:** dense table/list treatment optimized for link scanning.

## Explicit exclusions

- No dark GovTribe-style report shell.
- No Demandbase-style gradients or sales-prediction language.
- No vendor side navigation, trial banners, carousel dots, branded icons, or
  copied module names such as `AI Summary` or `Recommended For You`.
- No automated qualified/unqualified, predicted award, pursue, no-bid,
  disqualification, pipeline value, or win-probability treatment.
- No placeholder milestones such as `No Award Date`; omit the cell when the
  evidence does not establish a date.
- No repeated 16px-radius cards, excessive pills, floating shadows, or generic
  three-column symmetry that makes the report feel template-generated.
- No external font, icon, or stylesheet request in downloaded HTML.

## Chunk 5 acceptance checks

- Golden screenshots for Mark43, iMerit, and Riverbed use the same house tokens
  and preserve client-specific marks and content density.
- Font loading, fallback, wrapping, and single-line headlines pass at desktop,
  tablet, mobile, and print widths.
- Ticker, scoreboard, navigation, links, text resizing, image resizing,
  drag/drop, reorder, deletion, undo, save, reset, download, and print behavior
  remain unchanged.
- Edit mode keeps links clickable, reserves reordering for a dedicated handle,
  and never clips resize handles with panel overflow.
- Clean downloadable HTML embeds fonts and assets and contains no vendor or
  generator branding, comments, metadata, or variable names.
- A blind human review evaluates whether the report reads as a proprietary
  LILA intelligence product rather than a generic generated artifact.
