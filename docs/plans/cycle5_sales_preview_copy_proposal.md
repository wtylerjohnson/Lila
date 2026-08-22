# Cycle 5 sales-preview copy proposal

**Decision owner:** William

**Status:** Approval required. No renderer, model, gate, test, or client-facing
artifact changes until William approves the exact strings.

## Proposed voice

Lead with verified counts, time horizon, source coverage, and method. Describe
the delivered working detail without exposing names, contacts, evidence traces,
or pursuit plays.

## Exact before-and-after set

| Surface | Current | Proposed |
|---|---|---|
| Generic withheld row | “Locked · included in the full engagement” | Context label: “Additional graded pursuit · full identity in the assessment”; “Mapped competitor · identity in the assessment”; or “Monitored signal · detail in the assessment.” |
| Lock label | “CLIENT FEATURE” | “IN THE ASSESSMENT” |
| Agency-dollar badge | “locked” | “source-traced” |
| Market map | “Per-agency breakdown and the TAM math ship with the full assessment, every dollar traced to its source.” | “See where addressable spend sits and how the estimate was built. The assessment provides the source-traced agency map and calculation.” |
| Market teaser | “Weekly watchlist, a client feature, tracks the full feed.” | “Use the verified counts and time horizons above to separate what is actionable now, what is forming, and what remains under watch. The assessment adds the source-coverage detail behind that map.” |
| Pursuit dossier | “The full dossier (contracting office, contacts, vehicle path, incumbent, runway, recommended play) is a client feature.” | “Move from signal to pursuit decision. The assessment adds the exact notice and source, buyer contacts, vehicle and incumbent context, runway, and recommended next move.” |
| Competition | “{n} competitors mapped in your lanes. Names, the awards and vehicles behind each, and why they'll show up: it all ships with the full assessment.” | “{n} competitors mapped in your lanes. The assessment names each competitor and ties its position to cited awards and vehicles.” |
| Teaming path/watch | “Named evidence-graded candidates and their cited award records are client content.” | “The assessment converts this direction into a ranked partner path with evidence-graded candidates and cited award records.” |
| Scoreboard teaming | “Directions and candidates are client content.” / “Primes and evidence are client content.” | “Includes pursuit-specific direction and evidence-graded candidates.” / “Includes ranked prime candidates grounded in cited award evidence.” |
| Watchlist row | “Watchlist item · {source} · weekly monitoring is a client feature.” | “{source} signal under weekly monitoring · full signal detail in the assessment.” |
| Sales close | “What the full engagement includes” | “What your team can act on” |
| Scoreboard watchlist ticker | “plus {n} more items in the weekly watchlist” | “{n} additional source-linked signals under weekly monitoring” |
| Close body | “The per-agency dollar map with the math shown, all pursuit dossiers with recommended plays, named competitors with the evidence behind each, and the weekly-refreshed watchlist, every figure traced to a citable source.” | “A working opportunity map: where to focus, which live postings merit action, what is developing, who is positioned, where to team, and what changed. Live pursuits retain cited evidence; required source-coverage gaps remain visible.” |
| Footer | “All opportunities are verified against primary sources as of {date}. Full identifiers, contacts, and pursuit plays are part of the client engagement.” | “Method: approved capability terms and NAICS boundaries screened against SAM.gov and official federal planning sources. Live pursuit claims require current SAM.gov evidence; market and horizon signals remain source-cited and distinct from live postings. The assessment adds exact identifiers, contacts, evidence traces, and pursuit plays.” |

## Guardrails and approval

Copy approval authorizes words only. `gate_for_sales` still removes notice
IDs/URLs, contacts, identities, award rows, blocker/set-aside analysis, and
evidence traces. Counts, verifier marks, held-coverage language, third-party
headline/URL integrity, client-file doctrine, and the leak suite remain fixed.
Implementation gets its own labeled client-template commit and full report
lint battery.

The global `_LOCK_NOTE` candidate-name substitution token is not a standalone
copy surface and must not be replaced blindly; any implementation must change
only complete rendered sentences after the physical leak gate.

- **Approve as written**
- **Approve with edits below**
- **Defer**

**William’s notes:** _No code lands until a decision is recorded._
