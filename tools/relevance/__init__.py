"""Capability relevance engine (2026-07-17): keywords primary, codes as boundary.

Relevance is decided by capability evidence, never by code membership.
NAICS and PSC codes are boundary filters: they exclude, they never
include. A notice matching a boundary NAICS with no capability-keyword
evidence is noise; a notice with strong capability evidence under an
unexpected code is signal, flagged OFF-CODE SIGNAL (internal vocabulary
only). This is the evidence-inversion principle (R11) made a first-class,
testable engine with per-client taxonomies.

Package layout: taxonomy.py (schema + per-client files), engine.py
(deterministic matcher + scoring, no LLM in the hot path), calibrate.py
(the human-review disagreement harness), adjudicate.py (the append-only
decision seam).
"""
