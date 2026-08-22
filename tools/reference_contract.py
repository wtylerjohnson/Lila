"""The reference contract: what a benchmark artefact IS, machine-readably.

WHY THIS EXISTS. The gauntlet's parity gate says the production report must
meet or exceed the GOLDEN 87 benchmark in section coverage, evidence
mechanics, interactions, navigation and editability. "Meet or exceed" is
unenforceable as prose, so this module turns an artefact into a structural
contract, and parity into a diff of two contracts. The same extractor runs
over the benchmark fixture and over every pressed report; a gate failure
names the exact structure that is missing rather than a vibe.

NO HARDCODED QUANTITIES. The benchmark carries 25 evidence cards; the
contract records the INVARIANTS (every card has a lifecycle, a status badge,
a match panel, an action panel) and the counts ride along as observations,
never as requirements. A client with 300 qualifying records must scale past
the benchmark, and one with 4 must not fail for having 4.

WHAT A CONTRACT CONTAINS.

  sections     ordered section ids + their headings
  evidence     card anatomy invariants, source-link domains, id-link counts
  work         show-the-work mechanics (drawers, receipts, inline work)
  editing      the edit/save/export surface (data-edit-id, toolbar actions)
  navigation   nav presence and target coverage of section ids
  unknowns     how missing facts render (visibly, not silently)
  tokens       the visual token set (CSS custom properties)
  interactions JS behaviours by name, listener types
"""

from __future__ import annotations

import json
import re
from collections import Counter
from pathlib import Path

REFERENCE_CONTRACT_VERSION = "reference_contract.v1.2026-08-07"

_STYLE = re.compile(r"<style[^>]*>.*?</style>", re.S | re.I)
_SCRIPT = re.compile(r"<script[^>]*>.*?</script>", re.S | re.I)
_TAG = re.compile(r"<[^>]+>")


def _text(markup: str) -> str:
    return " ".join(_TAG.sub(" ", markup).split())


def _body(html: str) -> str:
    return _SCRIPT.sub("", _STYLE.sub("", html))


def _classes(markup: str) -> Counter:
    counts: Counter = Counter()
    for match in re.finditer(r'class="([^"]+)"', markup):
        for name in match.group(1).split():
            counts[name] += 1
    return counts


def extract(html: str) -> dict:
    """One artefact in, one structural contract out. Deterministic."""
    body = _body(html)
    css = "\n".join(re.findall(r"<style[^>]*>(.*?)</style>", html, re.S))
    js = "\n".join(re.findall(r"<script[^>]*>(.*?)</script>", html, re.S))
    classes = _classes(body)

    sections = []
    for match in re.finditer(
            r'<section[^>]*\bid="([^"]+)"[^>]*>(.*?)</section>', body, re.S):
        sid, inner = match.group(1), match.group(2)
        heading = ""
        head = re.search(r"<h[12][^>]*>(.*?)</h[12]>", inner, re.S)
        if head:
            heading = _text(head.group(1))
        sections.append({"id": sid, "heading": heading,
                         "words": len(_text(inner).split())})

    href_domains: Counter = Counter()
    for match in re.finditer(r'href="(https?://[^"/]+)', body):
        href_domains[match.group(1).split("//", 1)[1]] += 1
    # Procurement facts demand official domains; an EVENT's correct source
    # is the organiser's own page (the source-role law itself says so), so
    # the events section is excluded from the official-only judgement.
    body_no_events = re.sub(
        r'<section[^>]*\bid="events".*?</section>', "", body, flags=re.S)
    fact_domains: Counter = Counter()
    for match in re.finditer(r'href="(https?://[^"/]+)', body_no_events):
        fact_domains[match.group(1).split("//", 1)[1]] += 1

    card_total = classes.get("evidence-card", 0)
    card_anatomy = {}
    if card_total:
        # The invariant is per-card completeness, not the card count.
        for part in ("evidence-lifecycle", "status-badge", "evidence-tabs",
                     "match-panel", "action-panel", "evidence-titlebar"):
            card_anatomy[part] = {
                "present": classes.get(part, 0),
                "complete": classes.get(part, 0) >= card_total,
            }

    nav_targets = set(re.findall(r'href="#([\w-]+)"', body))
    section_ids = {s["id"] for s in sections}
    # An in-page link may land on ANY id (a pursuit row targets its
    # opportunity card); orphanhood is judged against every id in the
    # document, while section link-coverage stays judged against sections.
    all_ids = set(re.findall(r'\bid="([\w-]+)"', body))

    return {
        "version": REFERENCE_CONTRACT_VERSION,
        "sections": sections,
        "section_order": [s["id"] for s in sections],
        "evidence": {
            "cards": card_total,
            "card_anatomy": card_anatomy,
            "id_links": classes.get("id-link", 0),
            "source_links": classes.get("source-link", 0),
            "source_domains": dict(href_domains.most_common()),
            "official_sources_only": all(
                d.endswith((".gov", ".mil")) or d.startswith("www.usaspending")
                for d in fact_domains),
            "fact_domains": dict(fact_domains.most_common()),
        },
        "work": {
            "drawers": classes.get("work-drawer", 0),
            "work_buttons": classes.get("work-button", 0)
                            + classes.get("show-work-link", 0),
            "inline_work": classes.get("inline-work", 0),
            "receipts": classes.get("receipt-copy", 0),
            "receipt_previews": classes.get("receipt-preview", 0),
        },
        "editing": {
            "edit_ids": len(set(re.findall(r'data-edit-id="([^"]+)"', body))),
            "toolbar": ("studio-toolbar" in classes) or ("toolbar" in css),
            "actions": sorted(set(re.findall(r'data-action="([^"]+)"', body))),
        },
        "navigation": {
            "nav_present": classes.get("pipeline-nav", 0) > 0
                           or classes.get("mmnav", 0) > 0,
            "targets": sorted(nav_targets & section_ids),
            "orphan_targets": sorted(nav_targets - all_ids),
            "unlinked_sections": sorted(section_ids - nav_targets),
        },
        "unknowns": {
            "missing_value_marks": classes.get("missing-value", 0)
                                   + classes.get("missing", 0),
            "renders_unknowns_visibly": (classes.get("missing-value", 0)
                                         + classes.get("missing", 0)) > 0,
        },
        "lifecycle": {
            "badge_kinds": sorted(k for k in
                                  ("forecast", "historical", "pending",
                                   "open", "exact", "primary")
                                  if classes.get(k, 0)),
        },
        "tokens": sorted(set(re.findall(r"--([\w-]+)\s*:", css))),
        "interactions": {
            "functions": sorted(set(re.findall(r"function\s+(\w+)", js))),
            "listeners": dict(Counter(
                re.findall(r"addEventListener\(\s*['\"](\w+)['\"]", js))),
        },
        "ticker": {"present": classes.get("ticker", 0) > 0
                              or classes.get("pursuit-tape", 0) > 0},
        "observed_class_counts": dict(classes.most_common(60)),
    }


# --------------------------------------------------------------------------- #
# Parity: benchmark contract vs candidate contract
# --------------------------------------------------------------------------- #
def parity(reference: dict, candidate: dict) -> dict:
    """Where the candidate falls short of the benchmark's STRUCTURE.

    Scale differences are not findings (4 cards vs 25 cards is the client's
    market, not a defect). A missing MECHANISM is a finding: no nav, no
    show-the-work, no visible unknowns, an incomplete card anatomy, a
    non-official source domain.
    """
    findings = []

    ref_ids = set(reference.get("section_order") or [])
    cand_ids = set(candidate.get("section_order") or [])
    missing = ref_ids - cand_ids
    if missing:
        findings.append({"gate": "sections", "severity": "major",
                         "finding": f"benchmark sections absent: {sorted(missing)}"})

    for key, label in (("nav_present", "navigation"),):
        if reference["navigation"][key] and not candidate["navigation"][key]:
            findings.append({"gate": "navigation", "severity": "major",
                             "finding": "benchmark has section navigation; candidate has none"})
    if candidate["navigation"]["orphan_targets"]:
        findings.append({"gate": "navigation", "severity": "minor",
                         "finding": f"nav targets with no section: "
                                    f"{candidate['navigation']['orphan_targets']}"})

    if reference["unknowns"]["renders_unknowns_visibly"] and \
            not candidate["unknowns"]["renders_unknowns_visibly"]:
        findings.append({"gate": "unknowns", "severity": "major",
                         "finding": "benchmark marks unknown facts visibly; "
                                    "candidate renders none"})

    ref_work = reference["work"]
    cand_work = candidate["work"]
    if (ref_work["drawers"] or ref_work["inline_work"]) and not (
            cand_work["drawers"] or cand_work["inline_work"]
            or cand_work["work_buttons"]):
        findings.append({"gate": "evidence", "severity": "major",
                         "finding": "benchmark shows its work (drawers/receipts); "
                                    "candidate has no show-the-work surface"})

    if reference["evidence"]["source_links"] and \
            not candidate["evidence"]["source_links"] and \
            not candidate["evidence"]["id_links"]:
        findings.append({"gate": "evidence", "severity": "fatal",
                         "finding": "candidate carries no source links at all"})

    bad_domains = [d for d in candidate["evidence"].get(
                       "fact_domains", candidate["evidence"]["source_domains"])
                   if not (d.endswith((".gov", ".mil")))]
    if bad_domains:
        findings.append({"gate": "evidence", "severity": "major",
                         "finding": f"non-official source domains: {bad_domains}"})

    if reference["editing"]["edit_ids"] and not candidate["editing"]["edit_ids"]:
        findings.append({"gate": "editing", "severity": "major",
                         "finding": "benchmark is editable (data-edit-id); "
                                    "candidate exposes no editable fields"})

    if reference["ticker"]["present"] and not candidate["ticker"]["present"]:
        findings.append({"gate": "visual", "severity": "minor",
                         "finding": "benchmark carries a ticker/tape; candidate does not"})

    anatomy = candidate["evidence"].get("card_anatomy") or {}
    for part, state in anatomy.items():
        if not state["complete"]:
            findings.append({"gate": "evidence", "severity": "major",
                             "finding": f"evidence cards incomplete: {part} on "
                                        f"{state['present']} of "
                                        f"{candidate['evidence']['cards']} cards"})

    return {"findings": findings,
            "passes": not any(f["severity"] in ("fatal", "major")
                              for f in findings)}


def main() -> None:  # pragma: no cover - CLI shim
    import sys
    path = Path(sys.argv[1])
    out = extract(path.read_text(encoding="utf-8"))
    print(json.dumps(out, indent=1))


if __name__ == "__main__":  # pragma: no cover
    main()
