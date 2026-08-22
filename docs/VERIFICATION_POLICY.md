# VERIFICATION_POLICY.md · figure arbitration (2026-07-16)

Every dollar figure, date, and count in a rendered client deliverable
carries its own provenance: source system, source record id, and retrieval
timestamp (`agents/reports/facts.py::Fact`). Freshness is a property of the
figure, not the document. When the extract pass and the adversarial verify
pass disagree on a figure, this policy decides who wins. The rule set is
fixed; the enforcement code cites this file.

## The rules

1. When the extract pass and the adversarial verify pass disagree on a
   figure, the primary federal record (USAspending award page, SAM notice)
   wins automatically. Secondary sources (news, Deltek-derived reporting,
   vendor pages) never override a primary record.

2. When two primary records disagree, or a primary record is unreachable,
   the figure is frozen, the artifact cannot pass the release gate, and a
   human resolves it. The resolution is logged append-only (the existing
   observations JSONL pattern: figure, both values, records cited,
   decision, decider, timestamp).

3. No model resolves a dispute over a figure it authored.

## Enforcement map

- Primary systems are `PRIMARY_SOURCE_SYSTEMS` in
  `agents/reports/verification.py` (USASPENDING, SAM). APFS forecasts,
  agency documents, and analyst-compiled references are secondary for
  arbitration whatever their standing as evidence tiers.
- Rule 1 is `verification.arbitrate()`: exactly one distinct primary-record
  value among the competing values resolves automatically with decider
  `policy:primary_record`. The decision is made by RULE; a model never
  makes it. The automatic resolution is still logged (rule 2's log), so
  the trail is complete.
- Rule 2 is the gate: a fact with `disputed=True` and no logged resolution
  (or a logged decision of `frozen`) fails `freshness_violations` with
  `FIGURE_DISPUTED`, which flags the build and forces DRAFT exactly like a
  stale figure. The log lives at
  `data/state/verification/<slug>.resolutions.jsonl`, append-only, one
  `FigureResolution` row per line (figure, both values, records cited,
  decision, decider, timestamp). A superseding decision is a new row;
  rows are never rewritten. The latest row for a figure governs.
- Rule 3 is mechanical in `verification.append_resolution()`: a decider who
  appears as `observed_by` on any competing value is refused. Humans and
  the policy rule are never disputants.
- The dispute structure the verify pass writes into is
  `Fact.disputed` + `Fact.competing_values` (`CompetingValue`: value,
  source system, source record id, source URL, observed_by, observed_at).
  The verify pass itself is a separate Codex-owned session; the worked
  example it must satisfy is
  `tests/test_figure_freshness_gate.py::test_worked_example_insignary_odos_iii`
  (the $174,537,375 vs $122.2M ODOS III dispute, resolved to the
  USAspending award record by rule 1).

## Freshness, same gate

- `FRESHNESS_MAX_AGE_DAYS = 14` (config constant in
  `agents/reports/verification.py`; never a call-site literal). A cited
  figure older than that at render time fails `FRESHNESS_STALE` with a
  re-pull instruction naming the adapter and the Command Center step.
- A figure with no recoverable retrieval time is `UNKNOWN_FRESHNESS` and
  fails the same way. A guess never substitutes for provenance.
- The evidence dock's "data current" line is computed from
  min(retrieved_at) across rendered figures
  (`verification.data_current_date`, `signal_board._data_current_clause`);
  it is structurally impossible to hand-set. A computed date diverging
  from the document date by more than the threshold fails before render.
- Provenance renders nowhere in client artifacts; it lives in the INTERNAL
  sidecar only. Client copy carries no retrieval vocabulary
  (the composer payload physically excludes the provenance fields).
