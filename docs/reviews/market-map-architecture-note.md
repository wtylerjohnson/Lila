# ARCHITECTURE NOTE · Federal Market Map in the canonical press path

**Branch:** `fable/market-map-ontology` (worktree `~/lila-market-map`)
**Base:** `fable/apollo-email-path` @ `8d0d66d`
**Written before code, per CLAUDE.md session protocol step 2.**

---

## 1. The canonical active press path, traced

```
Command Center  POST /api/run {client_name, step:"candidate_review",
                               args:{golden_only:true}}
  ui/server.py:1670        cmd = run_candidate_review.py --client X --golden-only
  run_candidate_review.py:477/512   from agents.golden_press.press import golden_press
  agents/golden_press/press.py:492  golden_press(client, ...)
        build EvidencePack                       (records.py)
        competitive_build                        (competitive.py, pre-composition)
        _term_discovery                          (term_discovery.py, pre-retrieval)
        render_content_region(pack, prose)       (render.py) DETERMINISTIC
        split_golden / brand_skeleton / add_band_nav / splice  (skeleton.py)
        compile_editable_studio(...)             (studio.py)
        validate_press(content, studio, pack)    (validate.py)
        write client + studio + validation sidecar
```

**All five seams the brief asks about are confirmed active.** `press.py` is
the orchestrator, `EvidencePack` is the canonical evidence model,
`render_content_region` is the deterministic renderer, `skeleton.py` carries
the golden skeleton, `studio.py` carries the editable runtime.

## 2. The correction this note exists to make

I built `agents/golden_press/market_map.py` earlier today as a standalone
renderer with its own validator. **That is the third report architecture the
brief forbids.** It reads an `EvidencePack` but bypasses `press.py`,
`skeleton.py`, `studio.py` and `validate.py` entirely, so nothing it renders
is editable, portable, brand-marked, or validated by the press gate.

It is not wasted: its seven section contracts, its gap-phrase discipline and
its density laws (no repeated sentence, no repeated row, no blank cell) are
the right ontology. This work **folds that ontology into the canonical path
and retires the standalone renderer as a public entry point.**

## 3. Existing patterns being extended, not replaced

| pattern | where | how it is extended |
|---|---|---|
| `EvidencePack` as the one evidence join | `records.py` | the projection derives FROM it; no second evidence model |
| deterministic render, prose in slots only | `render.py` | renderer receives a completed projection and stops reinterpreting the pack |
| pack-derived rules print their evidence | `decision_rules.py`, `targeting_rules.py` | promotion rules follow the same shape: pure, receipted, negative-cased |
| capped lanes speak one dialect | `tools/api/base.cap_disclosed` | opportunity inventory caps disclose the same way |
| contract file is the single source | `docs/REPORT_CONTRACT.md` | `MARKET_MAP_CONTRACT.md` already exists and the validator reads it |
| provenance or no render | `targets_store.py` | `EvidenceReference` generalises that law to every material number |
| research demand as a first-class output | `term_discovery.py` (built today) | `research_demand` becomes a typed section, not a log line |
| screening state gates publication | `person_screen.py` (built today) | `CoverageState` is the same idea applied to any answer set |

## 4. What is new, and why it has to be

**`FederalMarketMapDocument`** — one typed projection with eight answer sets,
assembled BEFORE narrative and BEFORE render. Today the renderer derives
sections from the pack inline, which is why prose and evidence interleave and
why the same opportunity can appear in three places. A projection assembled
first makes single-appearance a property of the data, not of renderer
discipline.

**`ExactMoney`** — `Decimal` or a preserved source literal. The pack stores
`obligated_dollars: float`, and `float` is a **contract surface**: it is in
`GoldenRecord`, consumed by `rollups`, `decision_rules`, `compose`,
`signal_board` and every existing test. Per session protocol step 3, the
float-to-Decimal change is isolated, and everything else ships regardless.
The projection therefore carries `ExactMoney` built FROM the float with the
source literal preserved, and renders the exact figure. No client-visible
number is abbreviated.

**`CoverageState`** — complete / partial / research_next, with known fields,
missing fields and the next action. This is what turns absence into a work
order instead of an apology.

## 5. Contract surfaces encountered

1. **`GoldenRecord.obligated_dollars: float`** (`records.py`). Changing it to
   `Decimal` touches every consumer. ISOLATED, presented separately.
2. **`docs/REPORT_CONTRACT.md` band order** (`validate.GOLDEN_BAND_ORDER`).
   The Market Map is a DIFFERENT deliverable with its own contract; the band
   family is untouched.
3. **Assess → Target operator door** (`CONTRACT_SURFACES.md`). The contacts
   section reads only what the targeting lane already produced under that
   gate. No new unlock path.

## 6. Deliberately retained

The band-family press (`GOLDEN_BAND_ORDER`, bands 00-09) stays. Two clients
hold approvals bound to it, and the brief's completion standard is that every
client can be pressed into the clearer system, not that the older one is
deleted mid-flight. The migration note names what is shared, what is
replaced, and what is retained.

## 7. Sequence

1. evidence objects (`EvidenceReference`, `ExactMoney`, `CoverageState`)
2. the projection and its deterministic promotion + dedupe rules
3. research-demand derivation from typed absence
4. bounded narrative limits
5. renderer consumes the projection
6. validator: 18 checks from the brief
7. press Riverbed and Thinklogical through the Command Center
8. migration note
