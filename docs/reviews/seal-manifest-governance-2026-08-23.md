# Insignary seal-manifest governance decision

Decision date: 2026-08-23

Operator instruction: resolve the remaining seal-manifest governance hold.

## Finding

The frozen `agency_seals_embedded` expectation counted every embedded PNG,
not only agency seals. The current deterministic Insignary replay contains
13 embedded PNG occurrences:

- seven official agency-mark placements;
- five Insignary logo placements; and
- one GTM logo placement.

The seven agency-mark placements, in render order, are:

1. `agency:doc` in the federal-organization rail;
2. `agency:dhs` in the federal-organization rail;
3. `agency:va` in the federal-organization rail;
4. `agency:dod` in the federal-organization rail;
5. `agency:doc` on an opportunity card;
6. `agency:dhs` on an opportunity card; and
7. `agency:va` on an opportunity card.

## Ruling

Accept the seven typed agency-mark placements. Replace the ambiguous count
with two independent frozen expectations:

- `embedded_png_images: 13` protects the complete embedded-image surface;
- `agency_seal_keys` protects the identity, count, and order of official
  agency-mark placements.

The test recognizes an agency seal only when the rendered element declares
`data-brand-kind="agency"` and carries a `data-brand-key`. Client and GTM
logos no longer alter the agency-seal count.

## Boundary

This decision does not change the renderer, seal resolver, evidence, source
data, or release gates. A future image-count change or agency-key change still
fails the golden manifest and requires an explicit review.

## Verification

```text
LILA_SUITE_OFFLINE=1 python -m pytest tests/test_insignary_golden_manifest.py -q
LILA_SUITE_STRICT=1 LILA_SUITE_OFFLINE=1 python -m pytest tests/ -q
```
