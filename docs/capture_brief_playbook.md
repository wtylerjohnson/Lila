# Capture Brief Playbook (Federal Opportunity Assessment)

Standing standards for every client report. Updated 2026-07-09 with the
capability-inversion release.

## Evidence inversion (permanent)
Capability keywords are the PRIMARY evidence layer; NAICS codes are a
coarse boundary filter only. A NAICS lane marks where a contract is filed,
not what a vendor sells. Teaming doors, recompete calendars, and buyer
claims must be description-verified against the client profile
(`clients/<slug>/profile.json`). No profile, no sweep. The client-
invariance check runs on every build: a finding a dummy vendor would also
get is not client intelligence.

## Signal-density rule
Every statistic carries its population label or does not render. Lane
totals and unlabeled distribution statistics are excluded from stat cards,
budget bars, addressable framing, and thesis support; they survive only as
one labeled context sentence. Gross keyword-matched spend is labeled as
such. Verified negatives (unclaimed space, do-not-pursue) are first-class
findings, not gaps.

## Title standard
Titles are informative noun phrases. No hooks, no questions, no em dashes
anywhere in the document (house punctuation: commas, colons, periods,
middots).

## Evidence labels
Every claim carries its evidence level, and readers never have to infer:
CAPABILITY-VERIFIED (description-matched, scored), component-matched
(mission component, no description evidence), LANE-LEVEL EVIDENCE ONLY,
NOT CAPABILITY-VERIFIED (mandatory, un-suppressible), manually compiled
(provenance tag with as-of date), FINDING (verified negative).

## DRAFT / RELEASE convention
One command, two states. The pipeline ALWAYS renders. DRAFT: any open QA
flag or the release switch off; watermark band + QA appendix listing every
auto-fix, suppression, and open flag. RELEASE: zero flags, arbiter
consensus, `--release` passed; clean PDF, QA log to
`<slug>...qa.json/.md` sidecars. Flags are resolved by fixing source data
or copy and regenerating; there is no bypass from DRAFT to RELEASE.

## Standing collectors
- Incumbent buyer map (every sweep): named products + adjacent terms vs
  award/subaward descriptions; displacement windows = PoP end inside 18
  months; sole-source/J&A language attaches from the local SAM extract.
- Capability prime ranking (every sweep): core+adjacent terms vs subaward
  descriptions, ranked by capability-matched dollars.
- Funded-demand scan (govinfo, core terms): budget/appropriations text;
  document-level citations; empty is a valid finding.
- Meta-check: teaming graded on lane evidence alone for two consecutive
  builds auto-flags "evidence upgrade needed."

## Learnings promotion rule
`pipeline/LEARNINGS.md`: every entry is PROPOSED until a test or collector
enforces it (ENCODED). The lint suite fails any entry PROPOSED > 30 days.

## The value standard (the editor's charter)
Every assessment is optimized for four things, judged in a value pass
before the final revision: WELL PRESENTED (one storyline, clean numbers),
RELEVANT (this client's product and motion, never generic federal
wallpaper), NEW TO THE CLIENT (their own record and their market's common
knowledge are worth zero; unseen buyer behavior, unclocked windows, and
unmapped doors are the value), ACTIONABLE (a named office, a date, a door,
a watch to stand up — information with no handle gets cut). The editor
records its per-axis judgment; the internal file carries it.
