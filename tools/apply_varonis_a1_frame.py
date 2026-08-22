"""Apply work order A1 to the Varonis review packet: buyer-language keywords.

Operator work order A1 (2026-08-17) replaces the Varonis frame's capability
terms with tiered buyer language. The tier map is recorded once, in
clients/varonis/frame_tiers.json; the profile and taxonomy carry it for the
screening layers, and THIS script carries it into the review packet through
`agents.review.amend_terms`, the sanctioned keyword path on an APPROVED
strategy (approval intact, revision recorded, prior terms preserved in
kept_out rather than deleted).

Idempotent by design: run it twice and the second run reports the frame is
already in place and writes nothing. Zero LLM, zero network, zero quota;
bash is the sanctioned lane for it (CLAUDE.md, diagnostic/offline scripts).

Run it once in each checkout whose data/ should carry the frame:

    .venv/bin/python -m tools.apply_varonis_a1_frame

The packet's research_entities CANNOT ride this path: `revise()` refuses on
an approved packet and `amend_terms` deliberately touches keywords and NAICS
only. The tier 1 rival additions (Everfox, Forcepoint, Proofpoint) and the
DatAlert product therefore live in the profile's
named_competitors_and_incumbents now and reach the packet only through an
operator unlock-revise-reapprove, which this script must never perform.
"""
from __future__ import annotations

import json
import os
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CLIENT = "Varonis"
_FRAME_TIERS = os.path.join(_ROOT, "clients", "varonis", "frame_tiers.json")

# Keyword categories the amendment replaces. Everything else (agency,
# set_aside, naics, search_term) is a procurement lens or curated literal and
# rides through untouched.
_REPLACED_CATEGORIES = ("capability", "technology")

_NOTE = "work order A1 2026-08-17: buyer-language frame"
_KEPT_OUT_NOTE = ("replaced by the A1 buyer-language frame 2026-08-17; "
                  "restorable here")


def _frame() -> dict:
    with open(_FRAME_TIERS, encoding="utf-8") as fh:
        return json.load(fh)


def _tier2_terms(frame: dict) -> list[str]:
    ordered = list(frame["as_ordered"]["tier2"])
    for extra in (frame.get("screen_routing") or {}).get(
            "tier2_added_by_routing", []):
        if extra not in ordered:
            ordered.append(extra)
    return ordered


def _tier3_terms(frame: dict) -> list[str]:
    return list(frame["as_ordered"]["tier3"])


def _keyword(term: str, category: str, tier: str) -> dict:
    return {
        "term": term,
        "category": category,
        "rationale": f"{tier} buyer language for the data-security category",
        "origin": "consultant",
        "source": _NOTE,
        "note": ("tier 3 context, never keep-qualifying alone"
                 if tier == "tier 3" else "tier 2, keep-qualifying"),
    }


def build_amendment(packet) -> tuple[list[dict], list[dict], dict]:
    """(keywords, kept_out, summary) for amend_terms, from the live packet."""
    frame = _frame()
    current = [k.model_dump(mode="json") for k in packet.strategy.keywords]
    kept_out = [k.model_dump(mode="json") for k in packet.strategy.kept_out]

    preserved = [k for k in current
                 if k.get("category") not in _REPLACED_CATEGORIES]
    displaced = [k for k in current
                 if k.get("category") in _REPLACED_CATEGORIES]

    new_keywords = (
        [_keyword(t, "capability", "tier 2") for t in _tier2_terms(frame)]
        + [_keyword(t, "technology", "tier 3") for t in _tier3_terms(frame)]
        + preserved)

    already_out = {str(k.get("term", "")).casefold() for k in kept_out}
    new_kept_out = list(kept_out)
    for k in displaced:
        if str(k.get("term", "")).casefold() in already_out:
            continue
        row = dict(k)
        row["note"] = _KEPT_OUT_NOTE
        new_kept_out.append(row)

    summary = {
        "replaced": [k.get("term") for k in displaced],
        "added_tier2": _tier2_terms(frame),
        "added_tier3": _tier3_terms(frame),
        "preserved": [f"{k.get('term')} ({k.get('category')})"
                      for k in preserved],
    }
    return new_keywords, new_kept_out, summary


def already_applied(packet) -> bool:
    frame = _frame()
    want_cap = [t.casefold() for t in _tier2_terms(frame)]
    want_tech = [t.casefold() for t in _tier3_terms(frame)]
    have_cap = [k.term.casefold() for k in packet.strategy.keywords
                if getattr(k.category, "value", k.category) == "capability"]
    have_tech = [k.term.casefold() for k in packet.strategy.keywords
                 if getattr(k.category, "value", k.category) == "technology"]
    return have_cap == want_cap and have_tech == want_tech


def main() -> int:
    from agents.review import amend_terms, load_packet

    packet = load_packet(CLIENT)
    if packet is None:
        print(f"no review packet for {CLIENT}; nothing to amend")
        return 1
    if already_applied(packet):
        print("A1 frame already in place; packet untouched "
              f"(revision {packet.revision_count or 0})")
        return 0

    keywords, kept_out, summary = build_amendment(packet)
    amended = amend_terms(CLIENT, keywords=keywords, kept_out=kept_out)
    print(f"amended {CLIENT} packet to the A1 buyer-language frame")
    print(f"  revision: {packet.revision_count or 0} -> "
          f"{amended.revision_count or 0}")
    print(f"  capability (tier 2): {len(summary['added_tier2'])} terms")
    print(f"  technology (tier 3): {len(summary['added_tier3'])} terms")
    print(f"  moved to kept_out (restorable): {len(summary['replaced'])}: "
          + ", ".join(summary["replaced"]))
    print(f"  preserved untouched: {len(summary['preserved'])}: "
          + ", ".join(summary["preserved"]))
    return 0


if __name__ == "__main__":
    sys.exit(main())
