# Command Center promotion design QA

## Comparison target

- Source visual truth: `/Users/wtjohnson/Documents/Codex/2026-08-07/files-mentioned-by-the-user-you/outputs/command-center-gauntlet/42-final-clean-worktree-1440.png`
- Implementation: `http://127.0.0.1:8322/`, launched from `/Users/wtjohnson/Desktop/LILA Control Room.app` and served by `/Users/wtjohnson/federal-sales-os`
- Implementation capture: `/Users/wtjohnson/Documents/Codex/2026-08-08/rev/work/command-center-promotion/implementation-home.png`
- Combined comparison: `/Users/wtjohnson/Documents/Codex/2026-08-08/rev/work/command-center-promotion/comparison-home.png`
- Mobile navigation capture: `/Users/wtjohnson/Documents/Codex/2026-08-08/rev/work/command-center-promotion/implementation-mobile-nav.png`
- State: Apex Analytix selected, All Federal scope, Review current, Press blocked 2/5, Assessment Review is the single next action.

## Viewport and normalization

- Source pixels: 1425 x 885.
- Desktop CSS viewport: 1425 x 885; in-app Browser capture: 1232 x 876 after the browser surface's capture scaling.
- Mobile CSS viewport: 390 x 844; in-app Browser capture: 375 x 812 after the same browser-surface scaling.
- Density normalization: the combined comparison renders source and implementation at equal display width in one browser input. Browser chrome is excluded. The accepted reference carries the older long Apex edition label; the promoted checkout correctly displays the currently registered `Research Picture` edition from live local data.

## Full-view comparison evidence

The combined comparison confirms the same white top bar and sidebar, GTM lockup, Manrope hierarchy, violet orbital hero, five-part readiness treatment, coral primary action, Capitol vignette, four-stage rail, bordered queue, and right-side readiness column. Major-region proportions, action priority, radii, status colors, and above-the-fold density remain materially identical. No P0, P1, or P2 visual drift is visible.

## Focused-region comparison evidence

No separate crop was required. At the full comparison scale, the brand lockup, context strip, hero badges, Press eligibility card, workflow rail, next-action card, gate rows, Capitol asset, and queue rows are readable. The implementation also reuses the exact committed image assets and Command Center CSS/JavaScript from the accepted build rather than approximating them.

## Required fidelity surfaces

- Fonts and typography: Manrope family, weights, hierarchy, line lengths, labels, and button text match the accepted system. The live edition name is a data difference, not typography drift.
- Spacing and layout rhythm: sidebar width, context row, hero height, stage rail, two-column work area, card gaps, radii, and shadows match. No horizontal overflow was observed at 1440 or 390 CSS pixels.
- Colors and visual tokens: violet hero, coral action, mint readiness, amber Review, quiet lavender navigation, white surfaces, and neutral borders match the accepted visual language.
- Image quality and asset fidelity: GTM branding, `account-orbits.png`, and `capitol-why-now.png` render as the real supplied assets with correct crop and sharpness. No placeholder or code-drawn substitute was introduced.
- Copy and content: workflow boundaries, next-action language, five independent gates, Targeting Review distinction, and Press-blocked language are preserved. Live counts and edition names come from the primary checkout's current data.

## Interaction and responsive evidence

- Desktop: selected Apex Analytix from the queue; opened and closed the bound-run selector; filtered Search to Apex and closed it with Escape; opened the Assessment Review studio.
- Mobile: rendered at a true 390 x 844 CSS viewport; opened the modal navigation; verified the complete destination list; closed it with Escape.
- Browser console: no errors on the desktop or mobile verification tabs.
- Automated regression: `225 passed` across Command Center, brand-mark, Target gate, UI, dashboard-view, Signal Board asset, assessment-document, and workstation-history/browser tests.

## Logo, seal, snapshot, and ticker iteration (2026-08-08)

- Visual target: the accepted Lovable-derived Command Center above, extended with the same Manrope typography, violet/coral/mint tokens, orbital identity panel, bordered evidence cards, and existing drawer behavior.
- Desktop capture: live in-app Browser at 1280 x 720 with the Apex Analytix intelligence snapshot open. The client logo is present in both the underlying account hero and snapshot identity panel; the ticker, opportunity, evidence, agency, target, and next-action hierarchy remain legible without horizontal overflow.
- Mobile capture: live in-app Browser at 390 x 844 with the same Apex snapshot open. Header, logo, 2/5 readiness, three metrics, four-row ticker, fixed actions, and scrollable content remain usable; measured document overflow was zero.
- Brand-asset contract: all 11 active Command Center clients returned HTTP 200 from the local logo registry. Every distinct agency shown in current target rows returned HTTP 200 after adding HHS, EXIM, FDIC, HUD, Senate, and Library of Congress seals. The live snapshot reported 12 loaded marks and zero missing marks.
- Snapshot interaction: selecting a client opens a compact account preview before the authoritative client workspace. The preview shows the best ranked opportunity, accepted evidence summary, agency concentration with official seals, best target route, exact next action, and the server-bound client ticker.
- Snapshot discoverability: the selected-client control in the context strip and a coral `Open intelligence snapshot` action in the hero now open the same preview directly from the Command Center home. The hero action explicitly promises `Best results + client ticker`; users no longer need to discover the Clients workspace first.
- Ticker relevance: ticker rows are filtered by exact client slug/name. Apex rendered four dated client-bound signals; Recorded Future correctly rendered the explicit empty state rather than generic news.
- Responsive/interaction checks: client-card selection, dialog open/close, source links, stay-in-Command-Center action, and full-workspace handoff were exercised in the live browser. No broken images, missing-mark fallbacks, or horizontal overflow were observed.
- Automated regression: `.venv/bin/python -m pytest` completed `181 passed` for Command Center, brand marks, Target gate, UI, and workstation browser coverage, plus `44 passed` for dashboard views, Signal Board assets, and assessment documents (`225 passed` total).

## Comparison history

1. The earlier accepted build corrected optional-looking targeting, aggregate-readiness implications, release-gate bypasses, first-Press deadlock, lane-gap ownership, and mobile focus escape without abandoning the Lovable visual system.
2. Promotion into the newer primary checkout produced only one textual conflict, in `.gitignore`, which was reconciled additively. A later browser test found a semantic integration conflict: the older dark client-view routing had displaced the newer purpose-built workstation modes. The promotion now restores dormant, held, early, and full workstation routing while keeping the Lovable Command Center as the home composition layer.
3. The first desktop-icon launch reached the temporary aesthetic-gallery server already occupying port 8322. That exact temporary process was stopped, the desktop launcher started the primary checkout, and the server working directory, HTTP response, rendered surface, and interactions were reverified.
4. Post-fix workstation coverage is `52 passed`; combined promotion coverage is `225 passed`. A fresh live desktop render and side-by-side comparison found no actionable visual mismatch in the Command Center after the route correction.
5. The identity/snapshot iteration replaced initials-only client tiles with real cached marks, added official agency-seal rendering, and introduced the client intelligence preview plus relevance-gated ticker. Desktop and mobile captures preserve the accepted visual system, and all current rendered identities resolve without a missing asset.

## Findings

No actionable P0, P1, or P2 findings remain. The live Apex edition label differs from the historical reference because the main checkout's registered data differs; this is expected and preserves the binding contract. Missing future marks fail visibly with an asset-required badge, while new-client intake now attempts the local logo import before the client reaches the board.

## Follow-up polish

None required for promotion. Future visual changes should continue to use the accepted Lovable-derived screenshots as the champion baseline.

final result: passed

---

# LILA opportunity-card v1 design QA (2026-08-25)

## Comparison target

- Structural source: `/Users/wtjohnson/Desktop/Arista Networks/Arista_Networks_Federal_Market_Map_DEMO_DILIGENCE_READY_TARGETS_REBOUND_CLIENT_DELIVERABLE_2026-08-21.html`, opportunity section beginning with `opportunity-card`.
- Reuse proof: `/Users/wtjohnson/Desktop/JTG, inc./JTG, inc. · Federal Opportunity Pre-Assessment · 2026-08-20 · Federal Market Map · EVIDENCE PACK V2 · BEST DRAFT REVIEW.html`, which already reuses one opportunity-card vocabulary across priority and opportunity sections.
- Before capture: `/Users/wtjohnson/Desktop/JTG, inc./LILA seal audit 2026-08-25/05-jtg-opportunities-with-seals.png`.
- Implementation: `/Users/wtjohnson/Desktop/JTG, inc./JTG, inc. · LILA Golden Product · DRAFT · generated 2026-08-25 0948 MDT · evidence 2026-08-20 · CLIENT.html`.
- Implementation capture: blocked. The in-app browser's security policy rejected local-file navigation before the document loaded. No alternate browser or indirect browser command was used.
- Combined comparison: not produced because an implementation screenshot was unavailable.

## Viewport and dimensions

- Intended comparison: source and implementation at the same in-app browser viewport and the same Slot 5 scroll state.
- Actual viewport: unavailable because navigation was rejected before page evaluation.
- Responsive contract established in code: two-column shared-border ledger above 1050px, one column at or below 1050px, compact acquisition grid below 720px, and one-column non-splitting cards for print.

## Five fidelity surfaces

- Composition: source structure was ported as a three-cell status rail, authority identity row, headline and buyer, two decision cells, five acquisition cells, LILA decision intelligence, targets, source actions, and evidence drawer. Source inspection passed; rendered visual comparison is blocked.
- Typography: source sizes and hierarchy were carried into the component, including a 25px title, 9px uppercase labels, and compact procurement facts. Rendered visual comparison is blocked.
- Color and borders: the implementation uses the existing LILA Market Map tokens, flat square ledger geometry, shared 1px rules, dark posture cell, and 3px coral rail rule. Rendered visual comparison is blocked.
- Spacing and responsive behavior: the implementation uses 22px card-body padding, a 58px seal, fixed field order, and print `break-inside: avoid`. Browser measurement is blocked.
- Identity and evidence: the real JTG draft validates 50 canonical Slot 5 cards, all 50 with the same 10 regions and 15 fixed fields, exactly one official frozen agency seal, and two official-source actions. It also validates 14 compact Slot 1 owner links, 8 qualified notices, and 42 review notices. The semantic validator binds every card's title, deadline, status, fit, access, notice type, NAICS, PSC, solicitation number, published value, evidence read, route basis, next action, and official-source URL to its owning record. Pixel-level seal sizing and crop remain visually unverified.

## Iteration history

1. Replaced the generic Slot 5 opportunity panel with one `lila-opportunity-card.v1` renderer.
2. Kept Slot 1 as a compact non-owning promotion link to the canonical Slot 5 card, consistent with the locked one-owner rule. Slot 6 remains a non-owning teaming-route reference and does not render a second opportunity card.
3. Added explicit empty states so optional fields do not disappear or reorder the card.
4. Added structural and semantic HTML validation that fails on missing or reordered regions and fields, lost identity, wrong title or field content, source-to-owner mismatch, population mismatch, broken Slot 1 owner links, or a legacy priority-card renderer.
5. Pressed the frozen real-JTG product into the Desktop JTG folder and verified 50 owning cards plus 14 priority links without duplicating evidence ownership.
6. Re-pressed the same frozen product twice: CLIENT, STUDIO, and product JSON were byte-identical. CLIENT SHA-256 is `7df4ca8281bca1b8feaaf7ac77bd78852a74c9b73b5645be0d88f038b8a0a2ea`.
7. Browser security blocked the required implementation capture and combined visual comparison. The visual gate remains open until the generated client file is opened by the user in the in-app browser and captured at the reference viewport.

final result: blocked
