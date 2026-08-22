# Identity-template review · `ab72e59`

Reviewer: Codex  
Date: 2026-07-12  
Reviewed SHA: `ab72e592cc95edce96b4f25fcd2b50295046c12b`  
Parent: `c522599f053a730474ba6606f7d25b04e355946c`

## Mechanical verification

- Isolated seven-file identity diff verified mechanically.
- Pre-existing worktree changes in `data/entities/unresolved.log` and
  `docs/reviews/cycle5-followon-solo-range.md` were not touched by the commit
  review.
- Affected test set: **91 passed**.
- Full suite: **929 passed, 1 skipped** in 153.76s.
- No gate, approval, pointer, release, report press, or metered action.

## What holds

- NETSCOUT's repository SVG is marked, retains its native wide viewBox, and is
  no longer rewritten by R13 in the capture-brief renderer.
- Generated, unmarked chart SVGs remain governed by R13.
- The capture-brief cover gives NETSCOUT a deliberate white plate and removes
  the duplicate adjacent name.
- GTM and Command Center logo sizing is materially more intentional on desktop.
- No new remote-logo fallback was introduced; the primary Command Center path
  remains local.
- Identity lint and the complete suite remain green.

## Findings

### 1. P1 · Owner: QB · common valid SVG roots bypass or corrupt brand marking

`_mark_svg_as_brand` uses `re.match(r"<svg...")`, so a valid dropped SVG with
an XML declaration or leading comment is returned unchanged. R13 then rewrites
its native viewBox, reproducing the original defect for a supported brand
asset. Root attributes are parsed only with double quotes; a valid
single-quoted `class`/`viewBox` is misclassified and can receive a duplicate
`class` attribute.

Required repair: locate/parse the root SVG after optional XML declaration,
doctype, comments, and whitespace; support both quote styles; merge attributes
without duplication. Add XML-prolog and single-quoted wide-SVG regressions
that prove the marker is present and the native viewBox is unchanged.

### 2. P1 · Owner: QB · other client-report renderers retain the broken lockup

`_client_mark` is shared by the capture brief, three-view assessment, and
Target report, but only `render_capture_brief` received `client-plate` and
duplicate-name logic. `agents/reports/views.py` and
`agents/reports/target_report.py` still render the now-larger dark NETSCOUT
wordmark directly on the navy cover and append a redundant client-name
wordmark. The identity change therefore fixes one Federal Opportunity
Assessment path while leaving another client-facing assessment path visibly
wrong.

Required repair: one shared cover-lockup helper consumed by all report
renderers, with the same plate/name rules and tests for capture brief,
three-view client assessment, and Target report.

### 3. P2 · Owner: QB · file existence is not equivalent to a full wordmark

`_client_logo_present` tests path existence, then suppresses the adjacent name
for every real file. Recorded Future's committed 120x120 mark is an icon, not
a wordmark, so the company name disappears. An empty SVG is worse:
`_client_mark` falls back to the monogram while `_client_logo_present` remains
true, so the fallback name is also suppressed. Decide lockup behavior from the
successfully rendered mark kind/aspect, not a second filesystem lookup.

### 4. P2 · Owner: QB · square raster marks are forced into the wide treatment

Every raster mark is wrapped as 160x48 and tagged `client-brand-wide` without
reading its native dimensions. The repository already contains a square
Osprey client raster, which will be letterboxed at the left of a wide plate.
Read raster dimensions or use an aspect-neutral wrapper and add a real
square-raster regression.

### 5. P2 · Owner: QB · the R13 exemption is a substring check

`_is_brand_asset` exempts any opening tag containing the string
`data-brand-asset`, including `aria-label="data-brand-asset"` or
`data-brand-asset="0"`. Require the exact root attribute/value
`data-brand-asset="1"` (supporting normal quote variants), so generated charts
cannot be exempted accidentally.

### 6. P1 · Owner: QB · Command Center client header is not responsive

`.chead` remains a single unwrapped flex row. The new logo may consume 210px,
followed by the back button, 34px client heading, and INTERNAL/SALES controls.
The narrow breakpoint does not change this row, so phone widths push content
off-screen. Add a narrow grid/wrap treatment that moves view controls and
constrains the wordmark, then verify computed layout at a phone viewport.

### 7. P2 · Owner: QB · top-bar enlargement lacks a narrow treatment

The fixed 52px, non-wrapping top bar now contains a 34px padded GTM mark,
heavily tracked product title, build stamp, and New Client button. No mobile
override changes that composition. Provide a narrow hierarchy rather than
letting the enlarged identity become cramped or overflow.

### 8. P2 · Owner: QB · responsive tests are source-string assertions

The Command Center tests prove class names exist but never render a wide mark,
square/monogram, desktop viewport, or phone viewport. They cannot catch the two
responsive defects above. Add browser/computed-layout assertions for both mark
shapes at both viewport sizes.

## Verdict

**HOLD `ab72e59` for repair.** The intended desktop capture-brief and Command
Center direction is correct, but the asset parser, shape classification,
cross-renderer consistency, and narrow layouts are not yet robust enough for a
reusable professional identity template.

---

## Resolution addendum (QB, 2026-07-12) — all eight findings repaired

Each verified against the code before fixing; repaired in one isolated diff on
top of `ab72e59`. Suite 936 passed / 1 skipped. New regressions in
tests/test_identity_template.py (19 total).

Core refactor (unifies findings 2, 3, 4): one resolver `_resolve_client_mark`
returns `(html, kind)` where kind is decided from the SUCCESSFULLY RENDERED
mark (`wordmark` / `square` / `monogram`), never a second filesystem probe. One
shared `client_cover_lockup` consumes it and is used by the capture brief,
three-view assessment, AND Target report.

- F1 (P1) robust parse: `_mark_svg_as_brand` locates the root <svg> after an
  optional XML prolog / doctype / comment / whitespace and parses attributes in
  BOTH quote styles; class/role/aria-label are merged, never duplicated.
  viewBox preserved byte-for-byte. Regression: prolog + single-quoted wide SVG.
- F2 (P1) cross-renderer: views.py and target_report.py now render
  `client_cover_lockup`; the inline `{mark}<div class="rf-wordmark">` lockups
  are retired. Regression: three-view wordmark client gets the plate, no
  duplicate name, native viewBox intact.
- F3 (P2) kind, not existence: a square icon (Recorded Future) and the empty-
  SVG fallback both keep the visible client name; only a true wide wordmark
  drops it. Regression: empty SVG -> monogram + name.
- F4 (P2) square rasters: `_raster_client_mark` reads native PIL dimensions and
  wraps to native aspect; the committed square Osprey raster resolves `square`
  with a 128x128 viewBox, not a 160x48 letterbox. Regression added.
- F5 (P2) exact marker: `_is_brand_asset` requires `data-brand-asset="1"`
  (either quote); `="0"` and an aria-label mention no longer exempt a chart.
- F6/F7 (P1/P2) responsive: a `@media (max-width:720px)` block wraps the top
  bar and the `.chead` client header and constrains the wordmark. Verified in
  a 375px viewport: top bar and chead wrap, `.chead` fits, header logo 150px.
- F8 (P2) tests: added shape/kind, cross-renderer, robust-parse, exact-marker,
  and narrow-rule regressions, plus live computed-layout verification at
  desktop and phone viewports.

One honest scope note: at 375px the PAGE still overflows-x, but the source is
the pre-existing `.vb-tape` news ticker (9220px scrolling marquee), NOT any
identity element. The top bar, client header, and logo slots all fit. The
ticker clipping is a separate pre-existing responsive item, deliberately left
outside this isolated identity-template diff.
