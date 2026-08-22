# DESIGN v2 · per-motion title strategy

**Status:** design. Reviewer rulings absorbed. **One contract-surface change
identified and NOT yet implemented** (see Lifecycle). Nothing built.
**Branch:** `fable/apollo-email-path` (HEAD `07cc21a`)

## The problem

Band 09 sources titles from a STATIC persona ladder: four tiers, fixed title
lists, selected by row class. Every client and every opportunity searches for
the same people. An observability displacement at a Navy SYSCOM and a
cryptocurrency training program at FBI Cyber get identical vocabulary, which
is why the tier attribution looked clean at n=2 and means very little.

## Prior art, reused not rebuilt

`agents/decisions/schemas.py::BuyerPersona` / `TitleStrategy` already carry
the right model and were never wired to Band 09. Adopted as-is.

## Doctrinal position

Inferred titles are **search vocabulary, not claims**, the same category as
capability keywords and NAICS codes, which are already model-generated here
and gated by an operator workshop. The band stays deterministic: a model
proposes a vocabulary, the operator approves it, the supply lane searches
with it, the render assembles sentences from rule output and approved parts.

---

## REVIEWER RULINGS, ABSORBED

### R1 · The motion key is a BATCHING key, not the strategy key

`(row_class, org_kind)` batches specs. It does not determine a title set.
The strategy key differs by side:

| side | strategy key | why |
|---|---|---|
| `buying_component` | `(row_class, org_kind, agency_mission_applicability)` | DoD carries Authorizing Official, PEO, SYSCOM programme office; civilian carries OCFO, bureau CIO. A single set covering both is generic by construction. |
| `paper_holder` | `(row_class, org_kind, holder_identity, route_role)` | The target is a COMPANY. Agency family is irrelevant; what matters is which holder and whether it is reseller, prime, or vehicle holder on the cited record. |

This is sharper than the "third axis" I proposed: the axis is not the same
on both sides. Measured, red_hat's one `displacement x buying_component`
motion spans FBI, IRS, OCFO, Army, DISA, Air Force and Navy, which under R1
splits by mission applicability rather than staying one set.

### R2 · Blind independent generation plus deterministic diff

Generator and challenger both work from the same evidence, neither sees the
other's output. The challenger returns three named lists:

- `recommended` titles
- `likely_omissions`
- `tempting_but_wrong` titles, with the reason they read as plausible

Deterministic diff produces agreement, additions, and disputes. A targeted
critique pass runs ONLY when disagreement is material. Anchoring is the
failure this avoids: an adversary asked to *refute* found six false
rejections in the screener that a reviewer asked to *review* would have
waved through.

### R3 · The operator wins; a dispute withholds, it does not block

A disputed title stays OUT of supply. The motion still runs on its approved
titles. The motion blocks only when **no approved title remains for a
required function**, which is stated as a receipted supply gap rather than
an error.

### R4 · No four-persona minimum

The legacy `min_length=4` is removed. The requirement becomes
**evidence-supported functional coverage**: each function the motion
actually needs (mission/program, security operations, procurement, channel)
either has an evidence-supported title or a receipted gap. A floor
manufactures padding on a thin motion, and padding is what the static ladder
already does.

### R5 · `persona.why` stays internal

It renders in show-the-work, never in client copy. Client-facing reasoning is
assembled from deterministic templates over approved parts.

### R6 · A contracting POC proves procurement authority only

Usable as a route. Promoted to another function ONLY when the source
explicitly establishes that role. The default is procurement, and the
promotion needs published evidence, not inference.

### R7 · Hash the canonical inference payload, not record ids

The strategy fingerprint covers the whole payload the inference actually
saw, and any of these stale it:

- new or changed evidence rows
- changed organisation resolution
- changed capability vocabulary or NAICS boundary
- changed targeting rules
- changed schema version
- changed prompt version

Record ids alone would let a rewritten prompt or a re-resolved org silently
reuse an approved set. Same posture as the Assess approval's evidence
binding, which re-locks Produce on drift with no new click.

### R8 · The ladder is an explicit operator-imported recovery set

Never an automatic fallback. If inference is unavailable the motion reports
a supply gap; the operator may then IMPORT the ladder deliberately, and the
import is recorded as operator provenance. This closes the failure I flagged
in v1: a fallback that fires silently becomes the default, which is exactly
how the 25-credit cap became an invisible coverage limit.

---

## LIFECYCLE · the correction that matters, and the contract surface it touches

v1 claimed "no new approval state". **That was wrong**, and the reason is
temporal.

### Three decisions, in order

| # | decision | question | artifact | exists? |
|---|---|---|---|---|
| 1 | Assess approval | is this evidence sound? | `data/review/<slug>.assess_approval.json` | yes, evidence-bound |
| 2 | Target approval | may we do outreach work at all? | `data/review/<slug>.target_approval.json` | yes |
| 3 | **Title-set approval** | **may we spend against THIS vocabulary?** | none | **NO** |

Decisions 1 and 2 both **predate the titles**. The Target gate is what
unlocks the targeting lane so the titles can be generated in the first
place. An approval cannot bind a title set that did not exist when it was
signed, so neither existing decision can carry this.

Verified on disk: `target_approval.json` carries exactly `client`,
`approved_at`, `approved_by`, `note`. No evidence binding of its own, no
title concept. It is a proceed-to-outreach decision and nothing more.

### Why decision 3 must be SEPARATE, not a third leg on `target_gate_status`

`agents/review.py::target_gate_status` is documented as "the one gate
derivation" and has five production consumers (`run_targets.py:113`, and
four call sites in `ui/server.py`). Two clients hold valid Target approvals
today (insignary, mark43).

Adding a title leg to that function would **re-lock both clients and every
future one** until a title set exists, which is a breaking change to a
documented door. It would also conflate two genuinely different questions:
*may we do outreach* and *is this vocabulary right*.

**Proposal:** a new, narrower decision at
`data/review/<slug>.title_approval.json`, bound to the R7 fingerprint,
consulted ONLY by the supply step. `target_gate_status` is untouched, so
nothing currently unlocked becomes locked, and supply still requires the
Target gate first. Drift stales the fingerprint and withholds supply with no
new click, mirroring how evidence drift re-locks Produce.

### Contract-surface callout (CLAUDE.md session protocol, rule 3)

This adds a durable operator decision adjacent to the documented
**"Assess -> Target operator door"** section of `docs/CONTRACT_SURFACES.md`.

- It does **not** weaken any existing leg.
- It does **not** unlock outreach from a new code path.
- It **does** add a required precondition to the supply step, and
  `CONTRACT_SURFACES.md` needs a paragraph describing it.

Per rule 3 this ships as an **isolated, clearly-labeled diff with its own
tests, presented separately for approval**. The rest of the title-strategy
work does not depend on it landing first: inference, challenge, diff and the
workshop can all be built and tested while supply stays gated exactly as it
is today.

---

## Pipeline

```
A  MOTION BATCHING        deterministic, zero LLM
B  STRATEGY KEYING        deterministic, per R1 (side-dependent)
C  BLIND GENERATION x2    generator + challenger, neither sees the other
D  DETERMINISTIC DIFF     agreement / additions / disputes
E  TARGETED CRITIQUE      only on material disagreement
F  OPERATOR WORKSHOP      three lanes, provenance per title
G  TITLE-SET APPROVAL     durable, bound to the R7 fingerprint  [NEW GATE]
H  SUPPLY                 POC -> contact graph -> Apollo gap-fill
```

### Supply precedence (H)

1. **Government-published POCs** on notices bound to the motion's own cited
   records. R6 conservatism applies.
2. **Contact graph** identities already on disk.
3. **Apollo gap-fill**, only for functions still uncovered and only for the
   missing FIELD. A POC with a name and a `.gov` address but no mobile is a
   one-field enrichment, not a fresh search.
4. **Operator-supplied**, labeled.

Measured: 11,245 distinct `.gov`/`.mil` POC addresses on disk, 11,242 named.

## Rendered reasoning stays deterministic

| field | source |
|---|---|
| `why_this_account` | the decision rule: R1 tension, R2 verbatim sentence, R4 adjacency |
| `why_this_person` | template over the APPROVED persona: matched title, function, motion |
| `why_now` | the clock already on the record |
| `recommended_action` | template per motion class |

## Cost

1-3 motions per client measured, split by R1 keying to roughly 2-5, times
two blind generations, plus occasional critique: **4 to 12 model calls per
client** on the Max plan, zero API tokens.

## Build order proposed

1. Motion batching + R1 strategy keying (deterministic, testable alone)
2. Blind generation, challenger, deterministic diff
3. Workshop lanes + provenance
4. **Contract-surface diff: title-set approval** (isolated, presented separately)
5. Supply precedence rewrite, POC-first
6. Band 09 + export alignment

Steps 1-3 and 5-6 do not depend on 4 landing; supply stays gated as it is
today until the contract change is approved.

## Not proposing

No change to bands 00-08 or the report contract. No model output rendering
as a claim. No live Apollo call. No change to `target_gate_status`. No
replacement of the deterministic T1/T2/T3 derivation.
