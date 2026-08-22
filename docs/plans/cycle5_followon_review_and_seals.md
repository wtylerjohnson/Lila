# Cycle 5 follow-on · Codex review of solo work + seal-treatment collaboration

Base (Codex's last verified sync): `8fb4c50` (Cycle 5 Tranches 2-3 merge).
Review range: `8fb4c50..0796a74` on `rename/opportunity-assessment`.
Suite at HEAD: 910 passed / 1 skipped.

Standing hard rules (unchanged): no pointer activation/removal, no gate or
approval edits, no report press or metered call, verify SHAs and lane diffs
mechanically before trusting any claim, isolated diffs, report back in the
Tranche handoff format.

## Part 1 · Independent review of the solo range (priority order)

Eleven commits landed without Codex review. Adversarially verify each; route
findings to docs/reviews/ with owner + severity. Priority by risk:

1. `88e6e46` [CONTRACT SURFACE] scope containment + `01fc857` FOCUS projection
   at joins. The DHS-in-DoD leak fix. Verify: the affirmatively-foreign
   predicate (agency_is_foreign) never blocks a legitimate in-scope component
   (Secret Service/FLETC/TSA are unlisted DHS components), the fail-closed
   fact + document gates, the live-lane passthrough for the parity packet, and
   the agencies-table additions (IRS/WHS/FAS + DHS components) introduce no
   abbr collisions or mis-parents. Adversarial fixtures should inject foreign
   agencies AND unlisted in-scope components in one artifact.
2. `7439ab5` arbiter-key paste. CREDENTIAL HANDLING — review hardest. Verify:
   the key never persists to disk, never logs beyond last-4, never returns in
   any response (status is masked), press subprocesses inherit it only via
   os.environ, and activation makes the gate STRICTER (Anthropic consensus
   already holds without it), never weaker. Confirm no path echoes the key
   into a job log.
3. `333fcbf` [CONTRACT SURFACE] pointer-refresh compare-and-swap. This is the
   QB-lane fix of Codex's own Cycle 5 P1 (the rollback race). Verify the CAS
   actually closes the concurrent-removal AND concurrent-replacement windows
   your regression described, and that activation (the only creation path)
   still works.
4. `7d0e08f` Keyword Strategy Workshop. Verify: kept_out + keyword provenance
   are additive/backward-compatible (legacy packets load), the approval
   contract and POST /api/run shape are unchanged, "Approve search vocabulary"
   is NOT a new approval leg (strategy stays PENDING), and no destructive
   delete path exists. Exercise the three lanes + restore in-browser.
5. `13c19c6` review PDF. Verify it is physically separate from the release
   door: output only under data/state/review_exports, force-stamped INTERNAL
   REVIEW, and the gated /api/export/pdf refusal rules are untouched.
6. `f5d019b` Phase B status IA + `d0285bb` ticker fallback. Lower risk;
   confirm no release/gate surface changed.

## Part 2 · Seal-treatment collaboration (client-directed, open decision)

Context: Keith flagged "$586.2MCBP" (no space between figure and agency).
William: "use the agency seals." First attempt (4ec09ad) put a per-stat
department seal on every money-bar stat; reverted (0796a74) because in a
single-department report every component stat (CBP/USCIS/TSA) rendered the
IDENTICAL DHS seal and the abbreviation text beside it was redundant. Current
state: spacing fix only ("$50.13M · CBP"), no seal.

Hard asset constraint: data/reference/seals holds 18 DEPARTMENT seals
(dhs, dod, gsa, va, doj, doc, doe, doi, dol, dos, dot, epa, nara, nasa, si,
ssa, usda, dcc). There are NO component seals (no CBP/USCIS/TSA). So a per-stat
seal on component accents can only ever repeat the parent-department seal.

Three options on the table (William picks):
- (1) text-only, as now — cleanest for a single-agency report.
- (2) component seal assets: William drops CBP/USCIS/TSA/etc. seal files in,
  each stat shows its true distinct seal, abbreviation text drops. Needs the
  asset files (official federal marks — William provides).
- (3) ONE seal per section: the focus agency's department seal in the
  money-in-motion header, stats stay text-only. No new assets; no repetition.
  QB recommendation for scoped reports.

Ask of Codex: give an INDEPENDENT design read (not a rubber stamp) on 1/2/3
for a client-facing GTM sales deliverable, including the implied-association
consideration of federal seals on an outward sales artifact. Then, once
William picks: whoever implements, the other reviews. If it's (3), stress-test
across a MULTI-agency (all-scope) report where the header would need to
represent multiple departments, not just one — that is the case the
single-agency preview did not cover.

Do NOT implement a seal change before William rules on the option.

## Report back

Exact SHAs reviewed, findings with owner+severity to docs/reviews/, the seal
design read, and any contract-surface concern in the solo range. Then stop and
hand to William.
