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
from pathlib import Path
from typing import Any, Optional

EVIDENCE_CLASSES = (
    "client_historical", "competitive_historical", "current_opportunity",
    "forecast", "event", "excluded", "ambiguous")
ROUTE_RELATIONSHIPS = (
    "direct", "incumbent", "named_partner_teaming",
    "possible_subcontracting", "unknown")
WINDOW_STATES = ("live", "fy_only", "unstated", "stated_past")
SERVICE_FITS = ("direct", "adjacent", "unrelated", "ambiguous")
PROVENANCE_TYPES = ("measured", "cited", "inferred")
CLASSIFIER_VERSION = "evidence-route-v3-native-notices"

_ROOT = Path(__file__).resolve().parents[2]

#: Set-aside access rules the named client cannot pursue directly unless
#: its profile carries the matching certification. Conservative: only the
#: certifications the profile affirmatively states unlock these.
_RESTRICTED_ACCESS = {
    "sba": "small business set-aside",
    "8a": "8(a) set-aside",
    "8an": "8(a) sole source",
    "wosb": "women-owned small business set-aside",
    "edwosb": "economically disadvantaged WOSB set-aside",
    "sdvosbc": "service-disabled veteran-owned set-aside",
    "sdvosbs": "SDVOSB sole source",
    "vsa": "veteran-owned set-aside",
    "hzc": "HUBZone set-aside",
    "hzs": "HUBZone sole source",
    "iee": "Indian economic enterprise set-aside",
    "isbee": "Indian small business economic enterprise set-aside",
}

_INCUMBENT_RE = re.compile(
    r"\b([A-Z][A-Za-z&.,'\- ]{2,40}?)\s+(?:has|have)\s+(?:provided|"
    r"performed|supported|been providing|been performing)\b")


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
        add(str(name), "competitor", "operator route facts")
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
        out[_norm(name)] = "operator route facts: validated competitor"
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
                   ("title", "description", "relevance_matched")).casefold()
    for term in scope_terms:
        if term and term.casefold() in hay:
            return term
    for stem in _scope_stems(scope_terms):
        if re.search(r"\b" + re.escape(stem), hay):
            return stem
    return None


def _record_text(record: dict) -> str:
    return " ".join(str(record.get(k) or "") for k in (
        "title", "description", "relevance_matched", "requirement",
        "additional_info")).casefold()


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


def _signal_token(value: str) -> str:
    """Normalize procurement wording without inventing client vocabulary."""
    token = value.casefold()
    if token.startswith("interpret"):
        return "interpret"
    if token.startswith("translat"):
        return "translate"
    if token.endswith("ies") and len(token) > 5:
        return token[:-3] + "y"
    if token.endswith("s") and len(token) > 4:
        return token[:-1]
    return token


def _signal_tokens(value: Any) -> list[str]:
    return [
        _signal_token(word)
        for word in re.findall(r"[a-z]+", str(value or "").casefold())
        if word not in _SIGNAL_STOPWORDS
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
        for left_index, left in enumerate(tokens):
            for right in tokens[left_index + 1:]:
                if left != right:
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
                "fit_basis": f"negative category signal '{reason}'"}

    # Instructor is a generic occupation, not a category signal.  Require a
    # second domain-bearing token derived from the approved client profile
    # before any broader core phrase can promote the record.
    if re.search(r"\binstructors?\b", text):
        text_tokens = set(_signal_tokens(text))
        domain_tokens = set(ctx.get("instructor_domain_tokens") or [])
        if not text_tokens.intersection(domain_tokens):
            return {"service_fit": "unrelated",
                    "fit_basis":
                        "instructor scope lacks an approved domain token"}

    core = _phrase_match(text, ctx.get("core_terms") or [])
    if core:
        return {"service_fit": "direct",
                "fit_basis": f"approved core capability phrase '{core}'"}

    core_pair = _pair_match(
        text, ctx.get("core_signal_pairs") or [])
    if core_pair:
        signal = " + ".join(core_pair)
        return {"service_fit": "direct",
                "fit_basis":
                    f"approved core capability token pair '{signal}'"}

    adjacent = _phrase_match(text, ctx.get("adjacent_terms") or [])
    if adjacent:
        return {"service_fit": "adjacent",
                "fit_basis": f"approved adjacent capability phrase '{adjacent}'"}

    adjacent_pair = _pair_match(
        text, ctx.get("adjacent_signal_pairs") or [])
    if adjacent_pair:
        signal = " + ".join(adjacent_pair)
        return {"service_fit": "adjacent",
                "fit_basis":
                    f"approved adjacent capability token pair '{signal}'"}

    broad = _scope_match(record, ctx.get("scope_terms") or [])
    if broad:
        return {"service_fit": "ambiguous",
                "fit_basis": f"broad capability stem '{broad}' requires review"}
    return {"service_fit": "unrelated",
            "fit_basis": "no approved client capability signal in record scope"}


def classify_window(record: dict, ctx: dict) -> dict:
    """Normalize the record clock independently of fit and route."""
    lane = str(record.get("lane") or "")
    as_of = str(ctx.get("as_of") or "")[:10]
    if lane == "L1_notice":
        date = str(record.get("response_deadline") or "")[:10]
        if not date:
            return {"window_state": "unstated",
                    "window_basis": "notice response date not published"}
        if as_of and date < as_of:
            return {"window_state": "stated_past",
                    "window_basis": f"published response date {date} is past"}
        return {"window_state": "live",
                "window_basis": f"published response date {date}"}
    if lane == "L4_forecast":
        date = str(record.get("release_date") or
                   record.get("estimated_release_date") or
                   record.get("response_deadline") or "")[:10]
        fiscal = str(record.get("fiscal_year") or record.get("fy") or "").strip()
        if date:
            if as_of and date < as_of:
                return {"window_state": "stated_past",
                        "window_basis": f"published forecast date {date} is past"}
            return {"window_state": "live",
                    "window_basis": f"published forecast date {date}"}
        if fiscal:
            return {"window_state": "fy_only",
                    "window_basis": f"forecast states {fiscal} only"}
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
        return {"evidence_class": "excluded" if fit["service_fit"] == "unrelated" or window["window_state"] == "stated_past" else "ambiguous",
                "evidence_basis": "notice discovery only; current native source admission required"}

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
        from agents.golden_press.notice_bridge import access_decision
        return access_decision(record, ctx.get("client_certifications") or [])

    if lane == "L4_forecast":
        return {"route_relationship": "direct",
                "commercial_route": "direct", "eligible_route": True,
                "route_basis": "forecast: shape before release"}
    return {"route_relationship": "unknown", "commercial_route": "unknown",
            "eligible_route": False, "route_basis": "no route "
            "evidence for this lane"}


def _cert_tokens(set_aside_code: str) -> set[str]:
    """Certifications that unlock a restricted access rule."""
    code = set_aside_code.casefold()
    out: set[str] = set()
    if "8a" in code:
        out.add("8(a)")
    if "wosb" in code:
        out.add("wosb")
    if "sdvosb" in code or "vsa" in code:
        out.add("sdvosb")
    if "hz" in code:
        out.add("hubzone")
    if "sba" in code or "small" in code:
        out.add("small business")
    if "iee" in code:
        out.add("indian economic enterprise")
    if "isbee" in code:
        out.add("indian small business economic enterprise")
    return out


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
    if scope_terms is None:
        terms = (profile.get("capability_terms") or {})
        scope_terms = list(terms.get("core") or []) + \
            list(terms.get("adjacent") or [])
    terms = (profile.get("capability_terms") or {})
    summary = str(profile.get("capability_summary") or "")
    core_terms = list(terms.get("core") or [])
    adjacent_terms = list(terms.get("adjacent") or [])
    aliases = (pack or {}).get("client_entity_aliases") or \
        (profile.get("identity") or {}).get("aliases") or []
    entities = canonical_entities(
        client_name, pack_aliases=aliases, packet=packet,
        route_facts=route_facts)
    instructor_domain_tokens: set[str] = set()
    for phrase in core_terms:
        phrase_tokens = set(_signal_tokens(phrase))
        if "instructor" not in phrase_tokens:
            continue
        instructor_domain_tokens.update(
            phrase_tokens - {
                "instructor", "service", "support", "training", "contract",
                "course", "staff", "personnel",
            })
    return {
        "client_name": client_name,
        "client_aliases": client_aliases(
            client_name, aliases, profile),
        "validated_competitors": validated_competitors(packet, route_facts),
        "named_partners": named_partners(route_facts),
        "scope_terms": scope_terms,
        "capability_summary": summary,
        "core_terms": core_terms,
        "adjacent_terms": adjacent_terms,
        "core_signal_pairs": _derived_signal_pairs(
            core_terms),
        "instructor_domain_tokens": sorted(instructor_domain_tokens),
        "adjacent_signal_pairs": _derived_signal_pairs(adjacent_terms),
        "excluded_terms": list(terms.get("excluded") or []),
        "excluded_codes": list(terms.get("excluded_codes") or []),
        "canonical_entities": entities,
        "client_certifications":
            list(route_facts.get("certifications") or []) or
            [c for c in (profile.get("certifications") or [])],
        "as_of": as_of or str((pack or {}).get("generated_at") or ""),
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


def apply_native_admission(record: dict, notice_context=None, *, recheck=True) -> dict:
    """A cache or supplied positive overlay never replaces the current read boundary."""
    if record.get("lane") != "L1_notice":
        return dict(record)
    from agents.golden_press.notice_bridge import NoticeReadContext
    decision = (notice_context.decision(record, recheck=recheck) if isinstance(notice_context, NoticeReadContext)
                else {"version": "native-notice-admission-v1", "admitted": False,
                      "gaps": ["Current native source read context is unavailable"]})
    out = dict(record)
    out.pop("qualified", None)
    out["notice_admission"] = decision
    out["evidence_class"] = "current_opportunity" if decision["admitted"] else "ambiguous"
    out["evidence_basis"] = ("current original source, bound requirement approval, action, acquisition and access"
                             if decision["admitted"] else "; ".join(decision["gaps"]))
    if decision["admitted"]:
        source = notice_context.sources[record["record_id"]]
        raw = source.get("raw_payload") or {}
        out.update(title=source.get("title"), agency=source.get("agency"),
                   office=source.get("office") or raw.get("office"),
                   url=source.get("api_url") or source.get("url") or raw.get("url"),
                   notice_type=source.get("notice_type") or raw.get("type"),
                   response_deadline=decision["temporal"]["deadline"]["selected"]["raw"],
                   service_fit="direct", fit_basis="source-bound approved requested capability span",
                   window_state="live", window_basis="current aware source response window")
        out.update(decision["route"])
    else:
        out["discovery_service_fit"] = record.get("service_fit")
        out.update(eligible_route=False, commercial_route="unknown", route_relationship="unknown",
                   service_fit="ambiguous", fit_basis="current source qualification is withheld",
                   window_state="stated_past" if decision.get("temporal", {}).get("response_window") == "closed" else "unstated",
                   window_basis="current action clock is not established",
                   route_basis="current source access not established", qualification_state="held_for_source_review")
    return out
