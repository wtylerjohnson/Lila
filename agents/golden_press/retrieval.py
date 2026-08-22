"""Golden retrieval lanes (GOLDEN_BUILD Phase 2).

Lanes:
  L1 · SAM.gov capability notices, reused from the gate-designated sweep
       artifact (the metered search already ran there; this lane never
       re-spends quota).
  L2 · USASpending award-description search on entity names: client name,
       product names, competitor names (escalation adds resellers). Every
       entity hit passes a relevance screen before it may enter the pack.
  L3 · a flag on L2 records whose period of performance ends within 18
       months: the research clocks.
  L4 · DHS APFS forecasts matched against the client's capability profile
       keywords and NAICS (existing DhsApfsSource + match_forecasts).

Integrity rules (GOLDEN_BUILD): queries are built ONLY from generic entity
and capability terms taken from the approved strategy and research output.
Golden-reference record ids exist nowhere in this module and may never enter
a query or fetch; recall scoring lives in recall.py and runs post-retrieval.
"""

from __future__ import annotations

import json
import re
import sys
from datetime import date, datetime, timedelta, timezone
from typing import Any, Callable, Iterable, Optional

from agents.golden_press.records import (
    EvidencePack,
    GoldenRecord,
    LaneQuery,
    LaneStatus,
    Sufficiency,
)
from agents.reports.links import (
    build_sam_notice_link,
    build_usaspending_award_link,
)
from tools.api._http import get_json, post_json
from tools.api.usaspending import AWARD_SEARCH_URL, CONTRACT_TYPE_CODES
from tools.query_terms import capability_query_terms

AWARD_DETAIL_ENDPOINT = "https://api.usaspending.gov/api/v2/awards/{gid}/"

# The relevance screen's technology boundary. "riverbed" is a common noun in
# federal contracting (dredging, streambank stabilization); an entity match
# with no capability relevance is excluded and never counts toward
# sufficiency. Generic technology sector families only; nothing client-specific.
# BOTH NAICS VINTAGES, deliberately. The 2022 revision moved software
# publishing out of 511 into 513 and rebuilt the 517 telecom family, but the
# L2 award lane reaches back to 2017-01-01, so historical awards still carry
# the old codes while today's notices carry the new ones. Dropping either
# vintage silently loses records; this list covers both.
#
# Measured on the 2026-07-28 extract (78,553 active notices) when the 2022
# codes were missing:
#     513210 Software Publishers   547 notices dropped   <- 88% of the family
#     517810 All Other Telecom      91 notices dropped   <- NASA UNNO's code
#     517311 Wired Telecom Carriers 41 notices dropped
# while the 2017 equivalent we did catch, 511210, held only 77.
#
# ---------------------------------------------------------------------------
# DECISION 2026-07-28, OPERATOR: DO NOT WIDEN THIS LIST. Settled, not open.
#
# A coverage audit ranked every NAICS observed in a live federal day that sits
# OUTSIDE this boundary. Six tech-capable families were the plausible
# candidates. All six were REJECTED, with reasons, so this is not relitigated:
#
#   335999  other electrical equipment (UPS, substation gateways)
#   561621  security systems (Lenel access control)
#           -> both reintroduce exactly the polysemy classes the tier ladder
#              and the negative-context list exist to kill. Admitting them
#              undoes guard work rather than adding reach.
#
#   611430  training -> exam-prep procurement ("CompTIA Security+ prep
#           course"). Buying a class about a technology is not buying the
#           technology.
#
#   541715  R&D            (+2,304 in-corridor)
#   541611  admin/general management consulting (+1,694)
#   541990  other professional/technical services
#           -> services work. These would flood tier 3, where a single common
#              word already needs a domain anchor to survive, and the flood is
#              precisely what tier 3 was built to hold back.
#
# Size of the decision, measured: current boundary admits 12,081 of 78,553
# notices (15%). Accepting all six would admit 19,449 (25%), +7,368.
#
# THE BINDING CONSTRAINT IS THE KEYWORD FRAME, NOT THE BOUNDARY. Riverbed is
# the proof: 11,829 notices in-corridor and ZERO relevant, because the frame
# carried vendor marketing language. Widening the boundary would have raised
# 11,829 toward 19,000 and returned zero a little more expensively. Fix the
# frame; the boundary is not the problem.
# ---------------------------------------------------------------------------
TECH_NAICS_PREFIXES = (
    "3341", "3342", "3343", "3344", "3345",  # computer / comms / electronics mfg
    "4234", "4431", "4492",                  # IT equipment wholesale / retail
                                             #   (4492 is the 2022 retail code)
    "5112", "5132",                          # software publishers: 2017, 2022
    "5171", "5172", "5173", "5174",          # telecommunications carriers,
    "5178", "5179",                          #   both vintages
    "5182", "5191", "5192",                  # data processing, hosting, info svcs
    "5415",                                  # computer systems design
)
TECH_PSC_PREFIXES = (
    "70", "7A", "7B", "7C", "7D", "7E", "7F", "7G", "7H", "7J",  # IT products
    "D3", "DA", "DB", "DC", "DD", "DE", "DF", "DG", "DH", "DJ", "DK",  # IT svcs
    "58",                                                        # comms equipment
)

L2_TIME_START = "2017-01-01"      # award action window reaches the oldest
L2_PAGE_LIMIT = 100               # golden-relevant records (FY18 refresh cycle)
L2_MAX_PAGES = 2
L2_CLIENT_MAX_PAGES = 6           # the client term is the recall backbone: a
                                  # common-noun client name needs depth beyond
                                  # the top dollar ranks (amount-desc paging)
L2_FIELDS = [
    "Award ID", "Recipient Name", "Award Amount", "Description",
    "Start Date", "End Date", "Awarding Agency", "Awarding Sub Agency",
    "Contract Award Type", "NAICS", "PSC", "generated_internal_id",
    # Served by the search endpoint (verified live 2026-07-30). Without it,
    # records were RANKED AND CUT before detail enrichment ever ran, so an
    # award whose option-extended end sits inside the 18-month clock window
    # could be scored clockless and dropped from the pack - the exact
    # expiring-contract signal the clock lane exists to catch.
    "Period of Performance Potential End Date",
]
DETAIL_ENRICH_CAP = 40            # polite ceiling on per-award detail pulls
L1_PACK_CAP = 15                  # the pack is an evidence core, not a census
CLOCK_WINDOW_DAYS = 548           # 18 months
SUFFICIENCY_MIN_RECORDS = 15
SUFFICIENCY_MIN_LANES = 3

# Pack curation (dock-scale evidence core). The validator requires every pack
# record to render exactly once in the dock, so the pack is the STRONGEST
# records, not the screened universe; the screened breadth stays disclosed in
# pack.selection (no silent caps). Deterministic rule, nothing client-specific:
#   core (client/product hits)  · every corridor's largest LIVE-CLOCK row,
#                                 every seven-figure live clock, anchors for
#                                 unrepresented corridors by dollars, and any
#                                 corridor mega-anchor; then clock+dollar fill
#   competitor                  · two awards per competitor, corridor
#                                 entrenchment first, live clock next, dollars
#   channel (reseller-only)     · dollars, escalation pass only
#   forecasts                   · keyword/NAICS score order
#   notices                     · capability-hit order from the sweep screen
PACK_CORE_CAP = 26
PACK_COMPETITOR_CAP = 10
PACK_COMPETITOR_PER_TERM = 3
PACK_CHANNEL_CAP = 3
PACK_FORECAST_CAP = 8
PACK_NOTICE_CAP = 5
PACK_CLOCK_FLOOR_DOLLARS = 1_000_000.0   # every seven-figure live clock packs
PACK_MEGA_ANCHOR_DOLLARS = 5_000_000.0   # a represented corridor's mega-anchor
PACK_MEGA_ANCHOR_MULTIPLE = 5.0          # ... at 5x its clocked rep also packs
PACK_FORECAST_NICHE_SLOTS = 2            # reserved for high-relevance,
PACK_NICHE_VALUE_CEILING = 5_000_000.0   # small-dollar forecasts that
                                         # value-first ordering would drop
                                         # (operator-ratified, 2026-07-24)


def _log(msg: str) -> None:
    print(f"     [golden:retrieval] {msg}", file=sys.stderr, flush=True)


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def normalize_record_id(value: str | None) -> str:
    """Uppercase alphanumerics only: one identity per contract / notice id."""
    return re.sub(r"[^A-Z0-9]", "", str(value or "").upper())


def _award_url(gid: str | None) -> Optional[str]:
    """LINKAGE LAW: the canonical builder owns the URL form; never hand-built."""
    if not gid:
        return None
    try:
        return build_usaspending_award_link(str(gid)).url
    except ValueError:
        return None


def _notice_url(source_id: str | None, fallback: str | None) -> Optional[str]:
    """Public SAM notice form from the canonical builder; the sweep's
    workspace URL is never carried into the pack (lint_sam_workspace_links)."""
    try:
        return build_sam_notice_link(str(source_id or "")).url
    except ValueError:
        return fallback


# --------------------------------------------------------------------------- #
# Relevance screen
# --------------------------------------------------------------------------- #
def _word_hits(text: str, terms: Iterable[str]) -> list[str]:
    hits = []
    hay = " ".join(str(text or "").split()).casefold()
    for term in terms:
        needle = " ".join(str(term or "").split()).casefold()
        if len(needle) < 3:
            continue
        if re.search(r"(?<![a-z0-9])" + re.escape(needle) + r"(?![a-z0-9])", hay):
            hits.append(term)
    return hits


def screen_relevance(
    *,
    naics: str | None,
    psc: str | None,
    description: str | None,
    capability_terms: list[str],
    distinctive_names: Optional[Iterable[str]] = None,
    naics_boundary: Optional[Iterable[str]] = None,
    psc_boundary: Optional[Iterable[str]] = None,
) -> Optional[tuple[str, list[str]]]:
    """(method, matched) when the record shows capability relevance, else None.

    NAICS and PSC qualify a term hit; they never admit a record on their own.

    TERM CO-OCCURRENCE NO LONGER DOES (2026-07-28). It used to be a third
    independent route in, which meant a generic word could carry a record by
    itself, and generic words carry other industries with them. Measured on
    that day's extract: "network management" admitted a State Department
    health-insurance PROVIDER NETWORK, "network monitoring" admitted Navy
    substation protection relays, "observability" admitted Navy stealth
    research. Terms now run through the tier ladder in term_tiers, which
    requires a tech code for anything short of a distinctive product name and
    a nearby domain anchor for single common words.
    """
    # THE BOUNDARY IS THE CLIENT'S, NOT A GLOBAL TECH LIST.
    #
    # TECH_NAICS_PREFIXES is the same tuple for every client, and it encodes
    # one assumption: that a client's work is bought under technology codes.
    # apexanalytix disproves it. Intake derived their boundary as
    # 511210/541512/518210/541219/541611/541511 and the screen ignored it,
    # because 541219 (Other Accounting Services) and 541611 (Management
    # Consulting) are not "tech". Those are exactly where payment-integrity
    # work is bought. Measured: passing the client's own boundary recovers
    # "VHA-Office of Finance-Recovery Audit Services" (541219), the single
    # most on-target record in the store for that client, which the global
    # list hides completely and which reached the delivered report only
    # through the metered SAM lane.
    #
    # The operator's framing: we miss codes because we think about what the
    # client IS rather than what the government is TRYING TO FIX. The global
    # list stays as the fallback for a client with no derived boundary; it is
    # no longer the law.
    naics_prefixes = tuple(str(c).strip() for c in (naics_boundary or ())
                           if str(c).strip()) or TECH_NAICS_PREFIXES
    psc_prefixes = tuple(str(c).strip().upper() for c in (psc_boundary or ())
                         if str(c).strip()) or TECH_PSC_PREFIXES
    naics_code = re.sub(r"[^0-9]", "", str(naics or ""))
    psc_code = str(psc or "").strip().upper()
    has_naics = bool(naics_code and naics_code.startswith(naics_prefixes))
    has_psc = bool(psc_code and psc_code.startswith(psc_prefixes))
    # KEYWORDS GENERATE. NAICS QUALIFIES. (Operator directive, 2026-07-29.)
    #
    # A tech code used to admit a record on its own, and that is the single
    # largest source of noise in every report this system has produced.
    # Measured on the delivered apexanalytix report: 26 of 39 packed records
    # qualified as "tech_naics", meaning they are in a client's report because
    # they carry a software NAICS code and NOTHING matched. As the operator
    # put it: every company has a software product, so a software NAICS is
    # always "relevant" and therefore means nothing.
    #
    # A code is now a QUALIFIER, never a route in. It is still load-bearing:
    # the tier ladder below requires a tech code for any term short of a
    # distinctive product name, so the code decides which keyword hits count.
    # It just cannot manufacture a hit where none exists.
    #
    # The inverse failure is real and is why the boundary stays: dropping
    # codes entirely would let a single generic word carry an unrelated
    # industry, which is what term co-occurrence did before the tier ladder.
    from agents.golden_press.term_tiers import screen_terms_tiered
    # has_tech_code CARRIES THE REAL VALUE NOW. It was hardcoded False, and
    # that was correct while a tech code auto-passed above: reaching the
    # ladder meant you had no code. With the auto-pass gone, coded records
    # fall through to here, and telling the ladder they have no code makes
    # tiers 2 and 3 unable to ever fire. That is the NAICS-as-qualifier leg
    # of the directive: the code no longer admits a record by itself, it
    # decides which keyword hits count.
    verdicts = screen_terms_tiered(
        str(description or ""), capability_terms,
        has_tech_code=(has_naics or has_psc),
        distinctive_names=distinctive_names or ())
    if verdicts:
        return "term_cooccurrence", [v["term"] for v in verdicts][:6]
    return None


# --------------------------------------------------------------------------- #
# L2 · entity awards
# --------------------------------------------------------------------------- #
def entity_terms(strategy: Any, *, include_resellers: bool) -> list[tuple[str, str]]:
    """(kind, name) search terms from the strategy: client name always, then
    research entities. Base pass is client + products + competitors; the
    sufficiency escalation adds resellers (plan's cut order keeps L2 whole).

    Generic transform, never client-specific: a product name that carries the
    client's own name as a prefix ("<Client> <Product>") also searches the
    bare product suffix, because award descriptions routinely omit the vendor
    prefix. Suffixes under 4 characters stay out (too generic to search)."""
    client_name = " ".join(str(getattr(strategy, "client_name", "") or "").split())
    seen: set[str] = set()
    out: list[tuple[str, str]] = []

    def _add(kind: str, name: str) -> None:
        key = " ".join(str(name or "").split()).casefold()
        if len(key) < 3 or key in seen:
            return
        seen.add(key)
        out.append((kind, " ".join(str(name).split())))

    _add("client", client_name)
    kinds = ("product", "competitor") + (("reseller",) if include_resellers else ())
    for entity in getattr(strategy, "research_entities", []) or []:
        kind = getattr(entity, "kind", None) or (entity.get("kind") if isinstance(entity, dict) else None)
        name = getattr(entity, "name", None) or (entity.get("name") if isinstance(entity, dict) else None)
        if kind not in kinds or not name:
            continue
        _add(kind, name)
        if kind == "product" and client_name:
            prefix = client_name.casefold() + " "
            cleaned = " ".join(str(name).split())
            if cleaned.casefold().startswith(prefix):
                suffix = cleaned[len(prefix):].strip()
                # 4+ alphanumerics: "NPM+" normalizes to a 3-char token that
                # full-text search would match into unrelated acronyms (NPMS).
                if len(re.sub(r"[^A-Za-z0-9]", "", suffix)) >= 4:
                    _add("product", suffix)
    return out


def strip_entity_names(capability_terms: list[str],
                       entity_names: Iterable[str]) -> list[str]:
    """Capability CONTEXT terms only: entity names are search subjects, never
    relevance evidence.

    THE FAILURE THIS PREVENTS, observed live 2026-07-28. Riverbed's intake
    emitted "SteelHead" and "Aternity" as CAPABILITY terms as well as entity
    names. Capability terms are deliberately exempt from the entity guard,
    because procurement language has no vendor to anchor against, so a brand
    name sitting in the capability list walks straight past the guard that
    exists to catch it. "SteelHead" then matched a Commerce sole-source to
    Innovasea, an aquaculture buy, because steelhead is a trout.

    Shared by every lane that screens: same names removed, same way, once.
    """
    names = {" ".join(str(n).split()).casefold() for n in entity_names}
    return [t for t in capability_terms
            if " ".join(str(t).split()).casefold() not in names]


def screen_vocabulary(capability_terms: list[str], strategy: Any) -> list[str]:
    """The approved frame, WIDENED into federal phrasing, entity names out.

    WHY WIDENED (measured 2026-08-06). All eight of apexanalytix's approved
    core terms matched ZERO of 330,641 stored notices. Not narrow: dead. The
    matcher needs the contiguous literal phrase, so "improper payment
    prevention" cannot reach "prevention of improper payments" and "supplier
    onboarding" cannot reach "vendor onboarding". Marketing writes noun
    stacks; contracting officers do not. Widening recovered 3 hits to 597.

    THE APPROVED FRAME IS NOT EDITED. term_expansion adds MATCH forms of the
    operator's own terms and nothing else, and every variant still runs the
    full tiered screen, so the negative-context guards, the tech-code
    qualifier and the distinctive-name ladder all still apply. A widening
    cannot admit a record the guards would reject. Capability terms remain
    an Analyst Layer value the operator owns (L11).

    Entity names come out AFTER widening, so a brand that reaches the list
    through a synonym is stripped exactly as one in the frame would be.
    """
    from agents.golden_press.term_expansion import expand_vocabulary

    terms = list(capability_terms or [])
    # OPERATOR-APPROVED DISCOVERIES join the frame here and nowhere else.
    # term_discovery proposes federal phrasing mined from the store; a
    # proposal reaches a search only once the operator has moved it into
    # `discovered_terms_approved` on the profile, which keeps the engagement
    # boundary operator-owned while letting the corpus teach the vocabulary.
    terms.extend(_approved_discoveries(strategy))
    widened = expand_vocabulary(terms)["terms"]
    return strip_entity_names(
        widened,
        (name for _, name in entity_terms(strategy, include_resellers=True)))


def _approved_discoveries(strategy: Any) -> list[str]:
    """Discovered terms the operator has explicitly approved, or none."""
    client = " ".join(str(getattr(strategy, "client_name", "") or "").split())
    if not client:
        return []
    try:
        from pathlib import Path

        from agents.assessment_chain import canonical_slug
        root = Path(__file__).resolve().parents[2]
        path = root / "clients" / canonical_slug(client) / "profile.json"
        profile = json.loads(path.read_text(encoding="utf-8"))
    except Exception:                                     # noqa: BLE001
        return []
    return [" ".join(str(t).split())
            for t in (profile.get("discovered_terms_approved") or [])
            if str(t).strip()]


def _l2_body(term: str, *, page: int, today: date,
             sort: str = "Award Amount") -> dict:
    return {
        "filters": {
            "keywords": [term],
            "award_type_codes": list(CONTRACT_TYPE_CODES),
            "time_period": [{
                "start_date": L2_TIME_START,
                "end_date": today.isoformat(),
            }],
        },
        "fields": list(L2_FIELDS),
        "limit": L2_PAGE_LIMIT,
        "page": page,
        "sort": sort,
        "order": "desc",
    }


def _client_recipient_identity(recipient: Any, vendor: Optional[str],
                               matcher: dict) -> bool:
    """The client's own award proves itself by WHO WON IT (2026-07-30).

    The description guard exists to reject polysemy, but it also erased the
    client's own delivery record: the Osprey precedent dropped $2M+ of the
    client's proven work because the award text described the WORK without
    naming the vendor. When the recipient IS the client, recipient identity
    is stronger evidence than any description match.

    The trap this must not reopen: `_is_client_recipient` accepts a
    company-key PREFIX, so a single-word client called "Riverbed" would
    claim "RIVERBED RESTORATION LLC". A one-word client name therefore
    requires EXACT company-key equality (suffixes like INC/LLC are already
    stripped by the key), never the prefix rule; multi-word names ("Osprey
    Flight Solutions") keep the prefix rule, where the collision space is
    real companies with the same three-word name, which is identity anyway.
    Deliberately NOT gated on `matcher["distinctive"]`: that flag depends on
    a wordlist file, and a missing wordlist would silently open the trap.
    """
    if not (recipient and vendor):
        return False
    # the shared recipient-identity rule; one owner (incumbent_buyers)
    from tools.api.incumbent_buyers import _company_key, _is_client_recipient

    vendor_key = _company_key(str(vendor))
    if not vendor_key:
        return False
    if len(vendor_key.split()) >= 2:
        return _is_client_recipient(str(recipient), str(vendor))
    return _company_key(str(recipient)) == vendor_key


def run_l2(
    terms: list[tuple[str, str]],
    capability_terms: list[str],
    *,
    entities: Optional[dict[str, list[str]]] = None,
    vendor: Optional[str] = None,
    naics_boundary: Optional[list] = None,
    psc_boundary: Optional[list] = None,
    today: Optional[date] = None,
    post: Callable[..., dict] = post_json,
) -> tuple[list[GoldenRecord], list[LaneQuery]]:
    """One award-description search per entity term, screened for relevance.

    Records with no capability relevance are dropped here and never reach
    sufficiency. Every executed query is captured verbatim.
    """
    from agents.golden_press.sam_lanes import (
        _corpus_negatives, _pattern, aliases, build_matchers, is_distinctive,
        load_dictionary, reject_entity_hit,
    )

    today = today or date.today()
    records: list[GoldenRecord] = []
    queries: list[LaneQuery] = []
    entity_groups = {kind: list(names) for kind, names in (entities or {}).items()}
    if vendor:
        entity_groups.setdefault("client", []).append(vendor)
    matchers = build_matchers(entity_groups, vendor) if entity_groups else {}
    dictionary, _ = load_dictionary()
    vendor_pattern = _pattern(aliases(vendor)) if vendor else None
    for kind, term in terms:
        matcher = matchers.get(term)
        if matcher is None:
            forms = aliases(term, vendor)
            matcher = {
                "pattern": _pattern(forms),
                "kind": kind,
                "distinctive": is_distinctive(term, dictionary),
                "forms": sorted(forms),
            }
        kept = 0
        returned = 0
        seen_ids: set[str] = set()
        max_pages = L2_CLIENT_MAX_PAGES if kind == "client" else L2_MAX_PAGES
        pages_run = 0
        last_meta: dict = {}
        last_rows = 0
        failed = False

        def _consume_l2_rows(rows: list, executed_at: str) -> None:
            nonlocal kept
            for row in rows:
                # THE SEARCH TERM IS ITSELF EVIDENCE. L2 queries USASpending
                # BY ENTITY NAME, so a returned award already carries the
                # client's, a product's or a rival's name. The screen's job
                # here is to reject POLYSEMY ("riverbed" the streambank), not
                # to re-prove relevance the query already established. With
                # the NAICS auto-pass gone, omitting the term left the tier
                # ladder nothing to rank and the entire lane returned zero.
                # The term is offered to the ladder, NEVER prepended to the
                # text. Prepending guarantees a match and defeats the whole
                # point: the dredging award, which the query returned for
                # "Acme Networks", then passes on a name that appears nowhere
                # in it. The term must be found in the award's own words.
                description = str(row.get("Description") or "")
                # THE CLIENT'S OWN AWARD PROVES ITSELF BY WHO WON IT. The
                # polysemy guard erased the Osprey client's $2M delivery
                # record because the award described the WORK without naming
                # the vendor; recipient identity outranks description text.
                recipient_identity = (
                    kind == "client" and _client_recipient_identity(
                        row.get("Recipient Name"), vendor, matcher))
                if not matcher["pattern"].search(description):
                    if not recipient_identity:
                        continue

                # The entity-name side uses the exact shared guard proven in
                # L1. Ambiguous products and rivals need the client vendor in
                # the award text; resellers need the vendor or one of its
                # products. A common-word CLIENT name cannot prove itself, so
                # it proceeds through the tier ladder at its natural tier.
                vendor_present = bool(
                    vendor_pattern.search(description)) if vendor_pattern else False
                product_hits = {
                    name for name, candidate in matchers.items()
                    if candidate.get("kind") in ("product", "competitor")
                    and candidate["pattern"].search(description)
                }
                entity_cleared = bool(matcher.get("distinctive"))
                if kind != "client":
                    entity_cleared = reject_entity_hit(
                        matcher, vendor_present=vendor_present,
                        product_hits=product_hits, text=description,
                        negative_phrases=_corpus_negatives(),
                    ) is None
                    if not entity_cleared:
                        continue

                screen_terms = list(capability_terms) + [term]
                screen = screen_relevance(
                    naics=row.get("NAICS"), psc=row.get("PSC"),
                    description=description,
                    capability_terms=screen_terms,
                    distinctive_names=([term] if entity_cleared else []),
                    naics_boundary=naics_boundary, psc_boundary=psc_boundary)
                if screen is None:
                    if not recipient_identity:
                        continue
                    # kept on identity: the method says so out loud rather
                    # than borrowing a term-match method it never earned
                    method: Any = "client_recipient_identity"
                    matched: list = [str(row.get("Recipient Name") or "")]
                else:
                    method, matched = screen
                gid = row.get("generated_internal_id")
                dedupe_key = str(gid or row.get("Award ID") or "")
                if dedupe_key and dedupe_key in seen_ids:
                    continue              # already kept by an earlier pass
                if dedupe_key:
                    seen_ids.add(dedupe_key)
                records.append(GoldenRecord(
                    record_id=str(row.get("Award ID") or gid or ""),
                    lane="L2_entity_award",
                    title=str(row.get("Description") or "")[:140],
                    agency=row.get("Awarding Agency"),
                    sub_agency=row.get("Awarding Sub Agency"),
                    recipient=row.get("Recipient Name"),
                    obligated_dollars=row.get("Award Amount"),
                    period_start=row.get("Start Date"),
                    period_end=row.get("End Date"),
                    potential_end_date=row.get(
                        "Period of Performance Potential End Date"),
                    vehicle=row.get("Contract Award Type"),
                    description=row.get("Description"),
                    naics=str(row.get("NAICS") or "") or None,
                    psc=str(row.get("PSC") or "") or None,
                    url=_award_url(gid),
                    generated_internal_id=gid,
                    entity_hits=[term],
                    relevance_method=method,
                    relevance_matched=[str(m) for m in matched],
                    retrieved_at=executed_at,
                ))
                kept += 1

        for page in range(1, max_pages + 1):
            body = _l2_body(term, page=page, today=today)
            executed_at = _now_iso()
            try:
                payload = post(AWARD_SEARCH_URL, json=body)
            except Exception as exc:  # noqa: BLE001 · one term never sinks the lane
                queries.append(LaneQuery(
                    lane="L2_entity_award", method=f"entity:{kind}",
                    endpoint=AWARD_SEARCH_URL, body=body,
                    executed_at=executed_at, result_count=0,
                    kept_after_screen=0, note=f"FAILED: {exc}"))
                _log(f"L2 '{term}' page {page} failed: {exc}")
                failed = True
                break
            rows = payload.get("results", []) or []
            returned += len(rows)
            pages_run, last_rows = page, len(rows)
            _consume_l2_rows(rows, executed_at)
            queries.append(LaneQuery(
                lane="L2_entity_award", method=f"entity:{kind}",
                endpoint=AWARD_SEARCH_URL, body=body,
                executed_at=executed_at, result_count=len(rows),
                kept_after_screen=kept))
            meta = payload.get("page_metadata") or {}
            last_meta = meta
            if meta.get("hasNext") is False or len(rows) < L2_PAGE_LIMIT:
                break
        # SATURATION IS SAID OUT LOUD (2026-07-30). NetScout hit exactly
        # 200/200 and SolarWinds 200/194 on a live press and the log line
        # read as a census. Amount-desc paging means what falls off the
        # bottom is precisely the small recent task-order band where
        # renewal-cadence and displacement signals live, so a saturated
        # term also gets ONE recency page (Start Date desc) to catch what
        # the dollar sort structurally cannot.
        saturated = (not failed and pages_run == max_pages
                     and last_rows == L2_PAGE_LIMIT
                     and last_meta.get("hasNext") is not False)
        if saturated and queries:
            queries[-1].note = (
                f"SATURATED: top {returned} by Award Amount desc; "
                f"USAspending holds more matches beyond the "
                f"{max_pages}x{L2_PAGE_LIMIT} cap")
            body = _l2_body(term, page=1, today=today, sort="Start Date")
            executed_at = _now_iso()
            kept_before = kept
            try:
                payload = post(AWARD_SEARCH_URL, json=body)
                rows = payload.get("results", []) or []
            except Exception as exc:  # noqa: BLE001 · additive pass only
                rows = []
                queries.append(LaneQuery(
                    lane="L2_entity_award", method=f"entity:{kind}:recency",
                    endpoint=AWARD_SEARCH_URL, body=body,
                    executed_at=executed_at, result_count=0,
                    kept_after_screen=0, note=f"FAILED: {exc}"))
            if rows:
                returned += len(rows)
                _consume_l2_rows(rows, executed_at)
                queries.append(LaneQuery(
                    lane="L2_entity_award", method=f"entity:{kind}:recency",
                    endpoint=AWARD_SEARCH_URL, body=body,
                    executed_at=executed_at, result_count=len(rows),
                    kept_after_screen=kept - kept_before,
                    note=("recency pass after amount-desc saturation: newest "
                          "awards regardless of size, deduped against the "
                          "dollar pass")))
        _log(f"L2 '{term}' ({kind}): {returned} returned, {kept} kept"
             + (" [SATURATED; recency pass ran]" if saturated else ""))
    return records, queries


def enrich_details(
    records: list[GoldenRecord],
    *,
    get: Callable[..., dict] = get_json,
    cap: int = DETAIL_ENRICH_CAP,
) -> int:
    """Award-profile re-pull for kept L2 records: authoritative period of
    performance, obligation, ceiling, vehicle / parent IDV, full description.
    Reuses the normalize_award shape (tools/api/award_repull.py doctrine)."""
    from tools.api.award_repull import normalize_award

    enriched = 0
    for record in records:
        if record.lane != "L2_entity_award" or not record.generated_internal_id:
            continue
        if enriched >= cap:
            _log(f"detail enrichment capped at {cap}; remaining records keep "
                 "search-row fields (cap is loud, never silent)")
            break
        try:
            payload = get(AWARD_DETAIL_ENDPOINT.format(gid=record.generated_internal_id))
        except Exception as exc:  # noqa: BLE001 · enrichment is additive
            _log(f"detail pull failed for {record.record_id}: {exc}")
            continue
        row = normalize_award(record.generated_internal_id, payload,
                              retrieved_at=_now_iso())
        record.recipient = row.get("recipient") or record.recipient
        record.description = row.get("description") or record.description
        record.obligated_dollars = (
            row.get("amount") if row.get("amount") is not None
            else record.obligated_dollars)
        record.ceiling_dollars = row.get("potential_ceiling")
        record.period_start = row.get("start_date") or record.period_start
        record.period_end = row.get("end_date") or record.period_end
        record.potential_end_date = row.get("potential_end_date")
        record.naics = str(row.get("naics") or record.naics or "") or None
        record.psc = str(row.get("psc") or record.psc or "") or None
        record.set_aside = row.get("set_aside")
        # fetched since the award_repull doctrine, dropped here until the
        # 2026-07-30 audit: sole-source/8(a) direct awards never appear as
        # notices, so this field is the ONLY place that intelligence exists
        record.competition = row.get("competition")
        record.competition_code = row.get("competition_code")
        record.vehicle = (row.get("parent_vehicle_type")
                          or row.get("award_type") or record.vehicle)
        record.parent_award_id = row.get("parent_award_id")
        record.detail_enriched = True
        enriched += 1
    return enriched


def flag_clocks(records: list[GoldenRecord], *, today: Optional[date] = None) -> int:
    """L3: flag L2 records whose period of performance ends within 18 months."""
    today = today or date.today()
    horizon = today + timedelta(days=CLOCK_WINDOW_DAYS)
    flagged = 0
    for record in records:
        if record.lane != "L2_entity_award":
            continue
        end = record.period_end or record.potential_end_date
        if not end:
            continue
        try:
            end_date = date.fromisoformat(str(end)[:10])
        except ValueError:
            continue
        if today <= end_date <= horizon:
            record.research_clock = True
            flagged += 1
    return flagged


# --------------------------------------------------------------------------- #
# L1 · sweep notices
# --------------------------------------------------------------------------- #
def run_l1_from_sweep(
    sweep: dict,
    capability_terms: list[str],
    *,
    cap: int = L1_PACK_CAP,
) -> tuple[list[GoldenRecord], list[LaneQuery]]:
    """SAM notices from the gate-designated sweep artifact (provenance-checked
    per the legacy compatibility doctrine), screened by capability-term hits
    and capped to keep the pack an evidence core rather than a census."""
    rows = (sweep.get("results") or {}).get("sam.gov") or []
    eligible = [
        r for r in rows
        if isinstance(r, dict) and (r.get("source") or "sam.gov") == "sam.gov"
    ]
    scored: list[tuple[int, dict, list[str]]] = []
    for row in eligible:
        text = " ".join(str(row.get(k) or "") for k in ("title", "raw_payload"))
        hits = _word_hits(text, capability_terms)
        if hits:
            scored.append((len(hits), row, hits))
    scored.sort(key=lambda item: (-item[0], str(item[1].get("posted_date") or "")))
    kept = scored[:cap]
    if len(scored) > cap:
        _log(f"L1 capped at {cap} of {len(scored)} capability-screened notices "
             f"({len(eligible)} in sweep); cap is loud, never silent")
    records = [
        GoldenRecord(
            record_id=str(row.get("source_id") or ""),
            lane="L1_notice",
            title=str(row.get("title") or ""),
            agency=row.get("agency"),
            naics=row.get("naics_code"),
            psc=row.get("psc_code"),
            set_aside=row.get("set_aside"),
            posted_date=row.get("posted_date"),
            response_deadline=row.get("response_deadline"),
            url=_notice_url(row.get("source_id"), row.get("api_url")),
            entity_hits=hits,
            relevance_method="sweep_capability_search",
            relevance_matched=hits[:6],
            retrieved_at=str(sweep.get("generated_at") or ""),
        )
        for _, row, hits in kept
    ]
    query = LaneQuery(
        lane="L1_notice", method="sweep_reuse",
        endpoint="data/cleaned sweep artifact (SAM.gov capability search)",
        body={"capability_terms": capability_terms,
              "sweep_generated_at": sweep.get("generated_at"),
              "sweep_client": sweep.get("client")},
        executed_at=_now_iso(),
        result_count=len(eligible), kept_after_screen=len(records))
    return records, [query]


# --------------------------------------------------------------------------- #
# L4 · APFS forecasts
# --------------------------------------------------------------------------- #
_VALUE_UNIT = {"K": 1e3, "M": 1e6, "B": 1e9}


def _value_magnitude(display: str | None) -> float:
    """Rough upper dollar magnitude of an APFS dollar_range display string."""
    best = 0.0
    for amount, unit in re.findall(
            r"\$?\s*([0-9][0-9,.]*)\s*([KMB])", str(display or ""), re.I):
        try:
            best = max(best, float(amount.replace(",", ""))
                       * _VALUE_UNIT[unit.upper()])
        except ValueError:
            continue
    return best


def score_forecast(
    title: str,
    description: str,
    naics_code: str | None,
    *,
    phrases: list[str],
    unigrams: dict[str, int],
    naics_codes: list[str],
    idf: Optional[dict[str, float]] = None,
    distinctive_names: Optional[Iterable[str]] = None,
) -> tuple[float, list[str]]:
    """Tiered keywords-and-NAICS forecast screen (plan L4).

    Phrase hits weigh 3 (doubled in the title), while valid common-word hits
    carry their vocabulary-frequency and corpus-rarity weight. Tier 2 phrases
    require a client-boundary NAICS and tier 3 words require that code plus a
    nearby domain anchor. A code is a qualifier and bonus, never a route in.
    """
    from agents.golden_press.term_tiers import (
        TIER_COMMON, evaluate_term, screen_terms_tiered,
    )

    title_text = " ".join(str(title).split()).casefold()
    text = " ".join(f"{title} {description}".split()).casefold()
    idf = idf or {}
    code = re.sub(r"[^0-9]", "", str(naics_code or ""))[:6]
    naics_bonus, naics_reason = 0.0, ""
    if code and code in naics_codes:
        naics_bonus, naics_reason = 2.0, f"exact NAICS {code}"
    elif code and any(code[:4] == c[:4] for c in naics_codes):
        naics_bonus, naics_reason = 1.0, f"NAICS family {code[:4]}xx"
    has_qualifying_code = bool(naics_bonus)

    verdicts = screen_terms_tiered(
        text, phrases, has_tech_code=has_qualifying_code,
        distinctive_names=distinctive_names or ())
    phrase_hits = [
        verdict["term"] for verdict in verdicts
        if verdict["tier"] != TIER_COMMON
    ]
    common_hits = {
        verdict["term"] for verdict in verdicts
        if verdict["tier"] == TIER_COMMON
    }
    for word in unigrams:
        verdict = evaluate_term(
            word, text, has_tech_code=has_qualifying_code,
            distinctive_names=distinctive_names or ())
        if verdict and not verdict.get("rejected"):
            common_hits.add(word)
    unigram_hits = sorted(common_hits)
    title_phrases = [p for p in phrase_hits if p in title_text]
    title_unigrams = [
        word for word in unigram_hits
        if re.search(
            r"(?<![a-z0-9])" + re.escape(word) + r"(?![a-z0-9])",
            title_text,
        )
    ]

    def _uni_weight(word: str) -> float:
        return unigrams[word] * idf.get(word, 1.0)

    score = (3.0 * len(phrase_hits) + 3.0 * len(title_phrases)
             + sum(_uni_weight(w) for w in unigram_hits)
             + sum(_uni_weight(w) for w in title_unigrams)
             + naics_bonus)
    if not (phrase_hits or unigram_hits) or score < 3.0:
        return 0.0, []
    reasons = []
    if phrase_hits:
        reasons.append("keywords: " + ", ".join(sorted(phrase_hits)[:3]))
    if unigram_hits:
        reasons.append("terms: " + ", ".join(unigram_hits[:4]))
    if naics_reason:
        reasons.append(naics_reason)
    return score, reasons


def run_l4(
    capability_vocab: list[str],
    naics_codes: list[str],
    *,
    forecast_fetch: Optional[Callable[[], list]] = None,
) -> tuple[list[GoldenRecord], list[LaneQuery]]:
    """Agency procurement forecasts matching the client's keywords and NAICS
    boundary. Kept records are score-ordered with value magnitude as the
    tiebreak, so the pack cap takes the strongest programs.

    MULTIPLE ADAPTERS, ONE SCORED LANE (2026-07-30). This lane hardwired DHS APFS
    while the Acquisition Gateway census - measured that day: 7,644 records
    across Interior (3,155), USDA (2,519), VA (699), DOT (683), GSA, Labor,
    NRC, and NSF - was fetched, cached, and never read by the press: 90.7%
    of the forecast records on disk were invisible to every report. Both
    adapters now feed the same screen; each adapter gets its own LaneQuery
    row with real counts, and a disabled adapter is a named row, never a
    silent absence. Scoring is unified across the merged corpus so the IDF
    weights see one vocabulary universe, not separate source silos."""
    executed_at = _now_iso()
    queries: list[LaneQuery] = []
    all_records: list = []
    if forecast_fetch is not None:
        # Test seam: one injected fetch stands in for the whole lane.
        all_records = list(forecast_fetch())
        queries.append(LaneQuery(
            lane="L4_forecast", method="injected_fetch",
            endpoint="forecast_fetch callable (test seam)",
            body={"capability_vocab": capability_vocab,
                  "naics_codes": naics_codes},
            executed_at=executed_at, result_count=len(all_records)))
    else:
        from tools.api.forecasts import forecast_sources
        from tools.api.source_catalog import source_spec
        from tools.toggles import toggle_key

        for source in forecast_sources():
            spec = source_spec(source.name)
            endpoint = spec.official_url
            off_note = f"{toggle_key(source.name)} is off"
            if not source.enabled:
                queries.append(LaneQuery(
                    lane="L4_forecast", method=source.name,
                    endpoint=endpoint, body={"enabled": False},
                    executed_at=executed_at, note=off_note))
                continue
            try:
                fetched = source.forecasts()
            except Exception as exc:  # one forecast source never sinks L4
                queries.append(LaneQuery(
                    lane="L4_forecast", method=source.name,
                    endpoint=endpoint,
                    body={"enabled": True},
                    executed_at=executed_at,
                    note=f"source failed: {type(exc).__name__}",
                ))
                continue
            all_records.extend(fetched)
            provenance = getattr(source, "last_provenance", {}) or {}
            provenance_failed = (
                str(provenance.get("status") or "").casefold() == "failed"
            )
            queries.append(LaneQuery(
                lane="L4_forecast", method=source.name, endpoint=endpoint,
                body={"capability_vocab": capability_vocab,
                      "naics_codes": naics_codes,
                      "screen": ("tiered score_forecast: phrase*3 + anchored "
                                 "unigram + qualifying NAICS bonus, keep>=3")},
                executed_at=executed_at,
                result_count=len(fetched),
                note=("source failed: adapter provenance"
                      if provenance_failed else "")))
        if not all_records and all(
                (q.note or "").endswith("is off") for q in queries):
            return [], queries
    phrases = [" ".join(str(t).split()).casefold()
               for t in capability_vocab if len(str(t).strip()) >= 3]
    # Content unigrams weighted by how many capability phrases carry them:
    # the client's vocabulary center of mass ("network" in four phrases
    # outweighs a term that appears once).
    unigram_weight: dict[str, int] = {}
    for phrase in phrases:
        for word in set(re.findall(r"[a-z0-9]+", phrase)):
            if len(word) >= 5:
                unigram_weight[word] = unigram_weight.get(word, 0) + 1
    # Normalized corpus IDF over the live forecast pull: rare capability
    # nouns (the client's defining vocabulary) outrank ubiquitous IT words.
    import math

    corpus = [" ".join(f"{r.title or ''} {r.description or ''}".split())
              .casefold() for r in all_records]
    idf: dict[str, float] = {}
    if corpus:
        for word in unigram_weight:
            pattern = re.compile(
                r"(?<![a-z0-9])" + re.escape(word) + r"(?![a-z0-9])")
            df = sum(1 for text in corpus if pattern.search(text)) or 1
            idf[word] = math.log(len(corpus) / df) + 1.0
        mean_idf = sum(idf.values()) / len(idf) if idf else 1.0
        if mean_idf > 0:
            idf = {w: v / mean_idf for w, v in idf.items()}
    scored: list[tuple[float, float, float, Any, list[str]]] = []
    for rec in all_records:
        score, reasons = score_forecast(
            rec.title or "", rec.description or "", rec.naics_code,
            phrases=phrases, unigrams=unigram_weight,
            naics_codes=naics_codes, idf=idf)
        if score > 0:
            text = " ".join(
                f"{rec.title or ''} {rec.description or ''}".split()).casefold()
            rarity = max(
                (idf.get(w, 1.0) for w in unigram_weight
                 if re.search(r"(?<![a-z0-9])" + re.escape(w) + r"(?![a-z0-9])",
                              text)),
                default=0.0)
            scored.append((score, _value_magnitude(rec.estimated_value_range),
                           rarity, rec, reasons))
    # Value magnitude leads (the report leads with the largest programs);
    # score orders within a value tier.
    scored.sort(key=lambda item: (-item[1], -item[0], str(item[3].source_id)))
    records: list[GoldenRecord] = []
    for score, value, rarity, rec, reasons in scored:
        # Forecast identifiers are canonical only inside their publishing
        # system.  Several official adapters legitimately reuse the same
        # numeric source id, so the internal identity must carry the source
        # namespace even though the client-facing record_id remains exactly
        # as published.
        forecast_internal_id = f"{rec.source}::{rec.source_id}"
        records.append(GoldenRecord(
            record_id=str(rec.source_id),
            generated_internal_id=forecast_internal_id,
            forecast_score=round(score, 2),
            forecast_value=value,
            forecast_rarity=round(rarity, 3),
            lane="L4_forecast",
            title=rec.title,
            agency=rec.agency,
            sub_agency=rec.component,
            description=rec.description,
            naics=rec.naics_code,
            psc=rec.psc,
            vehicle=rec.award_type,
            parent_award_id=rec.predecessor_contract_id,
            set_aside=rec.set_aside,
            url=rec.url,
            estimated_value_range=rec.estimated_value_range,
            anticipated_solicitation=rec.anticipated_solicitation,
            anticipated_solicitation_close=rec.anticipated_solicitation_close,
            anticipated_award=rec.anticipated_award,
            fiscal_year=rec.fiscal_year,
            small_business_poc=rec.small_business_poc,
            source_fields=rec.source_fields,
            entity_hits=[],
            relevance_method="forecast_tiered_keyword_naics_match",
            relevance_matched=reasons[:4],
            retrieved_at=(rec.retrieved_at.strftime("%Y-%m-%dT%H:%M:%SZ")
                          if hasattr(rec.retrieved_at, "strftime")
                          else str(rec.retrieved_at)),
        ))
    # Per-adapter kept counts, so one adapter's dead screen can never hide
    # behind another's healthy one in the receipts.
    kept_by_source: dict[str, int] = {}
    for _, _, _, rec, _ in scored:
        name = str(getattr(rec, "source", "") or "injected_fetch")
        kept_by_source[name] = kept_by_source.get(name, 0) + 1
    for q in queries:
        if not (q.note or "").endswith("is off"):
            q.kept_after_screen = (
                len(records) if q.method == "injected_fetch"
                else kept_by_source.get(q.method, 0))
    return records, queries


def _forecast_lane_status(queries: list[LaneQuery]) -> str:
    """Return the pack status for the forecast adapter family.

    A successful adapter is live even when its valid result set is empty.
    The family is degraded only when no adapter ran or every enabled adapter
    failed.  Disabled adapters remain visible in the receipt but do not turn
    a successful sibling adapter into a degraded lane.
    """
    enabled = [
        query for query in queries
        if not (query.note or "").endswith("is off")
    ]
    if not enabled:
        return "degraded"
    if all((query.note or "").startswith("source failed:")
           for query in enabled):
        return "degraded"
    return "live"


# --------------------------------------------------------------------------- #
# Dedup, sufficiency, pack assembly
# --------------------------------------------------------------------------- #
def dedupe(records: list[GoldenRecord]) -> list[GoldenRecord]:
    """Unique on source-qualified identity across all lanes.

    Award and notice ids remain globally canonical.  Forecast ids are only
    canonical within an agency publisher, so L4 prefers the namespaced
    ``generated_internal_id`` assigned by :func:`run_l4`.  A repeat hit merges
    its entity terms into the kept record; detail-enriched rows win.
    """
    by_id: dict[str, GoldenRecord] = {}
    for record in records:
        if record.lane == "L4_forecast":
            key = normalize_record_id(
                record.generated_internal_id) or normalize_record_id(
                    record.record_id)
        else:
            key = normalize_record_id(record.record_id) or normalize_record_id(
                record.generated_internal_id)
        if not key:
            continue
        held = by_id.get(key)
        if held is None:
            by_id[key] = record
            continue
        keep, drop = (record, held) if (
            record.detail_enriched and not held.detail_enriched) else (held, record)
        keep.entity_hits = list(dict.fromkeys([*keep.entity_hits, *drop.entity_hits]))
        keep.research_clock = keep.research_clock or drop.research_clock
        by_id[key] = keep
    return list(by_id.values())


def apply_engagement_scope(
    records: list[GoldenRecord],
    scope: Any,
) -> tuple[list[GoldenRecord], list[GoldenRecord]]:
    """(in_scope, off_scope) under the operator's agency-universe boundary.

    Exclude-only, operator-owned; no scope config means everything passes
    (UNSCOPED, never guessed). Uses the shipped resolver so department
    aliases and subtier names classify exactly as the relevance engine's."""
    if scope is None:
        return records, []
    from tools.relevance.scope import in_scope

    inside: list[GoldenRecord] = []
    outside: list[GoldenRecord] = []
    for record in records:
        ok, _basis = in_scope(
            {"agency": record.agency,
             "awarding_sub_agency": record.sub_agency}, scope)
        (inside if ok else outside).append(record)
    if outside:
        _log(f"engagement scope dropped {len(outside)} off-scope records "
             f"(operator boundary; exclude-only)")
    return inside, outside


def _affinity(record: GoldenRecord, kind_by_name: dict[str, str]) -> str:
    """core | competitor | channel, from which entity terms surfaced the record."""
    kinds = {kind_by_name.get(hit.casefold(), "") for hit in record.entity_hits}
    if kinds & {"client", "product"}:
        return "core"
    if "competitor" in kinds:
        return "competitor"
    return "channel"


def published_floor(display: str | None) -> float:
    """The LOWEST published figure in a value-range string, or 0.

    A forecast publishes a band ("$1M to $5M"), never an obligation. The only
    defensible number to sum across bands is the lower bound, and it is a
    FLOOR: the true figure is at least this. Summing midpoints would invent a
    number nobody published, which is the fabrication this whole build removes.
    Mirrors render.forecast_floor so the pack and the page agree.
    """
    bands = re.findall(r"\$\s?([0-9][0-9,.]*)\s*([KMB])",
                       str(display or ""), re.I)
    values = []
    for amount, unit in bands:
        try:
            values.append(float(amount.replace(",", "")) * _VALUE_UNIT[unit.upper()])
        except (ValueError, KeyError):
            continue
    return min(values) if values else 0.0


def aggregate_segment(records: list[GoldenRecord], label: str,
                      *, money: str = "obligated") -> dict:
    """Aggregate one lane segment BEFORE its cap is applied.

    This is the only place a category figure can be honest. By the time a
    pack is written the selection rule has already cut the segment to its
    display cap, so a sum over pack.records is a sum over a sample. Here the
    full categorized segment still exists in memory.

    money="obligated"  awards: sum the obligated dollars actually reported.
    money="floor"      forecasts: sum the PUBLISHED LOWER BOUND of each value
                       range and label it a floor, never a point estimate.
    """
    by_fy: dict[str, int] = {}
    by_agency: dict[str, dict] = {}
    by_prime: dict[str, dict] = {}
    by_entity_hit: dict[str, int] = {}
    total = 0.0
    priced = 0

    for record in records:
        if money == "floor":
            value = published_floor(record.estimated_value_range)
        else:
            value = float(record.obligated_dollars or 0.0)
        total += value
        if value:
            priced += 1
        fy = str(record.fiscal_year or "unstated")
        by_fy[fy] = by_fy.get(fy, 0) + 1
        agency = (record.sub_agency or record.agency or "").strip()
        if agency:
            slot = by_agency.setdefault(agency, {"n": 0, "dollars": 0.0})
            slot["n"] += 1
            slot["dollars"] = round(slot["dollars"] + value, 2)
        prime = (record.recipient or "").strip()
        if prime:
            slot = by_prime.setdefault(prime, {"n": 0, "dollars": 0.0})
            slot["n"] += 1
            slot["dollars"] = round(slot["dollars"] + value, 2)
        for hit in (record.entity_hits or []):
            by_entity_hit[hit] = by_entity_hit.get(hit, 0) + 1

    money_label = ("summed published lower bounds (a FLOOR, not a total)"
                   if money == "floor" else "obligated dollars as reported")
    return {
        "segment": label,
        "count": len(records),
        "money_basis": money,
        "money_label": money_label,
        "dollars": round(total, 2),
        "records_with_dollars": priced,
        "records_without_dollars": len(records) - priced,
        "by_fiscal_year": dict(sorted(by_fy.items())),
        "by_agency": dict(sorted(by_agency.items(),
                                 key=lambda kv: -kv[1]["dollars"])),
        "by_prime": dict(sorted(by_prime.items(),
                                key=lambda kv: -kv[1]["dollars"])),
        "by_entity_hit": dict(sorted(by_entity_hit.items(),
                                     key=lambda kv: -kv[1])),
        "filter_statement": (
            f"CATEGORIZED set for {label}: {len(records)} deduplicated, "
            f"in-scope records as they entered the selection rule, before any "
            f"display cap. Counted from search-row fields, NOT "
            f"detail-enriched (enrichment runs on the packed slice only). "
            f"Not the same as the SCREENED tally, which counts per query "
            f"before deduplication and therefore double-counts a record "
            f"matched by several entity terms."),
    }


def select_pack(
    records: list[GoldenRecord],
    strategy: Any,
) -> tuple[list[GoldenRecord], dict]:
    """Deterministic dock-scale curation of the screened universe.

    Every cut is counted and disclosed in the returned selection dict; the
    screened breadth is never silently discarded (LaneQuery keeps per-query
    totals too). Rule constants documented at the top of this module."""
    kind_by_name = {"": ""}
    client = " ".join(str(getattr(strategy, "client_name", "") or "").split())
    if client:
        kind_by_name[client.casefold()] = "client"
    for kind, name in entity_terms(strategy, include_resellers=True):
        kind_by_name[name.casefold()] = kind

    l1 = [r for r in records if r.lane == "L1_notice"]
    l4 = [r for r in records if r.lane == "L4_forecast"]
    l2 = [r for r in records if r.lane == "L2_entity_award"]
    core = [r for r in l2 if _affinity(r, kind_by_name) == "core"]
    competitor = [r for r in l2 if _affinity(r, kind_by_name) == "competitor"]
    channel = [r for r in l2 if _affinity(r, kind_by_name) == "channel"]

    # PRE-CAP AGGREGATES. Computed HERE, on the full categorized segments,
    # because three lines below the caps start cutting and the qualifying set
    # stops existing. Every later stage sees only the display slice, so this
    # is the last moment an honest category figure can be taken.
    pre_cap_aggregates = {
        "basis": "categorized set, pre-cap",
        "categorized_total": len(core) + len(competitor) + len(channel)
                             + len(l4) + len(l1),
        "segments": [
            aggregate_segment(core, "L2_core"),
            aggregate_segment(competitor, "L2_competitor"),
            aggregate_segment(channel, "L2_channel"),
            aggregate_segment(l4, "L4_forecast", money="floor"),
            aggregate_segment(l1, "L1_notice"),
        ],
    }

    def _corridor(record: GoldenRecord) -> str:
        return (record.sub_agency or record.agency or "").strip()

    # Core, in priority order (the golden shape: live renewal clocks carry
    # the why-now story per corridor, big historical platforms anchor it):
    #   1. each corridor's largest LIVE-CLOCK row
    #   2. every remaining seven-figure live clock
    #   3. anchors (largest row) for corridors with no pick yet, dollars desc
    #   4. mega-anchors of already-represented corridors
    #   5. fill by clock then dollars to the cap
    def anchor_rank(r):
        return (-(r.obligated_dollars or 0.0), r.record_id)
    kept_core: list[GoldenRecord] = []
    taken: set[int] = set()

    def _take(rows: Iterable[GoldenRecord]) -> None:
        for row in rows:
            if id(row) not in taken and len(kept_core) < PACK_CORE_CAP:
                taken.add(id(row))
                kept_core.append(row)

    clocked_rep: dict[str, GoldenRecord] = {}
    for record in sorted((r for r in core if r.research_clock), key=anchor_rank):
        clocked_rep.setdefault(_corridor(record) or record.record_id, record)
    _take(sorted(clocked_rep.values(), key=anchor_rank))
    _take(sorted((r for r in core if r.research_clock
                  and (r.obligated_dollars or 0.0) >= PACK_CLOCK_FLOOR_DOLLARS
                  and id(r) not in taken), key=anchor_rank))
    # Anchors compete on dollars in ONE pool: a repped corridor's mega-anchor
    # (the historical platform buy) outranks a small unrepresented-corridor
    # anchor rather than queueing behind every one of them.
    anchor_by_corridor: dict[str, GoldenRecord] = {}
    for record in sorted(core, key=anchor_rank):
        anchor_by_corridor.setdefault(
            _corridor(record) or record.record_id, record)
    anchor_pool: list[GoldenRecord] = []
    for corridor, anchor in anchor_by_corridor.items():
        if id(anchor) in taken:
            continue
        if corridor not in clocked_rep:
            anchor_pool.append(anchor)
            continue
        dollars = anchor.obligated_dollars or 0.0
        rep_dollars = clocked_rep[corridor].obligated_dollars or 0.0
        if (dollars >= PACK_MEGA_ANCHOR_DOLLARS
                and dollars >= PACK_MEGA_ANCHOR_MULTIPLE * max(rep_dollars, 1.0)):
            anchor_pool.append(anchor)
    _take(sorted(anchor_pool, key=anchor_rank))
    _take(sorted((r for r in core if id(r) not in taken),
                 key=lambda r: (not r.research_clock,
                                -(r.obligated_dollars or 0.0), r.record_id)))

    corridors = ({_corridor(r) for r in kept_core if _corridor(r)}
                 | {r.agency for r in kept_core if r.agency})
    by_term: dict[str, list[GoldenRecord]] = {}
    for record in competitor:
        term = next((h for h in record.entity_hits
                     if kind_by_name.get(h.casefold()) == "competitor"),
                    record.entity_hits[0] if record.entity_hits else "")
        by_term.setdefault(term, []).append(record)
    # Competitor: entrenchment in the client's corridors first, live clock
    # next, then dollars; capped per competitor so one rival's ELA sprawl
    # cannot crowd the band.
    def competitor_rank(r):
        return (
            _corridor(r) not in corridors and r.agency not in corridors,
            not r.research_clock,
            -(r.obligated_dollars or 0.0),
            r.record_id,
        )
    # Round-robin allocation: every rival gets its best row before any rival
    # gets a second or third, so one competitor's big positions cannot evict
    # another's only corridor evidence.
    ranked_by_term = {term: sorted(rows, key=competitor_rank)
                      for term, rows in by_term.items()}
    term_order = sorted(ranked_by_term,
                        key=lambda t: competitor_rank(ranked_by_term[t][0]))
    kept_competitor: list[GoldenRecord] = []
    for round_index in range(PACK_COMPETITOR_PER_TERM):
        for term in term_order:
            rows = ranked_by_term[term]
            if round_index < len(rows) and len(kept_competitor) < PACK_COMPETITOR_CAP:
                kept_competitor.append(rows[round_index])

    channel.sort(key=lambda r: (-(r.obligated_dollars or 0.0), r.record_id))
    kept_channel = channel[:PACK_CHANNEL_CAP]
    # Forecasts: value-first main picks, plus up to two ADDITIVE reserved
    # niche-signal slots (high keyword relevance, at or under the value
    # ceiling) for records the value ordering would drop (operator-ratified
    # rule, 2026-07-24). The main picks are never evicted by a niche slot.
    niche_pool = sorted(
        (r for r in l4[PACK_FORECAST_CAP:]
         if (r.forecast_value or 0.0) <= PACK_NICHE_VALUE_CEILING),
        key=lambda r: (-(r.forecast_rarity or 0.0),
                       -(r.forecast_score or 0.0), r.record_id))
    niche_picks = niche_pool[:PACK_FORECAST_NICHE_SLOTS]
    kept_l4 = l4[:PACK_FORECAST_CAP] + niche_picks
    kept_l1 = l1[:PACK_NOTICE_CAP]            # capability-hit order

    selected = [*kept_core, *kept_competitor, *kept_channel, *kept_l4, *kept_l1]
    selection = {
        "rule": ("core: clocked corridor reps, seven-figure clocks, "
                 "dollar-ranked anchor pool (mega-anchors included), "
                 "clock+dollar fill, cap %d · competitor: round-robin %d per "
                 "rival, corridor then clock then dollars, cap %d · channel "
                 "cap %d · forecast: keyword/NAICS score, value-first order, "
                 "cap %d with up to %d reserved niche-signal slots "
                 "(high relevance under $%dM) · notice hit order cap %d"
                 % (PACK_CORE_CAP, PACK_COMPETITOR_PER_TERM,
                    PACK_COMPETITOR_CAP, PACK_CHANNEL_CAP,
                    PACK_FORECAST_CAP, PACK_FORECAST_NICHE_SLOTS,
                    int(PACK_NICHE_VALUE_CEILING / 1e6), PACK_NOTICE_CAP)),
        "screened_relevant": {
            "L2_core": len(core), "L2_competitor": len(competitor),
            "L2_channel": len(channel), "L4_forecast": len(l4),
            "L1_notice": len(l1)},
        "packed": {
            "L2_core": len(kept_core), "L2_competitor": len(kept_competitor),
            "L2_channel": len(kept_channel), "L4_forecast": len(kept_l4),
            "L1_notice": len(kept_l1)},
        "corridor_agencies": sorted(corridors),
        "niche_forecast_slots": [r.record_id for r in niche_picks],
        "pre_cap_aggregates": pre_cap_aggregates,
    }
    cut_total = len(records) - len(selected)
    _log(f"pack selection kept {len(selected)} of {len(records)} screened "
         f"records ({cut_total} cut by the disclosed rule)")
    return selected, selection


def assess_sufficiency(records: list[GoldenRecord], *, escalated: bool) -> Sufficiency:
    lanes = {r.lane for r in records}
    represented = sorted(lanes)
    if any(r.research_clock for r in records):
        represented.append("L3_research_clock")
    return Sufficiency(
        unique_records=len(records),
        lanes_represented=represented,
        met=(len(records) >= SUFFICIENCY_MIN_RECORDS
             and len(represented) >= SUFFICIENCY_MIN_LANES),
        escalated=escalated,
    )


def build_evidence_pack(
    strategy: Any,
    *,
    sweep: Optional[dict] = None,
    today: Optional[date] = None,
    post: Callable[..., dict] = post_json,
    get: Callable[..., dict] = get_json,
    forecast_fetch: Optional[Callable[[], list]] = None,
    sam_degraded_note: str = "",
    universe_out: Optional[str] = None,
    scope: Any = None,
) -> EvidencePack:
    """Run every lane, dedup, escalate once if the gate is unmet, and return
    the pack. Never pads, never duplicates; scarcity is a machine-readable
    note, not silence. ``universe_out`` optionally writes the full screened
    universe JSON before curation (checkpoint diagnostics).

    ``scope`` is the operator's EngagementScope (tools/relevance/scope.py):
    the agency-universe boundary is exclude-only and operator-owned; an
    out-of-scope record is dropped with a counted disclosure, never packed,
    regardless of capability strength (the pull-time-filter lesson)."""
    today = today or date.today()
    capability_terms = capability_query_terms(
        getattr(strategy, "keywords", []) or [], limit=20)
    screen_terms = screen_vocabulary(capability_terms, strategy)
    lanes: list[LaneStatus] = []
    queries: list[LaneQuery] = []
    records: list[GoldenRecord] = []

    entities_by_kind: dict[str, list[str]] = {}
    for entity in getattr(strategy, "research_entities", []) or []:
        entities_by_kind.setdefault(entity.kind, []).append(entity.name)

    # THE CLIENT'S OWN BOUNDARY, read from the profile the approval step
    # generated. A global tech list hid 541219 and 541611 for apexanalytix,
    # which is where payment-integrity work is actually bought. Absent or
    # unreadable, screen_relevance falls back to TECH_* on its own.
    client_naics_boundary: Optional[list] = None
    client_psc_boundary: Optional[list] = None
    try:
        import json as _json
        import os as _os
        from agents.decisions.client_files import client_dir
        _slug = "".join(
            c if c.isalnum() else "_"
            for c in str(getattr(strategy, "client_name", "")).lower()).strip("_")
        _profile_path = _os.path.join(client_dir(_slug), "profile.json")
        with open(_profile_path, encoding="utf-8") as _fh:
            _profile = _json.load(_fh)
        client_naics_boundary = [str(c) for c in
                                 (_profile.get("naics_boundary") or []) if c] or None
        _codes = (_profile.get("code_universe") or {})
        client_psc_boundary = [str(c) for c in (_codes.get("psc") or []) if c] or None
        if client_naics_boundary:
            _log(f"boundary: client profile supplies {len(client_naics_boundary)} "
                 f"NAICS codes {client_naics_boundary}; the global tech list is "
                 f"not used for this client")
    except Exception:  # noqa: BLE001 - a missing profile is not fatal
        pass

    # L1 · notices. THE STORE FIRST, the sweep artifact only as a fallback.
    #
    # run_l1_from_sweep reads data/cleaned/searches_<slug>.json, a cached
    # artifact from a metered run that may be weeks old and may not exist. It
    # is why this lane reported DEGRADED on every real pack and why both
    # shipped packs carried 3 L1 records. The store holds the whole active
    # universe, keeps notices sam.gov has already dropped, answers in ~13ms,
    # and costs no quota. It also knows notice_type, leverage rank, deadline,
    # set-aside, office and the contact, because it is the writer of all of
    # them.
    l1_records: list[GoldenRecord] = []
    l1_queries: list[LaneQuery] = []
    l1_status, l1_detail = "degraded", ""
    try:
        from agents.golden_press.l1_store import run_l1_from_store
        l1_records, l1_queries, l1_receipt = run_l1_from_store(
            screen_terms,
            entities=entities_by_kind or None,
            vendor=getattr(strategy, "client_name", None),
            naics_boundary=client_naics_boundary,
            psc_boundary=client_psc_boundary)
        if l1_records or (l1_receipt.get("store_rows") or 0) > 0:
            l1_status = "live"
            l1_detail = (
                f"{len(l1_records)} notices from the durable store "
                f"({l1_receipt.get('store_rows', 0):,} held, last ingest "
                f"{l1_receipt.get('store_last_ingest')}), "
                f"{l1_receipt.get('in_corridor', 0):,} in corridor, "
                f"{l1_receipt.get('screened', 0):,} passed the screen, "
                f"0 metered calls")
    except Exception as exc:  # noqa: BLE001 - never sinks the pack
        l1_detail = f"store lane failed ({type(exc).__name__}: {exc})"

    if not l1_records and sweep:
        # Fallback, loudly labelled so nobody reads a cached artifact as live.
        l1_records, l1_queries = run_l1_from_sweep(sweep, screen_terms)
        l1_status = "degraded"
        l1_detail = (f"store lane returned nothing; fell back to the cached "
                     f"sweep artifact: {len(l1_records)} notices"
                     + (f" · {sam_degraded_note}" if sam_degraded_note else ""))
    elif not l1_records and not sweep:
        l1_status, l1_detail = "degraded", (
            l1_detail or sam_degraded_note or "no store rows and no sweep artifact")

    records.extend(l1_records)
    queries.extend(l1_queries)
    lanes.append(LaneStatus(lane="L1_notice", status=l1_status,
                            detail=l1_detail))

    # L2 · entity awards (base pass: client + products + competitors)
    base_terms = entity_terms(strategy, include_resellers=False)
    l2_records, l2_queries = run_l2(
        base_terms, screen_terms, today=today, post=post,
        entities=entities_by_kind or None,
        vendor=getattr(strategy, "client_name", None),
        naics_boundary=client_naics_boundary,
        psc_boundary=client_psc_boundary)
    queries.extend(l2_queries)
    records.extend(l2_records)
    lanes.append(LaneStatus(
        lane="L2_entity_award", status="live",
        detail=f"{len(base_terms)} entity terms searched, "
               f"{len(l2_records)} records kept by the relevance screen"))

    # L4 · forecasts (keywords and NAICS screen per the plan)
    l4_records, l4_queries = run_l4(
        screen_terms,
        list(getattr(strategy, "inferred_naics", []) or []),
        forecast_fetch=forecast_fetch)
    queries.extend(l4_queries)
    records.extend(l4_records)
    l4_status = _forecast_lane_status(l4_queries)
    lanes.append(LaneStatus(
        lane="L4_forecast", status=l4_status,
        detail=f"{len(l4_records)} keyword/NAICS-matched forecast records"))

    universe = dedupe(records)
    flag_clocks(universe, today=today)
    universe, off_scope = apply_engagement_scope(universe, scope)
    if universe_out:
        from tools.artifacts import atomic_write_json
        atomic_write_json(universe_out, {
            "client_name": getattr(strategy, "client_name", ""),
            "generated_at": _now_iso(),
            "off_scope_dropped": len(off_scope),
            "records": [json.loads(r.model_dump_json()) for r in universe],
            "off_scope_records": [
                json.loads(r.model_dump_json()) for r in off_scope],
        })
        _log(f"screened universe ({len(universe)} in scope, "
             f"{len(off_scope)} off-scope) -> {universe_out}")
    selected, selection = select_pack(universe, strategy)
    selection["off_scope_dropped"] = len(off_scope)
    enrich_details(
        [r for r in selected if r.lane == "L2_entity_award"], get=get)
    flag_clocks(selected, today=today)
    verdict = assess_sufficiency(selected, escalated=False)

    scarcity: Optional[dict] = None
    if not verdict.met:
        reseller_terms = [
            t for t in entity_terms(strategy, include_resellers=True)
            if t not in base_terms]
        if reseller_terms:
            _log(f"sufficiency unmet ({verdict.unique_records} records); "
                 f"escalating with {len(reseller_terms)} reseller terms")
            extra_records, extra_queries = run_l2(
                reseller_terms, screen_terms, today=today, post=post,
                entities=entities_by_kind or None,
                vendor=getattr(strategy, "client_name", None),
                naics_boundary=client_naics_boundary,
                psc_boundary=client_psc_boundary)
            queries.extend(extra_queries)
            extra_in, extra_out = apply_engagement_scope(extra_records, scope)
            off_scope = [*off_scope, *extra_out]
            universe = dedupe([*universe, *extra_in])
            flag_clocks(universe, today=today)
            selected, selection = select_pack(universe, strategy)
            selection["off_scope_dropped"] = len(off_scope)
            enrich_details(
                [r for r in selected
                 if r.lane == "L2_entity_award" and not r.detail_enriched],
                get=get)
            flag_clocks(selected, today=today)
        verdict = assess_sufficiency(selected, escalated=True)
        if not verdict.met:
            scarcity = {
                "scarce": True,
                "unique_records": verdict.unique_records,
                "lanes_represented": verdict.lanes_represented,
                "detail": ("sufficiency gate unmet after reseller escalation; "
                           "composing from the records that exist, never padding"),
            }
            _log(f"sufficiency STILL unmet after escalation: "
                 f"{verdict.unique_records} records")

    # The pre-cap aggregates travel out of select_pack inside `selection` and
    # move to their own pack field here, so the number lives in exactly one
    # place. Escalation re-runs select_pack, so this reads whichever run won.
    aggregates = selection.pop("pre_cap_aggregates", None)
    pack = EvidencePack(
        client_name=getattr(strategy, "client_name", ""),
        generated_at=_now_iso(),
        lanes=lanes,
        queries=queries,
        records=sorted(
            selected,
            key=lambda r: (r.lane, -(r.obligated_dollars or 0.0), r.record_id)),
        research={
            "entities": entities_by_kind,
            "capability_terms": capability_terms,
            "screen_terms": screen_terms,
            "inferred_naics": list(getattr(strategy, "inferred_naics", []) or []),
            "target_agencies": list(getattr(strategy, "target_agencies", []) or []),
        },
        sufficiency=verdict,
        scarcity_note=scarcity,
        selection=selection,
        pre_cap_aggregates=aggregates,
    )
    _log(f"pack: {len(pack.records)} unique records, lanes "
         f"{verdict.lanes_represented}, sufficiency "
         f"{'MET' if verdict.met else 'UNMET'}")
    return pack
