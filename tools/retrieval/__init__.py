"""Retrieval lanes over the notice store (work order R1, 2026-08-18).

Lexical (FTS5/BM25), dense (local sentence embeddings), and hybrid
(reciprocal rank fusion) retrieval. Nothing here bypasses the guards: the
fused set rides the existing guard ladder and negative-context corpus, and
NAICS/PSC code membership is a boolean FEATURE on each row, never a gate
(the inversion: keywords and meaning retrieve, codes describe).
"""
