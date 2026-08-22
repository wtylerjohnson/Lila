"""Generate the three client files from an APPROVED review packet.

WHAT THIS CLOSES. A cold NetApp run reached a PENDING review packet in 13
minutes and then stopped dead: the sweep gate needs
clients/<slug>/profile.json, capability_taxonomy.json and
engagement_scope.json, and nothing in the pipeline created them. There was no
generator. A human had to hand-author three JSON files, and only Riverbed
ever got a complete set.

FIRES ON APPROVAL, NEVER ON INTAKE. A PENDING strategy has not been read by a
human yet, and these files decide what the sweep looks for. Generating them
at intake would let an unreviewed model decision open the sweep gate. The 409
("strategy is pending, not approved") stays exactly as it is; approval is
what triggers generation, and generation is what opens the sweep gate.

WHAT IT WILL NOT DO. Seven fields cannot be derived from the packet, and this
module leaves every one of them EMPTY rather than inventing a default. They
come back in `human_required` so the approval step can ask. An invented
default here is worse than a blank: a blank stops the sweep and asks, while a
wrong exclusion list silently poisons every search for the life of the
engagement.

THE ONE THAT MATTERS MOST is capability_terms.excluded. Riverbed's set
carries 22 entries: dredging, streambank stabilization, salmon spawning,
steelhead surveys, fish hatchery. Those are POLYSEMY TRAPS, and no model
reading riverbed.com can produce them, because nothing on riverbed.com
mentions fish. They come from knowing the client's name collides with an
unrelated federal domain. That is exactly the failure that put a Commerce
sole-source to Innovasea into a Riverbed report, and it is exactly what a
model reading a marketing site cannot see.
"""

from __future__ import annotations

from datetime import date
from typing import Any, Optional

# Keyword categories intake already assigns. It labels agency and naics
# categories today and NOTHING downstream reads them, which is how
# "Department of the Navy" and "334112" reached the search vocabulary.
_CAPABILITY_CATEGORIES = {"capability", "technology"}

# Fields no review packet can supply. Each one states what a human must give
# and why the packet cannot. This list IS the contract with the approval step.
HUMAN_REQUIRED: tuple[tuple[str, str, str], ...] = (
    ("profile.capability_summary",
     "One sentence of positioning for the report header.",
     "The packet carries pursuit_strategy, which is how to SELL to the "
     "government, not what the company IS. Deriving one from the other is "
     "writing copy, not reading data."),
    ("profile.capability_terms.excluded",
     "Words that collide with this client's name or products in an unrelated "
     "federal domain.",
     "THE POLYSEMY LIST. Riverbed needs dredging, salmon spawning and "
     "steelhead surveys; nothing on riverbed.com mentions fish. A model "
     "reading a marketing site cannot know the name collides, and this is "
     "the exact gap that put an aquaculture sole-source into a report."),
    ("profile.capability_terms.adjacent",
     "Terms that contribute relevance but are never sufficient alone.",
     "The packet splits NAICS into core and boundary but gives keywords no "
     "such split, so there is nothing to read."),
    ("taxonomy.core[].mode",
     "exact_phrase, stemmed or acronym, per term. Until these are set the "
     "taxonomy's core list stays EMPTY (the schema has no null option, so a "
     "placeholder would make the file unloadable); the terms wait in "
     "core_pending_mode.",
     "A matching decision. 'Riverbed' must be exact or it matches waterways; "
     "'observability' should stem. The packet has no notion of either."),
    ("taxonomy.code_universe.psc",
     "Product service codes that bound this client's work.",
     "The packet carries no PSC field at all. Intake never asks for one."),
    ("scope.preset",
     "civilian, all_federal, or another engagement preset.",
     "search_scope is null in the packet. This encodes the ENGAGEMENT, which "
     "data cannot determine: it is who the client sold you on covering."),
    ("scope.remove",
     "Departments to exclude from the preset.",
     "An operator decision, never a derived one."),
)


def _keyword_terms(strategy: dict) -> list[dict]:
    out = []
    for item in strategy.get("keywords") or []:
        if isinstance(item, str):
            out.append({"term": item, "category": None})
        elif isinstance(item, dict) and item.get("term"):
            out.append({"term": item["term"], "category": item.get("category")})
    return out


def derive_capability_terms(strategy: dict, *, vendor: str) -> tuple[list, dict]:
    """Capability terms that survive routing, plus what routing removed.

    The router is the same one the lanes use. Running it HERE means a brand
    name, an agency name or a bare NAICS code never reaches the profile in
    the first place, rather than being caught downstream every time.
    """
    from agents.decisions.term_routing import route_term

    entities = strategy.get("research_entities") or []
    names = [e.get("name") for e in entities if isinstance(e, dict) and e.get("name")]
    kept: list[str] = []
    removed: list[dict] = []
    for row in _keyword_terms(strategy):
        term, category = row["term"], row["category"]
        bucket, reason = route_term(term, entity_names=names + [vendor])
        if bucket == "capability" and (
                category is None or category in _CAPABILITY_CATEGORIES):
            if term not in kept:
                kept.append(term)
        else:
            removed.append({"term": term, "bucket": bucket, "reason": reason,
                            "intake_category": category})
    return kept, {"removed": removed, "kept": len(kept)}


def generate(packet: dict, *, as_of: Optional[str] = None) -> dict:
    """(files, human_required, routing) for an approved packet.

    Never raises on a thin packet: a missing section yields an empty list and
    the field turns up in human_required. The caller decides what to do with
    a file that is not yet complete; this module only reports.
    """
    strategy = packet.get("strategy") or {}
    client = (strategy.get("client_name") or packet.get("client_name") or "").strip()
    slug = "".join(c if c.isalnum() else "_" for c in client.lower()).strip("_")
    stamp = as_of or date.today().isoformat()

    core, routing = derive_capability_terms(strategy, vendor=client)

    entities = [e for e in (strategy.get("research_entities") or [])
                if isinstance(e, dict) and e.get("name")]
    rivals = [client] + [e["name"] for e in entities
                         if e.get("kind") in ("product", "competitor")]

    naics = [str(c) for c in (strategy.get("inferred_naics") or []) if c]
    agencies = [a for a in (strategy.get("target_agencies") or []) if a]

    profile = {
        "client_name": client,
        # EMPTY BY DESIGN. See HUMAN_REQUIRED.
        "capability_summary": "",
        "capability_terms": {
            "core": core,
            "adjacent": [],
            "excluded": [],
        },
        "named_competitors_and_incumbents": list(dict.fromkeys(rivals)),
        "mission_components": agencies,
        "naics_boundary": naics,
    }

    taxonomy = {
        "client_name": slug,
        "version": "1",
        "updated": stamp,
        "generated_from": "approved review packet",
        # EMPTY until a human assigns modes. CapabilityTaxonomy requires
        # mode to be exact_phrase, stemmed or acronym; there is no null
        # option, so emitting a placeholder makes the whole file unloadable
        # and takes the taxonomy down with it. The terms are not lost: they
        # are in profile.capability_terms.core, and they are listed below.
        "core": [],
        "core_pending_mode": core,
        "adjacent": [],
        "exclude": [],
        "code_universe": {"naics": naics, "psc": []},
    }

    scope = {
        "_comment": (f"GENERATED {stamp} from the approved packet. preset is "
                     "EMPTY and must be set by the operator before a sweep: "
                     "it encodes the engagement, which data cannot determine."),
        "preset": "",
        "remove": [],
    }

    missing = []
    for field, needs, why in HUMAN_REQUIRED:
        missing.append({"field": field, "needs": needs, "why": why})

    return {
        "slug": slug,
        "files": {"profile": profile, "taxonomy": taxonomy, "scope": scope},
        "human_required": missing,
        "routing": routing,
        "sweep_ready": False,   # never, straight out of the generator
        "sweep_blocked_by": [f["field"] for f in missing
                             if f["field"] in ("profile.capability_summary",
                                               "scope.preset")],
    }


def client_dir(slug: str, root: Optional[str] = None) -> str:
    import os
    base = root or os.environ.get("LILA_CLIENTS_DIR") or os.path.join(
        os.path.dirname(os.path.dirname(os.path.dirname(
            os.path.abspath(__file__)))), "clients")
    return os.path.join(base, slug)


_FILENAMES = {"profile": "profile.json",
              "taxonomy": "capability_taxonomy.json",
              "scope": "engagement_scope.json"}


def write_generated(packet: dict, *, root: Optional[str] = None,
                    as_of: Optional[str] = None) -> dict:
    """Write the three files for an approved packet. NEVER OVERWRITES.

    A hand-authored file is the more trustworthy artifact: Riverbed's
    exclusion list took a human who knew the name collides with waterways,
    and regenerating over it would silently delete 22 polysemy guards. An
    existing file is left exactly as it is and reported as kept.
    """
    import json
    import os

    out = generate(packet, as_of=as_of)
    target = client_dir(out["slug"], root)
    os.makedirs(target, exist_ok=True)
    written, kept = [], []
    for kind, payload in out["files"].items():
        path = os.path.join(target, _FILENAMES[kind])
        if os.path.exists(path):
            kept.append(_FILENAMES[kind])
            continue
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(payload, fh, indent=2)
            fh.write("\n")
        os.replace(tmp, path)
        written.append(_FILENAMES[kind])
    return {"slug": out["slug"], "dir": target,
            "written": written, "kept_existing": kept,
            "human_required": out["human_required"],
            "routing_removed": len(out["routing"]["removed"]),
            "sweep_ready": False,
            "sweep_blocked_by": out["sweep_blocked_by"]}
