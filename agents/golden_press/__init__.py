"""Golden press (GOLDEN_BUILD, 2026-07-24) · evidence-first report pipeline.

The golden flow reproduces the manual frontier-model process that made the
hand-built Riverbed reference: exhaustive entity-aware retrieval into one
evidence pack, full-context composition into the golden design skeleton, a
critic loop, and a mechanical validator. The deterministic candidate-review
composer stays in place but is disconnected from this flow's deliverable.

Modules:
  records.py   · GoldenRecord / LaneQuery / EvidencePack contracts
  retrieval.py · lanes L1 (sweep notices), L2 (entity awards), L3 (clock
                 flags), L4 (APFS forecasts), dedup, sufficiency, pack build
  recall.py    · POST-RETRIEVAL golden-dock recall scoring (never queries)
"""
