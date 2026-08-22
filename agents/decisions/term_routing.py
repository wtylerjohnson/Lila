"""Route every intake term into exactly one bucket, with the reason recorded.

WHY THIS EXISTS. Intake reads a vendor's marketing site and emits marketing
language as search terms. A second model reading the same site will not catch
it, because the language looks like domain vocabulary from the inside. So the
routing is mechanical and the rejections are written down.

THE DEFECT THAT FORCED IT. Intake put "SteelHead" and "Aternity" in the
CAPABILITY bucket. Capability terms are exempt from the entity guard by
design (procurement language has no vendor to anchor against), so a brand
name in that bucket walks straight past the guard built to catch it.
"SteelHead" then matched a Commerce sole-source to Innovasea, because
steelhead is a trout. A brand name must route to ENTITY.

THE FOUR BUCKETS, and what each one licenses downstream:

  entity      brand and product proper nouns. Matches alone. Subject to the
              entity and reseller guards in sam_lanes.reject_entity_hit.
  capability  procurement language a contracting officer would actually
              write. GENERATES candidates. Subject to negative-context and
              the tier rules.
  qualifier   real federal language that is context, not capability. An
              authorization attribute or policy term. QUALIFIES a hit that
              something else generated. Never generates one: "FedRAMP High"
              as a search term returns every cloud notice in the government.
  reject      never reaches search at all.

Every routing decision carries a reason string. A human reading the frame
can see what was dropped and why, which is the whole point: silent dropping
is how a frame becomes wrong without anyone noticing.
"""

from __future__ import annotations

import functools
import json
import re
from pathlib import Path
from typing import Any, Iterable, Optional

GUARDS_PATH = (Path(__file__).resolve().parents[2] / "data" / "reference"
               / "screen_guards.json")

# Characters that break a search rather than narrowing it. "NPM+" cannot be
# matched literally by a word-boundary regex and cannot be sent to an API
# query parameter without escaping that changes its meaning.
_BREAKING = re.compile(r"[+#&/\\()\[\]{}*?^$|~<>]")

# A term of this many words is a sentence, not a search term. Measured
# against the two live frames: every term at 4+ words was marketing copy
# ("Riverbed observability maintenance renewal") and none returned a hit.
SENTENCE_WORDS = 4

_AND_JOIN = re.compile(r"\band\b", re.I)

# An agency or organization name. These match thousands of notices and carry
# no capability signal whatever: NetApp's cold intake emitted "Department of
# the Navy" and "Department of Veterans Affairs" as KEYWORDS. Agency focus is
# an engagement-scope decision, not a search term.
_ORG_NAME = re.compile(
    r"\b(department of|office of|bureau of|agency for|administration|"
    r"command|headquarters|dept\.? of)\b", re.I)

# A bare code is a FILTER, not a keyword. NetApp intake emitted "334112" and
# "518210" as keywords; a NAICS belongs in the boundary, where it selects,
# not in the search, where it means nothing.
_BARE_CODE = re.compile(r"^[0-9][0-9\-\.]*$")

# A bare acronym with no expansion is context, not capability. "DISA" is an
# agency. A real product acronym routes to entity by the exact-match rule
# above, which is checked first, so this cannot swallow one.
_BARE_ACRONYM = re.compile(r"^[A-Z0-9]{2,6}$")


@functools.lru_cache(maxsize=1)
def load_guards(path: Optional[str] = None) -> dict:
    try:
        return json.loads(Path(path or GUARDS_PATH).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def _phrases(guards: dict, key: str) -> set[str]:
    block = (guards or {}).get(key) or {}
    out = set()
    for item in block.get("phrases") or []:
        text = item if isinstance(item, str) else (
            item.get("phrase") or item.get("term") or "")
        if text:
            out.add(" ".join(str(text).split()).casefold())
    return out


def _norm(text: Any) -> str:
    return " ".join(str(text or "").split()).casefold()


def route_term(term: str, *, entity_names: Iterable[str] = (),
               guards: Optional[dict] = None) -> tuple[str, str]:
    """(bucket, reason). Deterministic, order-dependent, never guesses.

    Order is load-bearing. An exact entity name is checked FIRST, so a
    product whose name happens to contain a rejectable shape still routes to
    entity: "Riverbed AppResponse" is two words and two proper nouns, and it
    is a real product, not an entity pair.
    """
    guards = guards if guards is not None else load_guards()
    raw = " ".join(str(term or "").split())
    norm = raw.casefold()
    if not raw:
        return ("reject", "empty term")

    names = {_norm(n) for n in entity_names if _norm(n)}

    # 1. IT IS AN ENTITY. Checked first, so a brand name can never be routed
    #    anywhere else no matter what shape it has.
    if norm in names:
        return ("entity", "matches a client entity name exactly")

    # 2. Two entity names welded together. "Carahsoft Riverbed" is a reseller
    #    and a vendor, and as a search term it finds neither reliably.
    contained = sorted(n for n in names
                       if n and re.search(rf"(?<![a-z0-9]){re.escape(n)}(?![a-z0-9])", norm))
    if len(contained) >= 2:
        return ("reject", f"entity pair: {' + '.join(contained[:3])}")

    # 3. An agency or organization name.
    if _ORG_NAME.search(raw):
        return ("reject", "agency or organization name: matches thousands of "
                          "notices, carries no capability signal")

    # 4. A bare numeric code. A NAICS is a filter, not a keyword.
    if _BARE_CODE.match(raw):
        return ("reject", "bare numeric code: belongs in the NAICS boundary, "
                          "not in the search vocabulary")

    # 5. A bare acronym with no expansion. Checked AFTER exact entity match,
    #    so a real product acronym has already routed to entity.
    if _BARE_ACRONYM.match(raw) and raw == raw.upper():
        return ("reject", "bare acronym with no expansion: agency or program "
                          "context, not capability")

    # 6. Search-breaking punctuation.
    if _BREAKING.search(raw):
        found = "".join(sorted(set(_BREAKING.findall(raw))))
        return ("reject", f"search-breaking punctuation {found!r}")

    # 7. A vendor's invented category. Measured at zero hits across the live
    #    extract, because no contracting officer writes it.
    if norm in _phrases(guards, "vendor_category_phrases"):
        return ("reject", "vendor category phrase, measured at zero hits")

    # 8. An unsplit compound. "WAN optimization and application acceleration"
    #    matches nothing whole and neither half was ever searched.
    if _AND_JOIN.search(raw):
        return ("reject", "unsplit compound joined by 'and'; matches nothing whole")

    # 9. Sentence-shaped.
    words = raw.split()
    if len(words) >= SENTENCE_WORDS:
        return ("reject", f"sentence-shaped ({len(words)} words)")

    # 10. A real federal attribute that qualifies rather than generates.
    if norm in _phrases(guards, "attribute_not_capability"):
        return ("qualifier", "federal attribute: qualifies a hit, never generates one")

    # 11. A brand name intake filed as capability. This is the SteelHead case:
    #    a single proper-noun token that is not procurement language. It is
    #    routed to entity so the entity guard can see it.
    if len(words) == 1 and raw[:1].isupper() and re.search(r"[a-z][A-Z]", raw):
        return ("entity", "internal capitalisation: a product name, not "
                          "procurement language")

    return ("capability", "procurement language")


def route_frame(capability_terms: Iterable[str],
                entities: Optional[dict] = None,
                *, vendor: Optional[str] = None,
                guards: Optional[dict] = None) -> dict:
    """Route a whole frame. Returns buckets plus the per-term decision list.

    The CLIENT NAME counts as an entity name here even though it never
    appears in `entities`. Without it "Carahsoft Riverbed" routes to
    capability: only "Carahsoft" is a listed entity, so the pair check sees
    one name and lets it through. The pair is exactly the shape we reject.
    """
    entity_names = [n for v in (entities or {}).values() for n in v]
    if vendor:
        entity_names.append(vendor)
    decisions = []
    buckets: dict[str, list[str]] = {
        "entity": [], "capability": [], "qualifier": [], "reject": []}
    for term in capability_terms or []:
        bucket, reason = route_term(term, entity_names=entity_names,
                                    guards=guards)
        decisions.append({"term": term, "bucket": bucket, "reason": reason,
                          "was": "capability"})
        buckets[bucket].append(term)
    for kind, names in (entities or {}).items():
        for name in names or []:
            if name in buckets["entity"]:
                continue
            bucket, reason = route_term(name, entity_names=entity_names,
                                        guards=guards)
            decisions.append({"term": name, "bucket": bucket, "reason": reason,
                              "was": f"entity/{kind}"})
            if name not in buckets[bucket]:
                buckets[bucket].append(name)
    return {"buckets": buckets, "decisions": decisions}
