"""Evidence class and route relationship: two independent dimensions
(JTG Market Map upstream order, 2026-08-20).

Every relevant record carries BOTH dimensions, classified upstream so the
renderer never reclassifies:

  evidence_class: what the record IS as evidence
    client_historical | competitive_historical | current_opportunity |
    forecast | event | excluded | ambiguous
  route_relationship: how the client REACHES the work the record points at
    direct | incumbent | named_partner_teaming | possible_subcontracting |
    unknown

The independence law: a record's route relationship never overwrites its
evidence class. classify_evidence never reads the route dimension;
classify_route never writes the class dimension. Test-pinned.

Hard rules encoded here, from the operator order:
  - the client can never classify as its own competitor: a recipient
    resolving to a client alias is client_historical by construction;
  - a teaming/access company never becomes a competitor merely because it
    holds contract paper: a provider absent from the validated-competitor
    set classifies ambiguous (receipted, excluded from competitive
    totals) even when its paper is in scope, and its teaming value rides
    the route dimension;
  - competitor validation comes from named market sources (the approved
    packet's competitor entities with rationales, or operator route
    facts), award scope, or incumbent identity: never from award
    co-occurrence alone, and never from hardcoded record ids;
  - a closed notice is never a live pursuit: current_opportunity requires
    a live response window against the pack's as-of date.
"""

from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from agents.partnering.blockers import parse_set_aside

EVIDENCE_CLASSES = (
    "client_historical", "competitive_historical", "current_opportunity",
    "forecast", "event", "excluded", "ambiguous")
ROUTE_RELATIONSHIPS = (
    "direct", "incumbent", "named_partner_teaming",
    "possible_subcontracting", "unknown")
WINDOW_STATES = ("live", "fy_only", "unstated", "stated_past")
SERVICE_FITS = ("direct", "adjacent", "unrelated", "ambiguous")
PROVENANCE_TYPES = ("measured", "cited", "inferred")
CLASSIFIER_VERSION = "evidence-route-v10-curriculum-deliverable-boundary"

_ROOT = Path(__file__).resolve().parents[2]

_INCUMBENT_RE = re.compile(
    r"\b([A-Z][A-Za-z&.,'\- ]{2,40}?)\s+(?:has|have)\s+(?:provided|"
    r"performed|supported|been providing|been performing)\b")
_INCUMBENT_IS_RE = re.compile(
    r"\bincumbent(?:\s+for\s+[^.;]{0,80})?\s+is\s+"
    r"([A-Z][A-Za-z0-9&.,'\- ]{2,80}?)(?:\s+under\s+contract|[.;])",
    flags=re.IGNORECASE,
)


def _norm(name: Any) -> str:
    return re.sub(r"[^a-z0-9]+", " ", str(name or "").casefold()).strip()


def client_aliases(client_name: str, pack_aliases: Any = None,
                   profile: Optional[dict] = None) -> set[str]:
    """Normalized client identities: pack aliases, profile identity, and
    the bare corporate stem ('JTG, inc.' resolves 'JTG, INC.' rows)."""
    out = {_norm(client_name)}
    stem = _norm(client_name).replace(" inc", "").replace(" llc", "").strip()
    if stem:
        out.add(stem)
    for alias in pack_aliases or []:
        if _norm(alias):
            out.add(_norm(alias))
    for alias in (profile or {}).get("named_competitors_and_incumbents") or []:
        pass  # profile competitor names never become client aliases
    return out


def canonical_entities(client_name: str, *, pack_aliases: Any = None,
                       packet: Optional[dict] = None,
                       route_facts: Optional[dict] = None) -> dict:
    """Canonical entity registry used by every relationship classifier.

    Entity roles are deliberately independent. A partner can only be a
    competitor when separate market evidence also names it as one. Aliases
    resolve to a canonical id before any class or route rule runs.
    """
    entities: dict[str, dict] = {}

    def add(name: str, role: str, basis: str, aliases: Any = None) -> None:
        canonical = _norm(name)
        if not canonical:
            return
        row = entities.setdefault(canonical, {
            "canonical_id": canonical.replace(" ", "_"),
            "name": name,
            "aliases": set(),
            "roles": set(),
            "role_provenance": {},
        })
        row["roles"].add(role)
        row["role_provenance"][role] = basis
        row["aliases"].add(canonical)
        for alias in aliases or []:
            if _norm(alias):
                row["aliases"].add(_norm(alias))

    client_forms = list(pack_aliases or [])
    add(client_name, "client", "client identity and approved alias set",
        client_forms)
    strategy = (packet or {}).get("strategy") or {}
    for name in _entity_names(strategy.get("research_entities"), "competitor"):
        aliases = re.findall(r"\(([^)]+)\)", name)
        bare = re.sub(r"\s*\([^)]*\)", "", name).strip()
        if bare and _norm(bare) != _norm(name):
            aliases.append(bare)
        add(name, "competitor", "approved packet market entity", aliases)
    for name in (route_facts or {}).get("validated_competitors") or []:
        display = str(name)
        forms = re.findall(r"\(([^)]+)\)", display)
        bare = re.sub(r"\s*\([^)]*\)", "", display).strip()
        if bare and _norm(bare) != _norm(display):
            forms.append(bare)
        add(display, "competitor", "operator route facts", forms)
    for name in (route_facts or {}).get("named_partners") or []:
        add(str(name), "partner", "operator named partner or teaming route")

    return {key: {
        **row,
        "aliases": sorted(row["aliases"]),
        "roles": sorted(row["roles"]),
    } for key, row in entities.items()}


def resolve_entity(name: Any, entities: dict) -> Optional[dict]:
    """Resolve an entity through canonical aliases, never display text alone."""
    normalized = _norm(name)
    if not normalized:
        return None
    stripped = _corp_strip(normalized)
    for row in entities.values():
        for alias in row.get("aliases") or []:
            if (normalized == alias or normalized.startswith(alias + " ")
                    or alias.startswith(normalized + " ")
                    or _corp_strip(alias) == stripped):
                return row
    return None


def _entity_names(entities: Any, kind: str) -> list[str]:
    out = []
    for ent in entities or []:
        if str(ent.get("kind") or "") == kind:
            name = str(ent.get("name") or "").strip()
            if name:
                out.append(name)
    return out


def validated_competitors(packet: Optional[dict] = None,
                          route_facts: Optional[dict] = None) -> dict[str, str]:
    """{normalized name -> basis}. Named market sources ONLY: the approved
    packet's competitor entities (research rationales) and operator route
    facts' competitor list. Never award co-occurrence, never ids."""
    out: dict[str, str] = {}
    strategy = (packet or {}).get("strategy") or {}
    for name in _entity_names(strategy.get("research_entities"), "competitor"):
        base = _norm(name)
        out[base] = f"approved packet research entity: {name}"
        # 'SOSi (SOS International)' must match 'SOS INTERNATIONAL LLC'
        inner = re.findall(r"\(([^)]+)\)", name)
        for form in inner:
            out[_norm(form)] = f"approved packet research entity: {name}"
        bare = re.sub(r"\s*\([^)]*\)", "", name).strip()
        if bare and _norm(bare) != base:
            out[_norm(bare)] = f"approved packet research entity: {name}"
    for name in (route_facts or {}).get("validated_competitors") or []:
        display = str(name)
        basis = f"operator route facts: validated competitor {display}"
        out[_norm(display)] = basis
        for form in re.findall(r"\(([^)]+)\)", display):
            out[_norm(form)] = basis
        bare = re.sub(r"\s*\([^)]*\)", "", display).strip()
        if bare:
            out[_norm(bare)] = basis
    return out


def named_partners(route_facts: Optional[dict] = None) -> dict[str, str]:
    """{normalized name -> basis} for teaming/access companies the
    operator or packet names. Route dimension only."""
    out: dict[str, str] = {}
    for name in (route_facts or {}).get("named_partners") or []:
        out[_norm(name)] = "operator route facts: named teaming/access company"
    return out


def load_route_facts(slug: str, root: Optional[Path] = None) -> dict:
    path = (Path(root) if root else _ROOT) / "clients" / slug / \
        "route_facts.json"
    if path.exists():
        return json.loads(path.read_text(encoding="utf-8"))
    return {}


def approved_capability_terms(packet: Optional[dict]) -> list[str]:
    """Capability terms admitted by an approved operator review packet."""
    if str((packet or {}).get("status") or "").casefold() != "approved":
        return []
    out: list[str] = []
    for row in ((packet or {}).get("strategy") or {}).get("keywords") or []:
        if str(row.get("category") or "").casefold() != "capability":
            continue
        term = str(row.get("term") or "").strip()
        if term and term.casefold() not in {item.casefold() for item in out}:
            out.append(term)
    return out


_CORP_TOKENS = ("the ",)
_CORP_SUFFIXES = (" llc", " inc", " group", " corp", " corporation")


def _corp_strip(name: str) -> str:
    """Corporate styling only: leading 'The', trailing Group/LLC/Inc.
    Never touches the distinctive identity words."""
    out = name
    for tok in _CORP_TOKENS:
        if out.startswith(tok):
            out = out[len(tok):]
    changed = True
    while changed:
        changed = False
        for suf in _CORP_SUFFIXES:
            if out.endswith(suf):
                out = out[: -len(suf)].strip()
                changed = True
    return out.strip()


def _recipient_matches(recipient: Any, names: dict[str, str] | set[str]) -> Optional[str]:
    rec = _norm(recipient)
    if not rec:
        return None
    rec_stripped = _corp_strip(rec)
    pool = names if isinstance(names, dict) else {n: "" for n in names}
    for name in pool:
        if not name:
            continue
        if rec == name or rec.startswith(name + " ") or name.startswith(rec + " "):
            return name
        # corporate styling: 'THE MISSION ESSENTIAL GROUP, LLC' resolves
        # 'Mission Essential'; identity words themselves never stripped
        if len(name) > 6 and _corp_strip(name) == rec_stripped:
            return name
        if rec_stripped and (rec_stripped == name
                             or rec_stripped.startswith(name + " ")):
            return name
    return None


_SCOPE_STEM_MIN = 10
_SCOPE_STEM_EXTRA = {"linguist", "interpreter"}


def _scope_stems(scope_terms: list[str]) -> list[str]:
    """Deterministic single-word stems for award-scope corroboration.

    Award descriptions rarely repeat a profile phrase verbatim
    ("LINGUIST SERVICES" vs "linguist support"), so scope matching also
    accepts the distinctive content words of the approved terms: words of
    ten or more characters, plus the domain stems linguist/interpreter.
    Short generics (language, training, support, services) never qualify,
    so a validated conglomerate's out-of-scope paper cannot scope-match.
    Derived from the terms, never a hardcoded list."""
    stems: list[str] = []
    for term in scope_terms:
        for word in re.findall(r"[a-z]+", str(term or "").casefold()):
            if (len(word) >= _SCOPE_STEM_MIN
                    or word in _SCOPE_STEM_EXTRA) and word not in stems:
                stems.append(word)
    return stems


def _scope_match(record: dict, scope_terms: list[str]) -> Optional[str]:
    hay = " ".join(str(record.get(k) or "") for k in
                   ("title", "description", "requirement",
                    "additional_info")).casefold()
    for term in scope_terms:
        if term and term.casefold() in hay:
            return term
    for stem in _scope_stems(scope_terms):
        if re.search(r"\b" + re.escape(stem), hay):
            return stem
    return None


def _record_text(record: dict) -> str:
    """Source-bearing requirement text, excluding retrieval query metadata."""
    return " ".join(str(record.get(k) or "") for k in (
        "title", "description", "requirement", "additional_info")).casefold()


def _phrase_match(text: str, terms: list[str]) -> Optional[str]:
    for term in sorted((str(t).strip() for t in terms if str(t).strip()),
                       key=len, reverse=True):
        if re.search(r"\b" + re.escape(term.casefold()) + r"\b", text):
            return term
    return None


_SIGNAL_STOPWORDS = {
    "a", "an", "and", "for", "from", "in", "including", "of", "or",
    "the", "to", "with",
}

# Pair matching exists only to tolerate source-language variations of an
# approved phrase.  A pair made entirely from generic procurement vocabulary
# is not a capability signal: "development services", "training team", and
# "human technology" occur across unrelated markets.  Exact approved phrases
# still match above; this guard applies only to the looser proximity rule.
_GENERIC_PAIR_TOKENS = {
    "analysis", "develop", "design", "education", "evaluate", "human",
    "implement", "management", "media", "monitoring", "open", "program",
    "project", "service", "social", "source", "support", "system", "team",
    "technology", "training", "learning",
}

# A role-bearing capability phrase cannot shed the role that gives the phrase
# its meaning. For example, "cultural advisor services" may tolerate reordered
# source wording, but "cultural services" alone is not evidence of advisory
# work. This is derived from the approved phrase rather than a record id.
_ROLE_BEARING_PAIR_TOKENS = {
    "advisor", "instructor", "interpret", "linguist",
}


def _signal_token(value: str) -> str:
    """Normalize procurement wording without inventing client vocabulary."""
    token = value.casefold()
    if token.startswith("interpret"):
        return "interpret"
    if token.startswith("translat"):
        return "translate"
    if token.startswith("design"):
        return "design"
    if token.startswith("develop"):
        return "develop"
    if token.startswith("evaluat"):
        return "evaluate"
    if token.startswith("implement"):
        return "implement"
    if token.endswith("ies") and len(token) > 5:
        return token[:-3] + "y"
    if token.endswith("s") and len(token) > 4:
        return token[:-1]
    return token


def _signal_tokens(value: Any) -> list[str]:
    return [
        _signal_token(word)
        for word in re.findall(r"[a-z]+", str(value or "").casefold())
        if word not in _SIGNAL_STOPWORDS and len(word) > 1
    ]


def _derived_signal_pairs(phrases: list[str]) -> list[tuple[str, str]]:
    """Derive unordered two-token signals from approved client phrases.

    The classifier needs to recognize source-language variations such as
    "translation and interpreting" when the approved profile says
    "translation and interpretation." The tokens still originate in the
    profile; this function does not add a vendor, record id, or capability.
    """
    pairs: set[tuple[str, str]] = set()
    for phrase in phrases:
        tokens = list(dict.fromkeys(_signal_tokens(phrase)))
        required_roles = set(tokens).intersection(_ROLE_BEARING_PAIR_TOKENS)
        for left_index, left in enumerate(tokens):
            for right in tokens[left_index + 1:]:
                if (left != right
                        and ({left, right} - _GENERIC_PAIR_TOKENS)
                        and (not required_roles
                             or {left, right}.intersection(required_roles))):
                    pairs.add(tuple(sorted((left, right))))
    return sorted(pairs)


def _pair_match(text: str, pairs: list[tuple[str, str]], *,
                max_distance: int = 8) -> Optional[tuple[str, str]]:
    tokens = _signal_tokens(text)
    positions: dict[str, list[int]] = {}
    for index, token in enumerate(tokens):
        positions.setdefault(token, []).append(index)
    for left, right in pairs:
        if left not in positions or right not in positions:
            continue
        if any(abs(a - b) <= max_distance
               for a in positions[left] for b in positions[right]):
            return left, right
    return None


def _historical_capability_receipts(
    pack: Optional[dict], aliases: set[str], capability_terms: list[str],
    term_modes: Optional[dict[str, str]] = None,
) -> list[dict]:
    """Verified client awards that corroborate terms in the approved frame."""
    receipts: list[dict] = []
    for row in (pack or {}).get("records") or []:
        if str(row.get("lane") or "") != "L2_entity_award":
            continue
        if not _recipient_matches(row.get("recipient"), aliases):
            continue
        text = _record_text(row)
        matched = None
        for term in capability_terms:
            mode = (term_modes or {}).get(term.casefold(), "stemmed")
            if (_phrase_match(text, [term])
                    or (mode != "exact_phrase" and _pair_match(
                        text, _derived_signal_pairs([term])))):
                matched = term
                break
        if not matched:
            continue
        receipts.append({
            "source_kind": "client_historical_award",
            "source_id": str(row.get("record_id") or ""),
            "source_url": str(row.get("url") or ""),
            "title": str(row.get("title") or row.get("description") or ""),
            "matched_capability": matched,
        })
    return sorted(receipts, key=lambda row: (
        row["matched_capability"].casefold(), row["source_id"]))


def incumbent_name(record: dict) -> Optional[str]:
    """Return a source-stated incumbent from supported notice sentence forms."""
    text = " ".join(str(record.get(key) or "")
                    for key in ("title", "description"))
    match = _INCUMBENT_RE.search(text) or _INCUMBENT_IS_RE.search(text)
    if not match:
        return None
    candidate = match.group(1).strip(" ,.;:-")
    normalized = _norm(candidate)
    placeholders = (
        "unknown", "tbd", "to be determined", "not identified",
        "not available", "none", "undetermined", "will be determined",
        "the contractor", "current contractor",
    )
    if (not candidate or not re.search(r"[A-Z]", candidate)
            or any(normalized == marker or normalized.startswith(marker + " ")
                   for marker in placeholders)):
        return None
    return candidate


def _fit_receipts(ctx: dict, matched: str | tuple[str, str]) -> list[dict]:
    """Return the governed frame and matching client-history receipts."""
    tokens = set(_signal_tokens(
        " ".join(matched) if isinstance(matched, tuple) else matched))
    sources = ctx.get("capability_term_sources") or {}
    matching_sources = []
    for term, source in sources.items():
        term_tokens = set(_signal_tokens(term))
        if (tokens and term_tokens
                and (tokens.issubset(term_tokens)
                     or (not isinstance(matched, tuple)
                         and term.casefold() == str(matched).casefold()))):
            matching_sources.append((term, source))
    receipts = []
    if matching_sources:
        term, source = sorted(
            matching_sources,
            key=lambda item: (-len(item[0]), item[0].casefold()),
        )[0]
        receipts.append({
            "source_kind": source.get("source_kind"),
            "source_id": source.get("source_id"),
            "title": source.get("label"),
            "matched_capability": term,
        })
    for receipt in ctx.get("historical_capability_evidence") or []:
        receipt_tokens = set(_signal_tokens(
            receipt.get("matched_capability") or ""))
        overlap = tokens.intersection(receipt_tokens)
        supports = (
            tokens.issubset(receipt_tokens)
            if isinstance(matched, tuple)
            else (len(overlap) >= 2
                  and bool(overlap - _GENERIC_PAIR_TOKENS))
        )
        if tokens and receipt_tokens and supports:
            receipts.append(dict(receipt))
    return receipts


def _curriculum_workflow_match(text: str, ctx: dict) -> Optional[str]:
    """Recognize the sourced instructional-development lifecycle.

    This rule only exists when the approved client frame states curriculum,
    e-learning, or instructional design. It then requires both an education
    object and multiple design/development/evaluation actions in the notice
    itself. Generic training or education language is never sufficient.
    """
    if not ctx.get("curriculum_workflow_enabled"):
        return None
    tokens = set(_signal_tokens(text))
    distinctive_objects = {"course", "curriculum", "instructional"}
    workflow = {"author", "design", "develop", "evaluate", "implement"}
    action_count = len(tokens.intersection(workflow))
    combined_training_education = bool(re.search(
        r"\b(?:training\s+and\s+education|education\s+and\s+training)\b",
        text,
    ))
    instructional_learning = bool(re.search(
        r"\b(?:e[- ]learning|distance\s+learning|online\s+learning)\b",
        text,
    ))
    if (((tokens.intersection(distinctive_objects) or instructional_learning)
         and action_count >= 2)
            or (combined_training_education and action_count >= 3)):
        return str(ctx.get("curriculum_workflow_basis") or
                   "curriculum and e-learning development")
    return None


def _facility_name_language_training_collision(text: str) -> bool:
    """Reject a facility name that happens to contain a capability phrase."""
    facility_name = re.search(
        r"\blanguage\s+training\s+"
        r"(?:cent(?:er|re)|school|academy|institute|facility)\b",
        text,
    )
    building_scope = re.search(
        r"\b(?:building|facilit(?:y|ies)|grounds?)\s+"
        r"(?:maintenance|repair|operations?|management|janitorial|custodial)\b"
        r"|\b(?:maintenance|repair|janitorial|custodial)\s+(?:services?\s+)?"
        r"(?:for\s+)?(?:a\s+|an\s+|the\s+)?"
        r"(?:building|facilit(?:y|ies)|grounds?)\b",
        text,
    )
    instructional_scope = re.search(
        r"\b(?:provide|deliver|conduct|teach|instruct|develop|design|"
        r"administer)\w*\b[^.;]{0,80}\blanguage\s+training\b",
        text,
    )
    return bool(facility_name and building_scope and not instructional_scope)


def _spatial_localization_collision(text: str) -> bool:
    """Distinguish language/content localization from spatial localization."""
    if not re.search(r"\blocali[sz]ation\b", text):
        return False
    language_context = re.search(
        r"\b(?:translat\w*|language\s+services?|linguistic|multilingual|"
        r"internationali[sz]ation|i18n|l10n|localized\s+content|"
        r"locali[sz]ation\s+of\s+(?:documents?|websites?|web\s+content|"
        r"software|applications?|user\s+interfaces?|resource\s+strings?|"
        r"text|media))\b",
        text,
    )
    spatial_context = re.search(
        r"\b(?:position(?:ing)?|geolocat\w*|navigation|sensors?|radar|sonar|"
        r"rfid|antennas?|tags?|iris|biometric\w*|images?|cameras?|targets?|"
        r"tracking|object\s+detection|computer\s+vision|esm)\b",
        text,
    )
    return bool(spatial_context and not language_context)


def _cultural_resource_collision(text: str) -> bool:
    """Require the advisor concept in cultural-resource procurement text."""
    resource_scope = re.search(r"\bcultural\s+resources?\b", text)
    advisory_scope = re.search(
        r"\b(?:cultural\s+(?:advis(?:or|ory)|awareness|training)|"
        r"advis(?:or|ory)\s+(?:services?|support)|cultural\s+liaison)\b",
        text,
    )
    return bool(resource_scope and not advisory_scope)


def _software_subscription_without_instructional_deliverable(text: str) -> bool:
    """Reject education-workflow software bought without content services."""
    software_product = re.search(
        r"\b(?:software|saas|platform)\b[^.;]{0,100}"
        r"\b(?:subscriptions?|licen[cs](?:e|es|ing)|renewals?)\b"
        r"|\b(?:subscriptions?|licen[cs](?:e|es|ing)|renewals?)\b"
        r"[^.;]{0,100}\b(?:software|saas|platform)\b",
        text,
    )
    instructional_deliverable = re.search(
        r"\b(?:develop|design|author|create|revise|deliver|teach|conduct|"
        r"sustain)\w*\b[^.;]{0,100}\b(?:curricul(?:um|a)|courseware|"
        r"courses?|instructional\s+(?:content|materials?|programs?)|"
        r"training\s+(?:content|curricul(?:um|a)|materials?|programs?|"
        r"courses?))\b"
        r"|\b(?:curricul(?:um|a)|courseware|instructional\s+"
        r"(?:content|materials?|programs?)|training\s+"
        r"(?:content|curricul(?:um|a)|materials?|programs?|courses?))\b"
        r"[^.;]{0,100}\b(?:develop|design|author|create|revise|deliver|"
        r"teach|conduct|sustain)\w*\b"
        r"|\b(?:curriculum|course|courseware|instructional\s+design|"
        r"training\s+delivery)\s+(?:development\s+)?services?\b",
        text,
    )
    supplier_instruction = re.search(
        r"\b(?:contractor|vendor|offeror)\s+(?:shall|will|must)\s+"
        r"(?:provide|implement)\w*\b[^.;]{0,100}\b(?:curricul(?:um|a)|"
        r"courseware|instructional\s+(?:content|materials?|programs?)|"
        r"training\s+(?:content|curricul(?:um|a)|materials?|programs?|"
        r"courses?))\b",
        text,
    )
    return bool(
        software_product
        and not instructional_deliverable
        and not supplier_instruction
    )


def _curriculum_delivery_boundary(
    text: str, ctx: dict,
) -> Optional[tuple[str, str]]:
    """Hold program-domain or inherited-IP matches without creation scope.

    Curriculum words can describe a PSC label, a named supplier's existing
    program, or licensed content. None proves that the procurement asks this
    client to design, author, revise, or maintain instructional material.
    Keep those records available for review, but do not treat a vocabulary
    collision as direct fit.
    """
    if not ctx.get("curriculum_workflow_enabled"):
        return None

    creation_deliverable = re.search(
        r"\b(?:contractor|vendor|offeror|awardee)\s+"
        r"(?:shall|will|must|is\s+required\s+to)\b[^.;]{0,140}"
        r"\b(?:develop|design|author|create|revise|maintain|evaluate)\w*\b"
        r"[^.;]{0,140}\b(?:curricul(?:um|a)|courseware|courses?|"
        r"instructional\s+(?:content|materials?|programs?))\b"
        r"|\b(?:curricul(?:um|a)|courseware|instructional\s+design)\b"
        r"[^.;]{0,80}\b(?:development|design|authoring|revision|"
        r"maintenance)\s+services?\b",
        text,
    )
    if creation_deliverable:
        return None

    administrative_label = re.search(
        r"\b(?:product\s+service\s+code|psc)\b[^.;\n]{0,140}"
        r"\btraining\s*/\s*curriculum\s+development\b",
        text,
    )
    curriculum_scope_text = (
        text[:administrative_label.start()] + text[administrative_label.end():]
        if administrative_label else text
    )
    substantive_curriculum = re.search(
        r"\b(?:develop|design|author|create|revise|maintain|evaluate)\w*\b"
        r"[^.;]{0,100}\b(?:curricul(?:um|a)|courseware|instructional\s+"
        r"(?:content|materials?))\b"
        r"|\b(?:curricul(?:um|a)|courseware|instructional\s+"
        r"(?:content|materials?))\b[^.;]{0,100}"
        r"\b(?:develop|design|author|create|revise|maintain|evaluate)\w*\b",
        curriculum_scope_text,
    )
    open_category_vehicle = re.search(
        r"\b(?:establish(?:ing)?\s+(?:a\s+|an\s+)?"
        r"(?:blanket\s+purchase\s+agreement|bpa|idiq)|"
        r"companies\s+that\s+provide\b[^.;]{0,120}\bservices\b|"
        r"offerors?\s+will\s+compete\s+at\s+the\s+call\s+level)\b",
        text,
    )
    if (administrative_label and not substantive_curriculum
            and not open_category_vehicle):
        return ("ambiguous",
            "curriculum wording appears only in an administrative PSC label; "
            "the published work does not require curriculum creation"
        )

    sole_source = re.search(
        r"\b(?:sole\s+source|only\s+one\s+responsible\s+source|"
        r"only\s+source)\b",
        text,
    )
    no_competitive_solicitation = re.search(
        r"\b(?:not\s+a\s+request\s+for\s+competitive|"
        r"no\s+solicitation\s+(?:document\s+)?(?:exists|will\s+be\s+posted)|"
        r"solicitation\s+will\s+not\s+be\s+posted)\b",
        text,
    )
    inherited_program = re.search(
        r"\b(?:uniquely\s+developed|proprietary|curriculum\s+licen[cs]ing|"
        r"existing\s+curriculum|pre[- ]existing\s+curriculum)\b",
        text,
    )
    normalized_text = _norm(text)
    client_is_named_source = any(
        len(alias) >= 3 and re.search(
            r"\b" + re.escape(alias) + r"\b", normalized_text)
        for alias in (ctx.get("client_aliases") or [])
    )
    if (sole_source and no_competitive_solicitation and inherited_program
            and not client_is_named_source):
        return ("unrelated",
            "notice seeks a named source's existing or licensed program and "
            "does not publish a curriculum-creation deliverable"
        )
    return None


def classify_service_fit(record: dict, ctx: dict) -> dict:
    """Classify technical fit before temporal or commercial promotion.

    Negative category evidence wins. An instructor record only qualifies
    when the record also carries the approved language-domain phrase. This
    blocks fitness and aerobics instructor notices without hardcoding ids.
    """
    text = _record_text(record)
    excluded = _phrase_match(text, ctx.get("excluded_terms") or [])
    naics = str(record.get("naics") or record.get("naics_code") or "")
    psc = str(record.get("psc") or record.get("psc_code") or "")
    excluded_codes = {str(v) for v in ctx.get("excluded_codes") or []}
    if excluded or naics in excluded_codes or psc in excluded_codes:
        reason = excluded or naics or psc
        return {"service_fit": "unrelated",
                "fit_basis": f"negative category signal '{reason}'",
                "fit_evidence": []}

    # Instructor is a generic occupation, not a category signal.  Require a
    # second domain-bearing token derived from the approved client profile
    # before any broader core phrase can promote the record.
    if re.search(r"\binstructors?\b", text):
        text_tokens = set(_signal_tokens(text))
        domain_tokens = set(ctx.get("instructor_domain_tokens") or [])
        if not text_tokens.intersection(domain_tokens):
            return {"service_fit": "unrelated",
                    "fit_basis":
                        "instructor scope lacks an approved domain token",
                    "fit_evidence": []}

    if _facility_name_language_training_collision(text):
        return {
            "service_fit": "unrelated",
            "fit_basis": (
                "language-training wording names a facility; the requested "
                "scope is building maintenance"),
            "fit_evidence": [],
        }

    if _spatial_localization_collision(text):
        return {
            "service_fit": "unrelated",
            "fit_basis": (
                "localization is spatial or sensor-related and lacks "
                "language/content-localization evidence"),
            "fit_evidence": [],
        }

    if _cultural_resource_collision(text):
        return {
            "service_fit": "unrelated",
            "fit_basis": (
                "cultural-resource scope lacks the approved advisor or "
                "cultural-training concept"),
            "fit_evidence": [],
        }

    if _software_subscription_without_instructional_deliverable(text):
        return {
            "service_fit": "unrelated",
            "fit_basis": (
                "software subscription or licensing scope has no "
                "instructional-content deliverable"),
            "fit_evidence": [],
        }

    curriculum_boundary = _curriculum_delivery_boundary(text, ctx)
    if curriculum_boundary:
        fit, basis = curriculum_boundary
        return {
            "service_fit": fit,
            "fit_basis": basis,
            "fit_evidence": [],
        }

    core = _phrase_match(text, ctx.get("core_terms") or [])
    if core:
        return {"service_fit": "direct",
                "fit_basis": f"approved core capability phrase '{core}'",
                "fit_evidence": _fit_receipts(ctx, core)}

    core_pair = _pair_match(
        text, ctx.get("core_signal_pairs") or [])
    if core_pair:
        signal = " + ".join(core_pair)
        return {"service_fit": "direct",
                "fit_basis":
                    f"approved core capability token pair '{signal}'",
                "fit_evidence": _fit_receipts(ctx, core_pair)}

    curriculum = _curriculum_workflow_match(text, ctx)
    if curriculum:
        return {
            "service_fit": "direct",
            "fit_basis": (
                "approved curriculum capability matches the published "
                "education design and development workflow"),
            "fit_evidence": _fit_receipts(ctx, curriculum),
        }

    adjacent = _phrase_match(text, ctx.get("adjacent_terms") or [])
    if adjacent:
        return {"service_fit": "adjacent",
                "fit_basis": f"approved adjacent capability phrase '{adjacent}'",
                "fit_evidence": _fit_receipts(ctx, adjacent)}

    adjacent_pair = _pair_match(
        text, ctx.get("adjacent_signal_pairs") or [])
    if adjacent_pair:
        signal = " + ".join(adjacent_pair)
        return {"service_fit": "adjacent",
                "fit_basis":
                    f"approved adjacent capability token pair '{signal}'",
                "fit_evidence": _fit_receipts(ctx, adjacent_pair)}

    broad = _scope_match(record, ctx.get("scope_terms") or [])
    if broad:
        return {"service_fit": "ambiguous",
                "fit_basis": f"broad capability stem '{broad}' requires review",
                "fit_evidence": _fit_receipts(ctx, broad)}
    broad_review = _phrase_match(text, ctx.get("review_terms") or [])
    if broad_review:
        return {
            "service_fit": "ambiguous",
            "fit_basis": (
                f"generic approved-domain term '{broad_review}' requires "
                "requirement-level review"),
            "fit_evidence": _fit_receipts(ctx, broad_review),
        }
    return {"service_fit": "unrelated",
            "fit_basis": "no approved client capability signal in record scope",
            "fit_evidence": []}


def _instant(value: Any) -> Optional[datetime]:
    """Normalize published temporal precision to one comparable UTC instant.

    A source date means the end of that UTC day, and a source year means the
    end of that UTC year. A timestamp must state its timezone; treating a
    naive local timestamp as UTC would invent deadline precision.
    """
    text = str(value or "").strip()
    if not text:
        return None

    if "T" in text:
        try:
            parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
        except ValueError:
            return None
        if parsed.tzinfo is None or parsed.utcoffset() is None:
            return None
        return parsed.astimezone(timezone.utc)

    parsed_date = None
    for pattern in ("%Y-%m-%d", "%m/%d/%Y", "%Y/%m/%d"):
        try:
            parsed_date = datetime.strptime(text, pattern)
            break
        except ValueError:
            continue
    if parsed_date is None and re.fullmatch(r"\d{4}", text):
        parsed_date = datetime(int(text), 12, 31)
    if parsed_date is None:
        return None
    return parsed_date.replace(
        hour=23, minute=59, second=59, microsecond=999999,
        tzinfo=timezone.utc)


def _window_is_past(deadline: Any, as_of: Any) -> bool:
    """Compare a deadline and classification clock at one UTC precision."""
    deadline_at = _instant(deadline)
    classified_at = _instant(as_of)
    return bool(
        deadline_at is not None
        and classified_at is not None
        and deadline_at < classified_at
    )


def classify_window(record: dict, ctx: dict) -> dict:
    """Normalize the record clock independently of fit and route."""
    lane = str(record.get("lane") or "")
    as_of = str(ctx.get("as_of") or "")
    if lane == "L1_notice":
        source_fields = record.get("source_fields") or {}
        active_value: Any = ""
        for candidate in (
                record.get("source_status"), record.get("active"),
                source_fields.get("active"), source_fields.get("status")):
            if candidate is not None and candidate != "":
                active_value = candidate
                break
        active = str(active_value).strip().casefold()
        notice_type = str(record.get("notice_type") or "").casefold()
        if (active_value is False
                or active in {"no", "false", "inactive", "cancelled", "canceled", "archived"}
                or any(term in notice_type
                       for term in ("cancel", "inactive", "archive"))):
            return {
                "window_state": "stated_past",
                "window_basis": "source marks the notice inactive or cancelled",
            }
        published = str(record.get("response_deadline") or "")
        if not published:
            return {"window_state": "unstated",
                    "window_basis": "notice response date not published"}
        if _instant(published) is None or _instant(as_of) is None:
            return {
                "window_state": "unstated",
                "window_basis": (
                    f"published response deadline {published} cannot be "
                    "compared without a timezone-aware classification clock"
                ),
            }
        if _window_is_past(published, as_of):
            return {"window_state": "stated_past",
                    "window_basis": f"published response deadline {published} is past"}
        return {"window_state": "live",
                "window_basis": f"published response deadline {published}"}
    if lane == "L4_forecast":
        published = str(record.get("release_date") or
                        record.get("estimated_release_date") or
                        record.get("anticipated_solicitation") or
                        record.get("anticipated_solicitation_close") or
                        record.get("response_deadline") or "")
        fiscal = str(record.get("fiscal_year") or record.get("fy") or "").strip()
        if published and _instant(published) is not None \
                and _instant(as_of) is not None:
            if _window_is_past(published, as_of):
                return {"window_state": "stated_past",
                        "window_basis": f"published forecast date {published} is past"}
            return {"window_state": "live",
                    "window_basis": f"published forecast date {published}"}
        if fiscal:
            return {"window_state": "fy_only",
                    "window_basis": f"forecast states {fiscal} only"}
        if published:
            return {
                "window_state": "unstated",
                "window_basis": (
                    f"published forecast date {published} cannot be compared "
                    "without timezone-aware temporal precision"
                ),
            }
        return {"window_state": "unstated",
                "window_basis": "forecast date not published"}
    return {"window_state": "unstated",
            "window_basis": "record class has no opportunity window"}


def _provenance(kind: str, field: str, value: Any, rule_id: str,
                confidence: str = "high") -> dict:
    if kind not in PROVENANCE_TYPES:
        raise ValueError(f"unsupported provenance kind: {kind}")
    if kind == "inferred" and confidence == "high":
        confidence = "medium"
    return {"kind": kind, "source_field": field,
            "evidence": None if value is None else str(value),
            "rule_id": rule_id, "confidence": confidence}


def classify_evidence(record: dict, ctx: dict, *, fit: Optional[dict] = None,
                      window: Optional[dict] = None) -> dict:
    """One record's evidence_class with its basis. Never reads routes."""
    lane = str(record.get("lane") or "")
    fit = fit or classify_service_fit(record, ctx)
    window = window or classify_window(record, ctx)

    if lane == "L4_forecast":
        if fit["service_fit"] == "unrelated":
            return {"evidence_class": "excluded",
                    "evidence_basis": fit["fit_basis"]}
        decay = ("; source timing is past and requires successor refresh"
                 if window["window_state"] == "stated_past" else "")
        return {"evidence_class": "forecast",
                "evidence_basis": "agency-published forecast record" + decay}
    if lane in {"event", "L5_event"}:
        return {"evidence_class": "event",
                "evidence_basis": "verified event record"}

    if lane == "L1_notice":
        if fit["service_fit"] == "unrelated":
            return {"evidence_class": "excluded",
                    "evidence_basis": fit["fit_basis"]}
        if window["window_state"] == "live":
            return {"evidence_class": "current_opportunity",
                    "evidence_basis": window["window_basis"]}
        if window["window_state"] == "unstated":
            return {"evidence_class": "ambiguous",
                    "evidence_basis": "notice carries no response date; "
                                      "held out of live pursuits until "
                                      "resolved"}
        return {"evidence_class": "excluded",
                "evidence_basis": window["window_basis"] +
                    "; never a live pursuit"}

    if lane == "L2_entity_award":
        recipient = record.get("recipient")
        hit = _recipient_matches(recipient, ctx.get("client_aliases") or set())
        if hit:
            return {"evidence_class": "client_historical",
                    "evidence_basis":
                        f"recipient resolves to client alias '{hit}'"}
        comp = _recipient_matches(recipient,
                                  ctx.get("validated_competitors") or {})
        scope = (fit.get("fit_basis") if fit["service_fit"] in
                 ("direct", "adjacent", "ambiguous") else None)
        if comp:
            basis = (ctx.get("validated_competitors") or {}).get(comp, "")
            if scope:
                return {"evidence_class": "competitive_historical",
                        "evidence_basis":
                            f"{basis}; award scope matches '{scope}'"}
            return {"evidence_class": "excluded",
                    "evidence_basis":
                        f"{basis}; but this award's scope shows no "
                        "client-capability evidence: a validated "
                        "provider's out-of-scope paper is resolved "
                        "not-competitive, never counted"}
        if scope:
            return {"evidence_class": "ambiguous",
                    "evidence_basis":
                        f"award scope matches '{scope}' but the provider "
                        "is not market-validated as a substitute; held "
                        "out of competitive totals (route value rides "
                        "the route dimension)"}
        return {"evidence_class": "excluded",
                "evidence_basis": "provider not market-validated and award "
                                  "scope shows no client-capability term "
                                  "(co-occurrence only)"}

    return {"evidence_class": "ambiguous",
            "evidence_basis": f"unrecognized lane {lane!r}"}


def classify_route(record: dict, ctx: dict) -> dict:
    """One record's route_relationship with its basis. Never writes the
    evidence class."""
    lane = str(record.get("lane") or "")
    recipient = record.get("recipient")

    if lane == "L2_entity_award":
        if _recipient_matches(recipient, ctx.get("client_aliases") or set()):
            return {"route_relationship": "direct",
                    "commercial_route": "direct",
                    "eligible_route": True,
                    "route_basis": "client's own paper"}
        partner = _recipient_matches(recipient,
                                     ctx.get("named_partners") or {})
        if partner:
            basis = (ctx.get("named_partners") or {}).get(partner, "")
            return {"route_relationship": "named_partner_teaming",
                    "commercial_route": "named_partner_teaming",
                    "eligible_route": True,
                    "route_basis": basis}
        if _recipient_matches(recipient,
                              ctx.get("validated_competitors") or {}):
            return {"route_relationship": "incumbent",
                    "commercial_route": "incumbent",
                    "eligible_route": True,
                    "route_basis": "validated competitor holding the "
                                   "paper: displacement route"}
        if _scope_match(record, ctx.get("scope_terms") or []):
            return {"route_relationship": "possible_subcontracting",
                    "commercial_route": "possible_subcontracting",
                    "eligible_route": True,
                    "route_basis": "prime holds in-scope paper; no named "
                                   "teaming evidence yet"}
        return {"route_relationship": "unknown",
                "commercial_route": "unknown", "eligible_route": False,
                "route_basis":
                "no traceable route evidence on this record"}

    if lane == "L1_notice":
        named_incumbent = incumbent_name(record)
        set_aside = str(record.get("set_aside") or "").strip()
        source_fields = record.get("source_fields") or {}
        set_aside_code = str(
            record.get("set_aside_code") or record.get("type_set_aside")
            or source_fields.get("set_aside_code")
            or source_fields.get("type_set_aside") or "").strip()
        if not set_aside and not set_aside_code:
            return {
                "route_relationship": "incumbent" if named_incumbent else "unknown",
                "commercial_route": "incumbent" if named_incumbent else "unknown",
                "eligible_route": False,
                "route_basis": (
                    ("notice names incumbent '" + named_incumbent
                     + "', but publishes no set-aside status; direct "
                       "eligibility remains unresolved")
                    if named_incumbent else
                    "notice publishes no set-aside status; direct eligibility "
                    "remains unresolved"
                ),
            }
        combined = " ".join((set_aside, set_aside_code)).casefold()
        if "sole source" in combined or "sole-source" in combined:
            return {
                "route_relationship": "incumbent" if named_incumbent else "unknown",
                "commercial_route": "incumbent" if named_incumbent else "unknown",
                "eligible_route": False,
                "route_basis": "sole-source notice is intelligence, not a direct opening",
            }
        access = parse_set_aside(set_aside, set_aside_code)
        if access and not access.get("recognized"):
            access = _route_specific_set_aside(set_aside, set_aside_code)
        if access is None:
            if named_incumbent:
                return {"route_relationship": "incumbent",
                        "commercial_route": "incumbent",
                        "eligible_route": True,
                        "route_basis": "notice states unrestricted access and "
                                       f"names incumbent '{named_incumbent}'"}
            return {"route_relationship": "direct",
                    "commercial_route": "direct", "eligible_route": True,
                    "route_basis": "notice affirmatively states unrestricted access"}
        if not access.get("recognized"):
            return {
                "route_relationship": "incumbent" if named_incumbent else "unknown",
                "commercial_route": "incumbent" if named_incumbent else "unknown",
                "eligible_route": False,
                "route_basis": f"set-aside '{set_aside or set_aside_code}' is "
                               "not recognized; eligibility requires review",
            }
        certs = {str(c).casefold().strip()
                 for c in (ctx.get("client_certifications") or [])}
        required = str(access.get("required_cert") or "small business")
        required_aliases = _certification_aliases(required)
        qualifies = bool(certs & required_aliases)
        restricted = set_aside or set_aside_code
        if not qualifies:
            if named_incumbent:
                return {"route_relationship": "named_partner_teaming",
                        "commercial_route": "named_partner_teaming",
                        "eligible_route": True,
                        "route_basis":
                            f"access rule '{restricted}' bars direct "
                            f"pursuit; incumbent text names "
                            f"'{named_incumbent}': partner or "
                            "subcontract route"}
            return {"route_relationship": "possible_subcontracting",
                    "commercial_route": "possible_subcontracting",
                    "eligible_route": False,
                    "route_basis": f"access rule '{restricted}' bars "
                                   "direct pursuit for this client"}
        if named_incumbent:
            return {"route_relationship": "incumbent",
                    "commercial_route": "incumbent",
                    "eligible_route": True,
                    "route_basis": "notice text names the incumbent: "
                                   f"'{named_incumbent}'"}
        return {"route_relationship": "direct",
                "commercial_route": "direct", "eligible_route": True,
                "route_basis": f"client attests {required}; access rule "
                               f"'{restricted}' permits a direct response"}

    if lane == "L4_forecast":
        return {"route_relationship": "direct",
                "commercial_route": "direct", "eligible_route": True,
                "route_basis": "forecast: shape before release"}
    return {"route_relationship": "unknown", "commercial_route": "unknown",
            "eligible_route": False, "route_basis": "no route "
            "evidence for this lane"}


def _certification_aliases(required: str) -> set[str]:
    """Human-readable and SAM-code forms that satisfy one access rule."""
    key = required.casefold().strip()
    aliases = {
        "8(a)": {"8(a)", "8a"},
        "hubzone": {"hubzone", "hzc", "hzs"},
        "sdvosb": {"sdvosb", "service-disabled veteran-owned small business"},
        "edwosb": {"edwosb", "economically disadvantaged wosb"},
        "wosb": {"wosb", "women-owned small business"},
        "vosb": {"vosb", "veteran-owned small business"},
        "iee": {"iee", "indian economic enterprise"},
        "isbee": {"isbee", "indian small business economic enterprise"},
        "small business": {"small business", "sba"},
    }
    return aliases.get(key, {key})


def _route_specific_set_aside(label: str, code: str) -> dict:
    """Route-only access rules not yet attested by partnering blockers."""
    text = " ".join((label, code)).upper()
    rules = (
        (("ISBEE", "INDIAN SMALL BUSINESS ECONOMIC ENTERPRISE"), "ISBEE"),
        (("IEE", "INDIAN ECONOMIC ENTERPRISE"), "IEE"),
        (("VSA", "VETERAN-OWNED SMALL BUSINESS"), "VOSB"),
    )
    for forms, required in rules:
        if any(form in text for form in forms):
            return {
                "requires_small": True,
                "required_cert": required,
                "recognized": True,
            }
    return {"requires_small": None, "required_cert": None,
            "recognized": False}


def build_context(client_name: str, slug: str, *,
                  pack: Optional[dict] = None,
                  packet: Optional[dict] = None,
                  profile: Optional[dict] = None,
                  route_facts: Optional[dict] = None,
                  scope_terms: Optional[list[str]] = None,
                  as_of: Optional[str] = None,
                  root: Optional[Path] = None) -> dict:
    """The classification context, assembled from the approved artifacts."""
    base = Path(root) if root else _ROOT
    if packet is None:
        p = base / "data" / "review" / f"{slug}.review.json"
        packet = json.loads(p.read_text(encoding="utf-8")) if p.exists() else {}
    if profile is None:
        p = base / "clients" / slug / "profile.json"
        profile = json.loads(p.read_text(encoding="utf-8")) if p.exists() else {}
    if route_facts is None:
        route_facts = load_route_facts(slug, root=base)
    taxonomy_path = base / "clients" / slug / "capability_taxonomy.json"
    taxonomy = (
        json.loads(taxonomy_path.read_text(encoding="utf-8"))
        if taxonomy_path.exists() else {})
    term_modes = {
        str(row.get("term") or "").casefold():
            str(row.get("mode") or "stemmed")
        for lane in ("core", "adjacent")
        for row in taxonomy.get(lane) or []
        if isinstance(row, dict) and str(row.get("term") or "").strip()
    }
    terms = (profile.get("capability_terms") or {})
    summary = str(profile.get("capability_summary") or "")
    core_terms = list(terms.get("core") or [])
    approved_terms = approved_capability_terms(packet)
    for term in approved_terms:
        if term.casefold() not in {item.casefold() for item in core_terms}:
            core_terms.append(term)
    adjacent_terms = list(terms.get("adjacent") or [])
    if scope_terms is None:
        scope_terms = core_terms + adjacent_terms
    aliases = (pack or {}).get("client_entity_aliases") or \
        (profile.get("identity") or {}).get("aliases") or []
    resolved_client_aliases = client_aliases(client_name, aliases, profile)
    entities = canonical_entities(
        client_name, pack_aliases=aliases, packet=packet,
        route_facts=route_facts)
    generic_instructor_tokens = {
        "contract", "course", "education", "instructor", "learning",
        "personnel", "service", "staff", "support", "team", "training",
    }
    instructor_domain_tokens: set[str] = set()
    for phrase in core_terms:
        phrase_tokens = set(_signal_tokens(phrase))
        instructor_domain_tokens.update(
            phrase_tokens - generic_instructor_tokens)
    token_counts = {}
    for phrase in core_terms:
        for token in set(_signal_tokens(phrase)):
            token_counts[token] = token_counts.get(token, 0) + 1
    review_terms = sorted(
        token for token, count in token_counts.items()
        if count >= 2 and token in {"analysis", "training"})
    curriculum_terms = [
        term for term in core_terms + adjacent_terms
        if any(marker in term.casefold()
               for marker in ("curriculum", "e-learning", "instructional design"))
    ]
    historical_receipts = _historical_capability_receipts(
        pack, resolved_client_aliases, core_terms, term_modes)
    approved_keys = {term.casefold() for term in approved_terms}
    capability_term_sources = {
        term: {
            "source_kind": (
                "operator_approved_keyword"
                if term.casefold() in approved_keys
                else "governed_client_profile"),
            "source_id": (
                f"capability-frame:{slug}:approved-review"
                if term.casefold() in approved_keys
                else f"capability-frame:{slug}:profile-v2"),
            "label": (
                "Operator-approved capability frame"
                if term.casefold() in approved_keys
                else "Governed client capability profile"),
            "match_mode": term_modes.get(term.casefold(), "stemmed"),
        }
        for term in core_terms + adjacent_terms
    }
    return {
        "client_name": client_name,
        "client_aliases": resolved_client_aliases,
        "validated_competitors": validated_competitors(packet, route_facts),
        "named_partners": named_partners(route_facts),
        "scope_terms": scope_terms,
        "capability_summary": summary,
        "core_terms": core_terms,
        "adjacent_terms": adjacent_terms,
        "capability_term_modes": {
            term: term_modes.get(term.casefold(), "stemmed")
            for term in core_terms + adjacent_terms
        },
        "core_signal_pairs": _derived_signal_pairs(
            [term for term in core_terms
             if term_modes.get(term.casefold(), "stemmed") !=
             "exact_phrase"]),
        "instructor_domain_tokens": sorted(instructor_domain_tokens),
        "review_terms": review_terms,
        "capability_frame_id": f"capability-frame:{slug}:v2",
        "capability_term_sources": capability_term_sources,
        "historical_capability_evidence": historical_receipts,
        "curriculum_workflow_enabled": bool(curriculum_terms),
        "curriculum_workflow_basis": (
            curriculum_terms[0] if curriculum_terms else ""),
        "adjacent_signal_pairs": _derived_signal_pairs(
            [term for term in adjacent_terms
             if term_modes.get(term.casefold(), "stemmed") !=
             "exact_phrase"]),
        "excluded_terms": list(terms.get("excluded") or []),
        "excluded_codes": list(terms.get("excluded_codes") or []),
        "canonical_entities": entities,
        "client_certifications":
            list(route_facts.get("certifications") or []) or
            [c for c in (profile.get("certifications") or [])],
        "as_of": as_of or str((pack or {}).get("generated_at") or "")[:10],
    }


def classify_record(record: dict, ctx: dict) -> dict:
    """Both dimensions, independently derived, additive on a copy."""
    out = dict(record)
    fit = classify_service_fit(record, ctx)
    window = classify_window(record, ctx)
    evidence = classify_evidence(record, ctx, fit=fit, window=window)
    route = classify_route(record, ctx)
    out.update(fit)
    out.update(window)
    out.update(evidence)
    out.update(route)
    resolved = resolve_entity(record.get("recipient"),
                              ctx.get("canonical_entities") or {})
    out["canonical_entity_id"] = (
        resolved.get("canonical_id") if resolved else None)
    out["canonical_entity"] = ({
        "canonical_id": resolved.get("canonical_id"),
        "name": resolved.get("name"),
        "roles": list(resolved.get("roles") or []),
        "matched_alias": _norm(record.get("recipient")),
    } if resolved else None)
    out["relationship_provenance"] = {
        "evidence_class": _provenance(
            "cited", "record", evidence["evidence_basis"],
            "evidence_class_v2"),
        "commercial_route": _provenance(
            "cited" if route.get("route_relationship") != "unknown"
            else "inferred", "record", route["route_basis"],
            "commercial_route_v2"),
        "window_state": _provenance(
            "cited" if window["window_state"] != "unstated" else "inferred",
            "response_deadline_or_forecast_date", window["window_basis"],
            "window_state_v2"),
        "service_fit": _provenance(
            "cited" if fit["service_fit"] in ("direct", "adjacent")
            else "inferred", "record_scope", fit["fit_basis"],
            "service_fit_v2"),
    }
    return out
