# STATUS · Osprey Capability-Inversion Build · 2026-07-09

## What the data said
- **The GDIT teaming thesis fell.** Zero of 500 subaward records in the
  boundary NAICS lanes match Osprey's capability vocabulary at the
  description level; GDIT's $893.7M (and Perspecta's $938.7M) is enterprise
  IT money that any vendor's lane filter would surface. The finding is
  "capability-matched teaming flow in these lanes is unclaimed." The one
  capability-verified door is HII Mission Technologies: 3 matched
  subawards, $97.2M, described as ISR services.
- **The incumbent buyer map is the actionable center.** 50 buying offices
  show description-matched purchasing (population: gross obligated value of
  matched award/subaward records, trailing 5 years). US Customs and Border
  Protection: $12.07M matched (ForeFlight, Jeppesen, Garmin, Dataminr) with
  3 sole-source notices attached, incl. the Air and Marine Contracting
  Division NOIs. Displacement windows (PoP end inside 18 months) include
  Dataminr at US Secret Service (2026-07-31), US Marshals (2026-09-30),
  NTSB (2026-09-21), plus Garmin/OSINT renewals at 9 more agencies.
- **IGW4 is a resolved do-not-pursue.** The NGA Justification names
  Compusult and the buyer map carries the J&A quote and link.
- **The capability-verified recompete calendar** (lane calendar cut):
  CACI geospatial-intelligence at SOCOM $190.7M ends 2026-07-17; Bluestaq
  Air Force data platform $515.3M ends 2026-07-31; Parsons Air Force
  awards end 2026-07-31; FAA NOTAM modernization (Karsun) $235M ends
  2026-08-14. GDIT Aug 31 2026 and Leidos Sep 27 2026 remain real dates,
  now labeled component-matched (no description evidence at that source).
- **Funded demand exists in budget text:** the Airspace Location and
  Enhanced Risk Transparency Act of 2026, CJS Appropriations 2027 airspace
  security language, FAA Reauthorization overflight-risk text (document-
  level citations; dollar-line extraction is a recorded limitation, L5).

## What changed in the thesis
From "build the GDIT teaming relationship" (lane-derived, client-invariant,
falsified) to: sell displacement and unclaimed space. Federal buyers
demonstrably pay for aviation data products Osprey competes with, on
subscriptions that renew inside 18 months; the capability lane has no
incumbent teaming claimant; and rulemaking plus budget text show demand
forming on Osprey's core subject.

## The three sentences for the Osprey meeting
1. "We mapped every federal office that already buys aviation-risk and
   flight-data products by name, and 12 of those subscriptions, including
   CBP's ForeFlight and Jeppesen sole-source renewals, come up for renewal
   within 18 months; each renewal is a displacement window with the
   incumbent, office, and end date on one page."
2. "The subcontract flow in your NAICS lanes belongs to enterprise-IT
   primes, not aviation-risk vendors, which means the teaming space for
   your actual capability is unclaimed; the one description-verified door
   is HII's ISR services flow, and we'd approach it first."
3. "Congress is already writing your subject into law and appropriations,
   from the Airspace Location and Enhanced Risk Transparency Act to FAA
   reauthorization language, so the 6-to-18-month positioning plays have
   funded demand forming behind them."

## What remains open
- FPDS ATOM as a secondary description source (1A spec item; USASpending
  coverage shipped first).
- Dollar-line extraction from govinfo full text (LEARNINGS L5, PROPOSED).
- Award-portion attribution in buyer-map totals (LEARNINGS L6, PROPOSED);
  the population label discloses gross-value counting meanwhile.
- Profile lives at clients/osprey_flight_solutions/profile.json (JSON, no
  yaml dependency in repo; spec named profile.yaml).
- Renderer rules pending: notice-ID-appears-once lint;
  vehicle-without-linked-finding lint; explicit snapshot-ID stamp rendered
  in the footer (all sections already render from the single sweep
  artifact in one pass).
- Thread-level (per-horizon-item) evidence grading for the meta-check; the
  shipped meta-check operates at the teaming-evidence level across
  consecutive builds.

## Per-source API calls this session
- USASpending: 62 (buyer map: 31 award + 31 subaward keyword searches),
  plus ~15 for the description slice refresh (5 lanes x edges/expiring/
  aggregation). Free, no key.
- GovInfo: 8 (funded-demand core terms). Keyed, free tier.
- SAM API: **0** (quota rule held; all notice work from the local extract).
- LLM: report builds only (review/compose/audit/patch/editor via Max plan).
