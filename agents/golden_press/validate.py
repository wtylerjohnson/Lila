"""Mechanical press validation (GOLDEN_BUILD Phase 3f). Code, not LLM.

Provenance checks (dollars, dates, contract numbers, dock, links, repeated
prose, calendar completeness) run over the COMPOSED CONTENT REGION against
the evidence pack; the sentinel and well-formedness checks run over the
spliced document. Computed totals (lane, corridor, clock, forecast-bound
sums over pack values) are correct behavior, not violations. On failure the
press gets ONE revision attempt with the violations listed; a second failure
FAILS LOUDLY. No silent ship.
"""

from __future__ import annotations

import re
from html.parser import HTMLParser
from typing import Optional

from agents.golden_press.records import EvidencePack, GoldenRecord
from agents.golden_press.press_time import local_press_date
from agents.golden_press.retrieval import normalize_record_id

SENTINEL = "<!-- LILA:COMPLETE -->"

_VOID_TAGS = {"area", "base", "br", "col", "embed", "hr", "img", "input",
              "link", "meta", "param", "source", "track", "wbr"}

_MONTHS = {m: i + 1 for i, m in enumerate(
    ["jan", "feb", "mar", "apr", "may", "jun",
     "jul", "aug", "sep", "oct", "nov", "dec"])}
_MONTH_FULL = {m: i + 1 for i, m in enumerate(
    ["january", "february", "march", "april", "may", "june", "july",
     "august", "september", "october", "november", "december"])}


class Violation(dict):
    """{rule, detail} — dict for painless JSON sidecars."""

    def __init__(self, rule: str, detail: str):
        super().__init__(rule=rule, detail=detail)


# --------------------------------------------------------------------------- #
# structural checks
# --------------------------------------------------------------------------- #
class _BalanceParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.stack: list[str] = []
        self.problems: list[str] = []

    def handle_starttag(self, tag, attrs):
        if tag not in _VOID_TAGS:
            self.stack.append(tag)

    def handle_endtag(self, tag):
        if tag in _VOID_TAGS:
            return
        if not self.stack:
            self.problems.append(f"stray closing </{tag}>")
            return
        if self.stack[-1] == tag:
            self.stack.pop()
            return
        # tolerate the browser-style implicit close of list/paragraph tags
        if tag in self.stack:
            while self.stack and self.stack[-1] != tag:
                dropped = self.stack.pop()
                if dropped not in {"p", "li", "option", "tr", "td", "th"}:
                    self.problems.append(
                        f"<{dropped}> closed implicitly by </{tag}>")
            if self.stack:
                self.stack.pop()
        else:
            self.problems.append(f"unmatched closing </{tag}>")


def check_well_formed(spliced: str) -> list[Violation]:
    parser = _BalanceParser()
    parser.feed(spliced)
    parser.close()
    problems = list(parser.problems)
    problems += [f"<{t}> never closed" for t in parser.stack
                 if t not in {"html", "body", "p", "li"}]
    return [Violation("html_not_well_formed", p) for p in problems[:12]]


def check_sentinel(spliced: str) -> list[Violation]:
    if SENTINEL not in spliced:
        return [Violation("sentinel_missing", f"{SENTINEL} absent")]
    return []


_VOID_TAGS = {"img", "br", "hr", "input", "source", "track", "wbr", "meta",
              "link", "area", "base", "col", "embed", "param"}
_ARIA_HIDDEN_OPEN = re.compile(
    r'<(\w+)(?=[^>]*\saria-hidden="true")[^>]*?(/?)>', re.I)


def _strip_aria_hidden(html: str) -> str:
    """Drop aria-hidden subtrees before reading copy.

    aria-hidden="true" means "not part of the readable content" -- a screen
    reader skips it, and so must a prose check. The scrolling ticker carries a
    duplicate mirror set purely so the CSS keyframe (which ends at
    translateX(-50%)) loops seamlessly; counting that presentational copy as
    prose reported 12 phantom repeated_prose violations against a report whose
    visible copy never repeated. Operator-approved 2026-07-30.
    """

    out: list[str] = []
    index = 0
    while True:
        match = _ARIA_HIDDEN_OPEN.search(html, index)
        if match is None:
            out.append(html[index:])
            return "".join(out)
        out.append(html[index:match.start()])
        tag = match.group(1).lower()
        if match.group(2) == "/" or tag in _VOID_TAGS:
            index = match.end()          # nothing to close: drop the tag alone
            continue
        depth, position = 1, match.end()
        pattern = re.compile(rf"</?{re.escape(tag)}\b[^>]*>", re.I)
        while depth:
            nested = pattern.search(html, position)
            if nested is None:
                return "".join(out)      # unbalanced: drop the rest, never crash
            depth += -1 if nested.group(0).startswith("</") else 1
            position = nested.end()
        index = position


def _visible_text(html: str) -> str:
    text = re.sub(r"<script.*?</script>", " ", html, flags=re.S | re.I)
    text = re.sub(r"<style.*?</style>", " ", text, flags=re.S | re.I)
    text = re.sub(r"<!--.*?-->", " ", text, flags=re.S)
    text = _strip_aria_hidden(text)
    text = re.sub(r"<[^>]+>", " ", text)
    return re.sub(r"\s+", " ", text)


def check_emdash(content: str) -> list[Violation]:
    """House rule R9 over the FULL artifact (same scoping fix as
    banned_vocabulary, 2026-08-03): an em dash in an aria-hidden mirror or
    an attribute is output-reachable."""
    count = _full_artifact_text(content).count("—")
    if count:
        return [Violation("emdash_in_copy", f"{count} em dash(es) in copy")]
    return []


# --------------------------------------------------------------------------- #
# dollar provenance
# --------------------------------------------------------------------------- #
_DOLLAR_RE = re.compile(
    r"\$\s?([0-9][0-9,]*(?:\.[0-9]+)?)\s*([KMB])?\b", re.I)


def _record_affinity(record: GoldenRecord, pack: EvidencePack) -> str:
    """core | competitor | channel from the pack's own research entities,
    mirroring the selection's affinity tiers."""
    entities = (pack.research or {}).get("entities", {})
    core_names = {str(n).casefold() for n in entities.get("product", [])}
    core_names.add(str(pack.client_name).casefold())
    competitor_names = {str(n).casefold()
                        for n in entities.get("competitor", [])}
    hits = {h.casefold() for h in record.entity_hits}
    if hits & core_names:
        return "core"
    if hits & competitor_names:
        return "competitor"
    return "channel"


def _pack_dollar_candidates(pack: EvidencePack) -> list[float]:
    """Every pack value plus every aggregate the composer may legitimately
    state: lane totals, corridor (agency and sub-agency) totals, clock
    totals, affinity-tier totals (core / competitor / channel, clocked and
    not: the live-press proved the composer states these), forecast bound
    sums, grand totals, ceilings, and per-record values."""
    values: list[float] = []
    sums: dict[str, float] = {}

    def _bump(key: str, amount: Optional[float]) -> None:
        if amount:
            sums[key] = sums.get(key, 0.0) + amount

    # R2 sentence receipts (decision band) are verbatim bound evidence on
    # the pack; a dollar figure inside one reconciles like a pack value.
    for alert in ((getattr(pack, "decisions", None) or {}).get("r2")
                  or {}).get("alerts", []):
        for m in _DOLLAR_RE.finditer(str(alert.get("matched_sentence") or "")):
            v, _tol = _stated_dollar(m.group(1), m.group(2))
            if v > 0:
                values.append(v)
    lo_total = hi_total = 0.0
    for record in pack.records:
        for v in (record.obligated_dollars, record.ceiling_dollars):
            if v:
                values.append(float(v))
        amount = record.obligated_dollars or 0.0
        _bump(f"lane:{record.lane}", amount)
        _bump("total", amount)
        affinity = _record_affinity(record, pack)
        _bump(f"aff:{affinity}", amount)
        if record.research_clock:
            _bump("clocks", amount)
            _bump(f"clocks:{affinity}", amount)
        elif record.lane == "L2_entity_award":
            _bump(f"noclock:{affinity}", amount)
        if record.agency:
            _bump(f"agency:{record.agency}", amount)
        if record.sub_agency:
            _bump(f"sub:{record.sub_agency}", amount)
        buyer = " ".join(str(record.sub_agency or record.agency or "").split())
        if buyer and record.lane == "L2_entity_award":
            # Band 05 groups strictly by (sub_agency or agency); its per-
            # buyer totals are pack aggregates by construction.
            _bump(f"buyer:{buyer}", amount)
        if record.recipient:
            # The Ed-doctrine ecosystem view ranks prime recipients on the
            # cited awards.  Its totals are as derivable as agency and entity
            # totals and remain anchored to the underlying award links.
            _bump(f"recipient:{record.recipient.casefold()}", amount)
            _bump(
                f"recipient:{affinity}:{record.recipient.casefold()}",
                amount,
            )
            # render.shape() folds channel-affinity awards into the core
            # band (its split is competitor-vs-everything-else), so the
            # prime-ecosystem view legitimately sums a recipient across
            # core AND channel rows. Mirror that fold here or a dual-lane
            # prime's stated total reads as unprovable (observed live:
            # FCN's $61,457,791 across 6 shape-core records, 2026-08-03).
            if affinity != "competitor" and record.lane == "L2_entity_award":
                _bump(
                    f"recipient:shapecore:{record.recipient.casefold()}",
                    amount,
                )
        for hit in record.entity_hits:
            _bump(f"entity:{hit.casefold()}", amount)
        if record.lane == "L4_forecast":
            bounds = re.findall(
                r"\$\s?([0-9][0-9,.]*)\s*([KMB])",
                str(record.estimated_value_range or ""), re.I)
            unit = {"K": 1e3, "M": 1e6, "B": 1e9}
            parsed = [float(a.replace(",", "")) * unit[u.upper()]
                      for a, u in bounds]
            if parsed:
                lo_total += min(parsed)
                hi_total += max(parsed)
                values += parsed
    # PACK-CARRIED DOLLAR TEXT (2026-08-03): a dollar figure inside a
    # record's own title or description (a per-user price inside an award
    # description: 'PPU: $6,750.00', FiscalNote Air Force paper) is the
    # record's own text rendered verbatim — evidence, never a minted
    # figure. Same doctrine as pack-carried contract-number tokens.
    import json as _json
    for record in pack.records:
        for field in (record.title, record.description):
            for m in _DOLLAR_RE.finditer(str(field or "")):
                v, _tol = _stated_dollar(m.group(1), m.group(2))
                if v > 0:
                    values.append(v)
    decisions_blob = getattr(pack, "decisions", None)
    if decisions_blob:
        for m in _DOLLAR_RE.finditer(
                _json.dumps(decisions_blob, ensure_ascii=False)):
            v, _tol = _stated_dollar(m.group(1), m.group(2))
            if v > 0:
                values.append(v)
    values += list(sums.values()) + [lo_total, hi_total]
    # DECISION BAND D2 (2026-08-03): the materiality disclosure states each
    # R1 card's side totals, and the minor-tier summary states the versioned
    # floor constant. Both are deterministic rule output over pack values
    # (or version-pinned constants), so they are provenanced, exactly like
    # RULE_CONSTANT_DATES on the date side.
    from agents.golden_press.decision_rules import RULE_CONSTANT_DOLLARS
    values += [float(v) for v in RULE_CONSTANT_DOLLARS]
    decisions = getattr(pack, "decisions", None) or {}
    for card in ((decisions.get("r1") or {}).get("cards") or []):
        materiality = card.get("materiality") or {}
        for key in ("client_total", "rival_total"):
            v = materiality.get(key)
            if v:
                values.append(float(v))
    return [v for v in values if v > 0]


def _stated_dollar(value_text: str, unit: Optional[str]) -> tuple[float, float]:
    """(value, tolerance): tolerance is half the unit of the last displayed
    digit, so $1.92M matches 1,923,540 and $2M matches 1,960,000."""
    raw = float(value_text.replace(",", ""))
    scale = {"K": 1e3, "M": 1e6, "B": 1e9}.get((unit or "").upper(), 1.0)
    value = raw * scale
    if scale == 1.0:
        return value, 0.5
    decimals = len(value_text.split(".")[1]) if "." in value_text else 0
    return value, (10 ** -decimals) * scale / 2.0


def _composed_copy(content: str) -> str:
    """Content minus structural receipt rows, for figure provenance scans.

    ``sb-treatment`` and ``sb-card-agency`` rows are records-derived house
    chrome: MATCHED sentences are verbatim quoted SOURCE text bound to a
    linked card, and the quote is itself the receipt for any figure, id, or
    date the source printed (witnessed 2026-08-05: DLA sole-source quotes
    carrying model numbers and US-format dates). The deterministic renderer
    is the only writer of these rows, so an invented figure cannot enter
    them; composed prose and every other surface stay fully scanned.
    """
    content = re.sub(r'<div class="sb-treatment">.*?</div>', " · ", content,
                     flags=re.S)
    return re.sub(r'<div class="sb-card-agency">.*?</div>', " · ", content,
                  flags=re.S)


def check_dollars(content: str, pack: EvidencePack) -> list[Violation]:
    candidates = _pack_dollar_candidates(pack)
    out: list[Violation] = []
    for match in _DOLLAR_RE.finditer(_visible_text(_composed_copy(content))):
        value, tolerance = _stated_dollar(match.group(1), match.group(2))
        if value <= 0:
            continue
        if not any(abs(value - c) <= max(tolerance, 0.5) for c in candidates):
            out.append(Violation(
                "dollar_without_provenance",
                f"{match.group(0).strip()} matches no pack value or "
                f"derivable aggregate"))
    return out[:20]


# --------------------------------------------------------------------------- #
# date provenance
# --------------------------------------------------------------------------- #
def _pack_date_keys(pack: EvidencePack) -> set[str]:
    keys: set[str] = set()

    def _add_ymd(year: int, month: int, day: int) -> None:
        keys.add(f"{year:04d}-{month:02d}-{day:02d}")
        keys.add(f"{year:04d}-{month:02d}")
        keys.add(f"m:{month:02d}-{day:02d}")            # "31 JUL" style
        keys.add(f"y:{year}")
        fiscal = year + (1 if month >= 10 else 0)
        keys.add(f"fy:{fiscal}")

    def _add_iso(value: Optional[str]) -> None:
        text = str(value or "")
        m = re.fullmatch(r"(\d{4})-(\d{2})-(\d{2})", text[:10])
        if m:
            _add_ymd(int(m[1]), int(m[2]), int(m[3]))
            return
        # APFS anticipated dates arrive US-formatted (07/27/2026)
        m = re.search(r"\b(\d{1,2})/(\d{1,2})/(\d{4})\b", text)
        if m:
            _add_ymd(int(m[3]), int(m[1]), int(m[2]))

    # The pack's own generation stamp is a pack date by definition: the
    # competitive band's "as of" cites it (2026-08-05).
    _add_iso(getattr(pack, "generated_at", None))
    for record in pack.records:
        for field in (record.period_start, record.period_end,
                      record.potential_end_date, record.posted_date,
                      record.response_deadline, record.retrieved_at):
            _add_iso(field)
        if record.fiscal_year and str(record.fiscal_year).isdigit():
            keys.add(f"fy:{int(record.fiscal_year)}")
        window = str(record.anticipated_solicitation or "")
        for quarter, year in re.findall(r"Q([1-4])\s*(?:FY)?\s*(\d{4})", window):
            keys.add(f"q:{quarter}-{year}")
            keys.add(f"fy:{int(year)}")
        _add_iso(window)
    # EVENTS_LANE (2026-07-27): event and registration dates are pack dates
    # too, so an event row's dates reconcile like every other figure. An
    # event date the pack does not carry is a date-provenance violation.
    for event in (pack.events or []):
        get = (event.get if isinstance(event, dict)
               else lambda k, d=None: getattr(event, k, d))
        for field in ("event_start", "event_end", "registration_deadline",
                      "posted_date"):
            _add_iso(get(field))
    # DECISION_RULES (2026-08-03): R2 store-notice alerts render their
    # deadline/posted dates in the decision band; those dates live on the
    # alert rows the pack carries, so they reconcile like events.
    for alert in ((pack.decisions or {}).get("r2") or {}).get("alerts", []):
        for field in ("deadline", "posted"):
            _add_iso(alert.get(field))
        # the verbatim sentence receipt is bound evidence carried on the
        # pack; dates it quotes reconcile like every other pack date
        for _display, key in _stated_date_keys(
                str(alert.get("matched_sentence") or "")):
            keys.add(key)
    # R3's vehicle facts are a versioned, cited constant carried on the pack
    for value in ((pack.decisions or {}).get("r3") or {}).get(
            "facts", {}).values():
        for item in (value if isinstance(value, (list, tuple)) else [value]):
            _add_iso(item)
    _add_iso(pack.generated_at)
    _add_iso(local_press_date(pack.generated_at))
    keys.add(f"y:{str(pack.generated_at)[:4]}")
    # DECISION BAND (2026-08-03). Two additional provenance sources, both
    # deterministic: the versioned R3 vehicle constant (one owner in
    # decision_rules; the band renders its published dates), and the
    # pack-stored decisions object itself, whose evidence rows and verbatim
    # sentence receipts may carry dates the record fields do not (a store
    # notice's posted date, a date inside a quoted sentence). Both surfaces
    # are pack-carried or version-pinned, so a date they state is
    # provenanced; a date in neither still violates.
    from agents.golden_press.decision_rules import RULE_CONSTANT_DATES
    for value in RULE_CONSTANT_DATES:
        _add_iso(value)
    # TARGETING BAND (v1.4, 2026-08-05): the band prints each contact's
    # RETRIEVED DATE, which is an enrichment stamp and not a record field.
    # It is carried on the pack exactly like the decisions blob, so a date
    # the pack states is provenanced and a date in neither still violates.
    decisions = getattr(pack, "decisions", None)
    for blob_source in (decisions, getattr(pack, "targeting", None)):
        if not blob_source:
            continue
        import json as _json
        blob = _json.dumps(blob_source, ensure_ascii=False, default=str)
        for _display, key in _stated_date_keys(blob):
            keys.add(key)
            if re.fullmatch(r"\d{4}-\d{2}-\d{2}", key):
                keys.add(key[:7])
                keys.add(f"m:{key[5:7]}-{key[8:10]}")
                keys.add(f"y:{key[:4]}")
    return keys


def _stated_date_keys(text: str) -> list[tuple[str, str]]:
    """(display, key) pairs for every date-like token in the visible copy."""
    out: list[tuple[str, str]] = []
    for m in re.finditer(r"\b(\d{4})-(\d{2})-(\d{2})\b", text):
        out.append((m.group(0), f"{m[1]}-{m[2]}-{m[3]}"))
    for m in re.finditer(r"\b(\d{1,2})/(\d{1,2})/(\d{4})\b", text):
        out.append((m.group(0),
                    f"{int(m[3]):04d}-{int(m[1]):02d}-{int(m[2]):02d}"))
    # A month token is a real month word, whole: the loose [a-z]* tail let
    # "03 Decision clocks" read as "03 Dec" (the 04 AUGUST SCHELL class,
    # band-heading edition, caught by the contract renderer 2026-08-03).
    month_name = (r"(jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|"
                  r"jun(?:e)?|jul(?:y)?|aug(?:ust)?|sep(?:t(?:ember)?)?|"
                  r"oct(?:ober)?|nov(?:ember)?|dec(?:ember)?)(?![a-z])")
    for m in re.finditer(rf"\b(\d{{1,2}})\s+{month_name}\.?\s*(\d{{4}})?\b",
                         text, re.I):
        month = _MONTHS[m.group(2).casefold()[:3]]
        day = int(m.group(1))
        if m.group(3):
            out.append((m.group(0), f"{int(m.group(3)):04d}-{month:02d}-{day:02d}"))
        else:
            out.append((m.group(0), f"m:{month:02d}-{day:02d}"))
    for m in re.finditer(rf"\b{month_name}\.?\s+(\d{{1,2}})(?:,)?\s+(\d{{4}})\b",
                         text, re.I):
        month = _MONTHS[m.group(1).casefold()[:3]]
        out.append((m.group(0),
                    f"{int(m.group(3)):04d}-{month:02d}-{int(m.group(2)):02d}"))
    for m in re.finditer(rf"\b{month_name}\.?\s+(\d{{4}})\b", text, re.I):
        if m.group(1).casefold()[:3] in _MONTHS:
            out.append((m.group(0),
                        f"{int(m.group(2)):04d}-{_MONTHS[m.group(1).casefold()[:3]]:02d}"))
    for m in re.finditer(r"\bFY\s?(\d{2,4})\b", text):
        year = int(m.group(1))
        out.append((m.group(0), f"fy:{year + 2000 if year < 100 else year}"))
    for m in re.finditer(r"\bQ([1-4])\s*(?:FY)?\s?(\d{4})\b", text):
        out.append((m.group(0), f"q:{m.group(1)}-{m.group(2)}"))
    return out


def check_dates(content: str, pack: EvidencePack) -> list[Violation]:
    keys = _pack_date_keys(pack)
    out = []
    for display, key in _stated_date_keys(
            _visible_text(_composed_copy(content))):
        if key in keys:
            continue
        if re.fullmatch(r"\d{4}-\d{2}-\d{2}", key) and (
                key[:7] in keys or f"y:{key[:4]}" in keys):
            # day-precision claims need the exact day
            out.append(Violation("date_without_provenance",
                                 f"{display!r} not a pack date"))
        elif key not in keys:
            out.append(Violation("date_without_provenance",
                                 f"{display!r} not a pack date"))
    return out[:20]


# --------------------------------------------------------------------------- #
# identifiers, dock, links, calendar, prose
# --------------------------------------------------------------------------- #
_ID_TOKEN_RE = re.compile(r"\b[0-9A-Z][0-9A-Z\-]{8,24}\b")


def _visible_token_positions(html: str, token: str) -> list[int]:
    """Start offsets of token occurrences that render as visible text.

    A match inside a tag (between an unclosed ``<`` and its ``>``) is
    markup, not copy, and never counts as an occurrence.
    """
    positions = []
    for m in re.finditer(
            rf"(?<![0-9A-Za-z]){re.escape(token)}(?![0-9A-Za-z])", html):
        open_lt = html.rfind("<", 0, m.start())
        if open_lt != -1 and html.find(">", open_lt, m.start()) == -1:
            continue
        positions.append(m.start())
    return positions


def check_contract_numbers(content: str, pack: EvidencePack) -> list[Violation]:
    """Every contract-number-shaped token in the copy exists in the pack.

    Exception with provenance intact: a token whose every visible
    occurrence sits inside an anchor is verbatim linked source text (the
    source itself printed the number and the link is the citation). The
    rule keeps firing for numbers loose in prose, which is where an
    invented id could hide.
    """
    known = {normalize_record_id(r.record_id) for r in pack.records}
    known |= {normalize_record_id(r.parent_award_id) for r in pack.records
              if r.parent_award_id}
    # Discovery AND the anchored-position test both run over the SAME
    # receipt-stripped text: a treatment quote is source verbatim, so an
    # occurrence inside it must not count as a loose unanchored id
    # (witnessed 2026-08-05: MATCHED quotes re-printing an anchored
    # solicitation number read as unprovenanced).
    scan_text = _composed_copy(content)
    spans = _anchor_spans(scan_text)
    # PACK-CARRIED TOKENS (2026-08-03). The check exists to catch an id the
    # renderer or model MINTED; a token the pack itself carries in a record
    # string field is evidence, not invention. The store-backed L1 lane's
    # first report proved the need: a VA notice's office field reads
    # "248-NETWORK CONTRACT OFFICE 8", which is id-shaped and is the
    # record's own text. Same rule for the pack-stored decisions object
    # (store-notice ids, ids inside verbatim sentence receipts). A token in
    # neither surface still violates.
    import json as _json
    surfaces = [_json.dumps(
        [record.model_dump() for record in pack.records],
        ensure_ascii=False, default=str)]
    decisions = getattr(pack, "decisions", None)
    if decisions:
        surfaces.append(_json.dumps(decisions, ensure_ascii=False))
    research = getattr(pack, "research", None)
    if research:
        # Entity names are pack-carried text too: 'Proficiency1' is a
        # client product division, not a minted identifier (JTG press,
        # 2026-08-19). Same doctrine as the decisions blob.
        surfaces.append(_json.dumps(research, ensure_ascii=False,
                                    default=str))
    targeting = getattr(pack, "targeting", None)
    if targeting:
        # Same doctrine as the decisions blob: an id-shaped token the pack
        # itself carries (a contracting-office string on a joined evidence
        # row) is the record's own text, not a minted identifier.
        surfaces.append(_json.dumps(targeting, ensure_ascii=False,
                                    default=str))
    for blob in surfaces:
        # The detector is uppercase-shaped; a pack-carried mixed-case name
        # the renderer uppercases ("Proficiency1" -> a heading's
        # PROFICIENCY1) must still count as pack-carried, so the known side
        # scans the uppercased blob too (normalize_record_id folds case).
        for token in set(_ID_TOKEN_RE.findall(blob)
                         + _ID_TOKEN_RE.findall(blob.upper())):
            if any(ch.isdigit() for ch in token) \
                    and any(ch.isalpha() for ch in token):
                known.add(normalize_record_id(token))
    # Verbatim linked source text keeps its provenance: a token whose every
    # visible occurrence sits inside an anchor was printed by the source and
    # the link is its citation.
    spans = _anchor_spans(content)
    out = []
    for token in set(_ID_TOKEN_RE.findall(_visible_text(scan_text))):
        if not any(ch.isdigit() for ch in token):
            continue                      # prose acronyms, never ids
        if not any(ch.isalpha() for ch in token):
            continue                      # dates and bare numbers, never ids
        if re.fullmatch(r"(FY|Q)[0-9\-]+", token):
            continue
        if normalize_record_id(token) in known:
            continue
        positions = _visible_token_positions(scan_text, token)
        if positions and all(
                any(start <= p < end for start, end, _ in spans)
                for p in positions):
            continue                      # verbatim linked source text
        out.append(Violation(
            "contract_number_without_provenance",
            f"{token} is not a pack record or parent award id"))
    return out[:20]


def _anchor_spans(html: str) -> list[tuple[int, int, str]]:
    spans = []
    for m in re.finditer(r"<a\b[^>]*href=\"([^\"]+)\"[^>]*>(.*?)</a>",
                         html, re.S | re.I):
        spans.append((m.start(), m.end(), m.group(1)))
    return spans


def check_links(content: str, pack: EvidencePack) -> list[Violation]:
    """LINKAGE LAW: every record identifier occurrence sits inside an anchor
    whose href exactly matches that record's pack source_url. Base64 image
    payloads are masked first: an id-shaped run inside a seal's data URI is
    image bytes, not a citation (coverage fixture, 2026-08-03)."""
    content = re.sub(r'(src="data:[^"]*")',
                     lambda m: 'src="data:masked"'.ljust(len(m.group(1)), "_"),
                     content)
    # Discovery AND the anchored-position test run over the SAME text, or the
    # offsets refer to different documents and every span lookup is a guess.
    scan_text = _composed_copy(content)
    spans = _anchor_spans(scan_text)
    out: list[Violation] = []
    for record in pack.records:
        if not record.record_id:
            continue
        display = str(record.record_id)
        # Word-bounded, visible-text occurrences only: a short id such as
        # "0023" must not match inside another award's PIID, a URL, or a
        # figure; markup never counts as copy.
        for start_pos in _visible_token_positions(scan_text, display):
            enclosing = [href for start, end, href in spans
                         if start <= start_pos < end]
            if not enclosing:
                out.append(Violation(
                    "record_id_not_linked",
                    f"{display} appears without an anchor"))
                break
            if record.url and enclosing[-1] != record.url:
                out.append(Violation(
                    "record_link_not_canonical",
                    f"{display} anchored to {enclosing[-1][:80]} instead of "
                    f"its pack source_url"))
                break
    return out[:24]


def check_repeated_prose(content: str) -> list[Violation]:
    """No repeated prose strings across records: any 48+ char normalized
    sentence fragment appearing more than once is boilerplate.

    Structured treatment rows (``sb-treatment``) are records-derived house
    language, byte-identical by construction when two cards cite the same
    linked award (e.g. one client-history award anchoring two displacement
    cards). Like aria-hidden ticker mirrors, they are not composed prose
    and never count toward repetition.
    """
    # The replacement is a split token, not a space: a bare space would
    # weld the text on either side of a stripped treatment into one
    # phantom fragment (witnessed 2026-08-04: "…INDEFINITE QUANTITY" +
    # "$6,759…" across a card seam read as one repeated sentence).
    content = re.sub(
        r'<div class="sb-treatment">.*?</div>', " · ", content, flags=re.S)
    text = _visible_text(content)
    fragments = re.split(r"(?<=[.!?;])\s+|·|\|", text)
    seen: dict[str, int] = {}
    for fragment in fragments:
        norm = " ".join(fragment.split()).casefold()
        if len(norm) >= 48 and not norm.startswith(("http", "www")):
            seen[norm] = seen.get(norm, 0) + 1
    out = [Violation("repeated_prose", f"{k[:90]!r} x{v}")
           for k, v in seen.items() if v > 1]
    return out[:12]


# CONTRACT DERIVATION (2026-08-03): the band list is defined ONCE, in
# docs/REPORT_CONTRACT.md §1, loaded by agents.golden_press.contract. The
# GOLDEN_BAND_ORDER / EVENTS_BAND_ORDER constants and the conditional-events
# grammar are RETIRED with the ten-band grammar itself; every §1 band is
# required, and a zero renders as its stated zero-state, never as absence.

# L7: the contract names the base banned terms and directs extensions here.
BANNED_VOCABULARY_EXTENSIONS: tuple[str, ...] = ()


def check_band_grammar(content: str,
                       pack: Optional[EvidencePack] = None) -> list[Violation]:
    """Contract §1 + §3: the exact band sequence, position-derived numbering
    (the '08 inside 05' class), and the composed hero opening the region."""
    from agents.golden_press.contract import contract_section_ids
    out: list[Violation] = []
    if not content.lstrip().startswith('<div id="board">'):
        out.append(Violation(
            "hero_missing",
            "content region must open with the #board wrapper carrying the "
            "library hero (composed, never skeleton-inherited)"))
    found = tuple(re.findall(r'<section class="band" id="([a-z-]+)"',
                             content))
    expected = contract_section_ids()
    if found != expected:
        out.append(Violation(
            "band_grammar",
            f"content bands must be exactly {list(expected)} in order "
            f"(REPORT_CONTRACT.md §1 is the single band source); "
            f"found {list(found)}"))
    numbers = [int(n) for n in
               re.findall(r'<div class="b-no">(\d+)</div>', content)]
    if numbers != list(range(1, len(found) + 1)):
        out.append(Violation(
            "band_numbering",
            f"band numbers must be position-derived 1..{len(found)}; "
            f"found {numbers}"))
    return out


def _full_artifact_text(html_text: str) -> str:
    """The HONEST scan surface (2026-08-03 forensics): the decoded final
    serialized HTML, excluding ONLY <style> and <script> element bodies.
    No aria-hidden stripping, no tag stripping: attribute copy, labels,
    and presentation mirrors are all output-reachable."""
    import html as _html
    text = re.sub(r"<style\b[^>]*>.*?</style>", " ", html_text,
                  flags=re.S | re.I)
    text = re.sub(r"<script\b[^>]*>.*?</script>", " ", text,
                  flags=re.S | re.I)
    return _html.unescape(text)


def check_banned_vocabulary(content: str) -> list[Violation]:
    """Contract L7 over the FULL artifact (style/script bodies excluded).
    Plural and possessive tails cannot defeat the boundary: 'research
    clocks' fails exactly like 'research clock' (the certified-hero
    escape, forensics 2026-08-03)."""
    from agents.golden_press.contract import banned_vocabulary
    text = _full_artifact_text(content).casefold()
    out = []
    for term in tuple(banned_vocabulary()) + BANNED_VOCABULARY_EXTENSIONS:
        needle = re.escape(term.casefold())
        pattern = re.compile(
            r"(?<![a-z0-9])" + needle + r"(?:s|es|'s)?(?![a-z0-9])")
        n = len(pattern.findall(text))
        if n:
            out.append(Violation(
                "banned_vocabulary",
                f"{term!r} appears {n}x in the artifact (contract L7, "
                f"full-artifact scan)"))
    return out[:12]


_STUDIO_ATTR_RE = re.compile(
    r'\s(?:data-studio-[a-z-]+|data-edit-id|data-edit-singleline|'
    r'contenteditable)(?:="[^"]*")?', re.I)


def client_export(content: str) -> str:
    """The client build, generated from press state: studio affordances
    physically removed (contract §3). The studio build keeps them."""
    text = re.sub(r"<script.*?</script>", "", content, flags=re.S | re.I)
    text = _STUDIO_ATTR_RE.sub("", text)
    text = re.sub(r'\shidden(?:="[^"]*")?(?=[\s>])', "", text, flags=re.I)
    return text


def check_client_purity(client_html: str) -> list[Violation]:
    """Contract §3: the client artifact carries no script, no
    contenteditable, no data-studio-*, no hidden sections, no localStorage
    references. Stylesheet SELECTORS naming those attributes are inert
    (no runtime can act on them) and are excluded from the scan."""
    scannable = re.sub(r"<style.*?</style>", " ", client_html,
                       flags=re.S | re.I)
    out = []
    for name, pattern in (
            ("script", r"<script"),
            ("contenteditable", r"contenteditable"),
            ("data-studio", r"data-studio-[a-z-]+"),
            ("hidden section", r"<(?:section|div)[^>]*hidden"),
            ("localStorage", r"localStorage")):
        target = client_html if name == "script" else scannable
        if re.search(pattern, target, re.I):
            out.append(Violation(
                "client_artifact_impure",
                f"client artifact contains {name} (contract §3)"))
    return out


def check_expired_flags(content: str, pack: EvidencePack) -> list[Violation]:
    """Contract L8: any clock in the clocks table earlier than the press
    date renders flagged, never silently."""
    today = local_press_date(pack.generated_at)
    if not today:
        return []
    table = re.search(r'<table[^>]*data-clock-table="1".*?</table>',
                      content, re.S)
    if not table:
        return []
    out = []
    for row in re.findall(r"<tr[^>]*>.*?</tr>", table.group(0), re.S):
        cell = re.search(r'class="cd">([^<]+)', row)
        if not cell:
            continue
        parsed = _stated_date_keys(cell.group(1))
        iso = next((k for _d, k in parsed
                    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", k)), None)
        if iso and iso < today and 'class="flag"' not in row:
            out.append(Violation(
                "expired_clock_unflagged",
                f"clock {cell.group(1).strip()!r} predates the press date "
                f"and carries no EXPIRED flag (contract L8)"))
    return out[:12]


def check_shapeability_decay(content: str,
                             pack: EvidencePack) -> list[Violation]:
    """TASK 3 (2026-08-04): a notice whose response deadline predates the
    press date may render only inside the collapsed closed-windows strip,
    never as a featured forward card."""
    press_date = str(pack.generated_at or "")[:10]
    if not press_date:
        return []
    forward = re.search(r'<section class="band" id="forward".*?</section>',
                        content, re.S)
    if not forward:
        return []
    band = forward.group(0)
    closed = re.search(
        r"<details[^>]*>(?:(?!</details>).)*CLOSED SHAPEABLE WINDOWS"
        r".*?</details>", band, re.S)
    featured = band.replace(closed.group(0), "") if closed else band
    out = []
    for record in pack.records:
        if record.lane != "L1_notice":
            continue
        deadline = str(record.response_deadline or "")[:10]
        if not deadline or deadline >= press_date:
            continue
        if str(record.record_id) in featured:
            out.append(Violation(
                "shapeable_window_expired_featured",
                f"{record.record_id} respond-by {deadline} predates the "
                f"press date yet renders featured (contract L8 decay)"))
    return out[:12]


def check_hero_proof(content: str, pack: EvidencePack) -> list[Violation]:
    """The library-native hero proof must visibly reconcile to the pack."""
    hero = re.search(r'<header class="hero"[^>]*>.*?</header>', content, re.S)
    if not hero:
        return [Violation("hero_proof_missing", "hero is absent")]
    proof = re.search(r'<details class="proof"[^>]*>.*?</details>', hero.group(0), re.S)
    if not proof or "Show the work" not in _visible_text(proof.group(0)):
        return [Violation(
            "hero_proof_missing",
            "hero lacks the visible library-native Show the work component")]
    counts = [int(n) for n in re.findall(
        r'\bdata-count="(\d+)"', proof.group(0))]
    if not counts or sum(counts) != len(pack.records):
        return [Violation(
            "hero_proof_mismatch",
            f"hero proof sums to {sum(counts)}; pack carries "
            f"{len(pack.records)} records")]
    return []


def check_required_fields(content: str, pack: EvidencePack) -> list[Violation]:
    """Contract §2, mechanical presence probes for bands 00-08 + footer."""
    out = []
    text = _visible_text(content)

    def _band(band_id: str) -> str:
        m = re.search(rf'<section class="band" id="{band_id}".*?</section>',
                      content, re.S)
        return m.group(0) if m else ""

    if "Federal Opportunity Pre-Assessment" not in text:
        out.append(Violation("required_field",
                             "00 hero: artifact name missing"))
    if '<header class="hero"' not in content:
        out.append(Violation("required_field", "00 hero: headline missing"))
    decisions = _band("decisions")
    # A stated zero is any of the sanctioned zero-states: the tested
    # head-to-head zero, or the untested-lane statuses that replaced the
    # old unconditional NONE (gold-standard brief, 2026-08-04).
    _stated_zero = ("HEAD-TO-HEAD · NONE", "COMPETITIVE VALIDATION NEXT",
                    "COMPETITIVE VALIDATION PENDING", "were not computed")
    if (decisions and "DO NOW" not in decisions
            and not any(token in decisions for token in _stated_zero)):
        out.append(Violation(
            "required_field",
            "01 decisions: cards carry no DO NOW action and no stated zero"))
    forward = _band("forward")
    if forward and "qualify" not in _visible_text(forward).casefold():
        out.append(Violation(
            "required_field",
            "02 forward: entries carry no qualify label and no stated zero"))
    clocks = _band("clocks")
    if clocks and 'data-clock-table="1"' not in clocks             and "CLOCKS · NONE" not in clocks:
        out.append(Violation(
            "required_field", "03 clocks: no table and no stated zero"))
    paper = _band("paper")
    if paper and "route" not in _visible_text(paper).casefold():
        out.append(Violation(
            "required_field", "04 paper: route caveat missing"))
    agencies_band = _band("agencies")
    if agencies_band and "istor" not in agencies_band             and "OBLIGATIONS · NONE" not in agencies_band:
        out.append(Violation(
            "required_field",
            "05 agencies: historical-context lede missing"))
    competitive_band = _band("competitive")
    # Contract §2/06 (amendment v1.3): contested accounts render as decks;
    # otherwise the band states one of the sanctioned postures. The tested
    # completed-screen receipt is additionally guarded by
    # check_competitor_claim_is_tested, so an untested lane cannot satisfy
    # this probe with a zero it never earned.
    _competitive_posture = ("COMPLETED SCREEN", "COMPETITIVE VALIDATION NEXT",
                            "COMPETITIVE VALIDATION PENDING", "not computed")
    if (competitive_band and '<article class="deck' not in competitive_band
            and not any(t in competitive_band for t in _competitive_posture)):
        out.append(Violation(
            "required_field",
            "06 competitive: no account decks and no stated posture"))
    method = _band("method")
    for label in ("LINK CLASSES", "SELECTION RULE", "MARKS",
                  "MANUAL CONTENT", "DOES NOT CLAIM", "SCREENING RECEIPTS"):
        if method and label not in method:
            out.append(Violation(
                "required_field", f"08 method: block {label!r} missing"))
    targeting_band = _band("targeting")
    # Contract §2/09 (amendment v1.4): the band renders its spec table, or
    # states one of the postures its own §2 law names. The postures are
    # parsed from the contract, so this probe follows a rename.
    if targeting_band:
        from agents.golden_press.contract import band_postures
        postures = band_postures("targeting")
        if (_TARGETING_TABLE not in targeting_band
                and "were not computed" not in targeting_band
                and not any(p in targeting_band for p in postures)):
            out.append(Violation(
                "required_field",
                "09 targeting: no spec table and no stated posture"))
    if 'class="foot"' not in content             or "prepared by The GTM Group" not in text:
        out.append(Violation("required_field", "footer missing or unnamed"))
    return out[:20]


def check_events(content: str, pack: EvidencePack) -> list[Violation]:
    """EVENTS_LANE laws (2026-07-27).

    An event ships only with a live-verified URL, anchored to that URL
    wherever its name renders, and it never enters the evidence dock (events
    are a separate surface and never count toward sufficiency).
    """
    events = list(pack.events or [])
    if not events:
        return []
    out: list[Violation] = []
    band = re.search(r'<section[^>]*id="events".*?</section>', content, re.S)
    for event in events:
        get = (event.get if isinstance(event, dict)
               else lambda k, d=None: getattr(event, k, d))
        name, url = str(get("name") or ""), str(get("url") or "")
        if not get("url_verified"):
            out.append(Violation(
                "event_unverified",
                f"event {name[:60]!r} shipped without live URL verification"))
        if band and name and name[:40] in band.group(0):
            # The renderer attribute-escapes URLs, so a query-string URL
            # renders with &amp; where the record holds &. Comparing only
            # the raw form failed the FIRST event URL that carried a query
            # string (AFCEA WEST, 2026-07-30) even though the anchor was
            # present and correct; sam.gov canonical URLs had hidden the
            # bug because they carry no ampersands.
            import html as _html

            anchored = (f'href="{url}"' in band.group(0)
                        or f'href="{_html.escape(url, quote=True)}"'
                        in band.group(0))
            if not anchored:
                out.append(Violation(
                    "event_not_linked",
                    f"event {name[:60]!r} renders without its verified URL"))
    return out[:20]


def check_lane_representation(content: str, pack: EvidencePack) -> list[Violation]:
    """L1/L2 lanes must render. L4 is exempt: contract §2/02's inclusion
    rule cuts forecasts with no client-paper adjacency, and the cut count
    is stated in Show the work, so an unrendered forecast is a stated
    decision, not a silent drop."""
    lanes_present = {r.lane for r in pack.records} - {"L4_forecast"}
    out = []
    for lane in lanes_present:
        rec = next(r for r in pack.records if r.lane == lane)
        if str(rec.record_id) not in content:
            out.append(Violation(
                "lane_unrepresented",
                f"{lane} has pack records but none render in the content"))
    return out


_SIGNAL_TILE_RE = re.compile(r'<div class="sb-signal">.*?</div>\s*</div>|'
                             r'<div class="sb-signal">.*?</small></div>', re.S)
_TILE_FIGURE_RE = re.compile(
    r"<strong>(?:(?!</strong>).)*?((?:&gt;)?\$\s?[0-9][0-9,.]*\s?[KMB])"
    r"(?:(?!</strong>).)*?</strong>", re.S)
_PANEL_TITLES = ("How the total was built", "How this was identified",
                 "How the floor was built")


def check_aggregate_justification(content: str, pack: EvidencePack) -> list[Violation]:
    """SHOW-YOUR-WORK LAW (operator directive 2026-07-25, same standing as
    the LINK CHECK): every band-stat dollar aggregate carries a qualifier
    caption, a VISIBLE Show-the-work toggle, and a panel whose components
    reconcile against the pack; the terminal figure matches the headline
    character for character."""
    out: list[Violation] = []
    known_urls = {r.url for r in pack.records if r.url}
    for tile in re.findall(r'<div class="sb-signal">.*?(?=<div class="sb-signal">|'
                           r'</div>\s*<div class="sb-reading-guide|$)',
                           content, re.S):
        fig = _TILE_FIGURE_RE.search(tile)
        if not fig:
            continue                       # count tiles carry no dollar claim
        figure = fig.group(1).strip()
        if '<details class="lila-just"' not in tile:
            out.append(Violation(
                "aggregate_unjustified",
                f"band aggregate {figure} has no show-the-work panel"))
            continue
        if "lila-qualifier" not in tile:
            out.append(Violation(
                "aggregate_uncaptioned",
                f"band aggregate {figure} lacks a qualifier caption"))
        if "Show the work" not in tile or "Hide the work" not in tile:
            out.append(Violation(
                "aggregate_toggle_missing",
                f"band aggregate {figure} lacks the visible toggle"))
        if not any(t in tile for t in _PANEL_TITLES):
            out.append(Violation(
                "aggregate_panel_untitled",
                f"band aggregate {figure} panel lacks its variant title"))
        body = re.search(r'<div class="lila-just-body">(.*?)</div>\s*</details>',
                         tile, re.S)
        panel = body.group(1) if body else ""
        if figure not in panel:
            out.append(Violation(
                "aggregate_terminal_mismatch",
                f"panel terminal does not restate {figure} character for "
                f"character"))
        hrefs = set(re.findall(r'href="([^"]+)"', panel))
        if hrefs and not (hrefs & known_urls):
            out.append(Violation(
                "aggregate_components_unanchored",
                f"panel for {figure} cites no pack source_url"))
    return out[:24]


_TESTED_ZERO_CLAIMS: tuple[tuple[str, str], ...] = (
    (r"HEAD-TO-HEAD\s*·\s*NONE(?!\s+FOUND)", "HEAD-TO-HEAD · NONE"),
    (r"\bno cited overlap\b", "no cited overlap"),
    (r"\bno rival presence\b", "no rival presence"),
    (r"\bzero overlap\b", "zero overlap"),
    (r"holds both current client paper and current named-rival paper",
     "the tested head-to-head sentence"),
)


def check_competitor_claim_is_tested(content: str, pack: EvidencePack
                                     ) -> list[Violation]:
    """A competitive zero may only be printed when a rival screen RAN.

    The Thinklogical press certified while asserting 'HEAD-TO-HEAD · NONE'
    over a pack carrying zero competitor entities and zero competitor
    queries: nothing was screened, so the sentence claimed a test that had
    never happened. Coverage is derived from the pack (supplied entities +
    the lane's own query rows), never from the copy.
    """
    from agents.golden_press.render import competitor_coverage

    coverage = competitor_coverage(pack)
    if coverage["tested_zero_allowed"]:
        return []
    text = _full_artifact_text(content)
    out: list[Violation] = []
    for pattern, label in _TESTED_ZERO_CLAIMS:
        if re.search(pattern, text, re.I):
            out.append(Violation(
                "competitor_zero_untested",
                f"the artifact prints {label!r}, a tested competitive zero, "
                f"but the rival lane is {coverage['state']!r}: "
                f"{len(coverage['supplied'])} rival(s) supplied, "
                f"{coverage['queries']} rival quer(ies) run. An untested "
                f"lane may only say COMPETITIVE VALIDATION NEXT or PENDING"))
    return out


_CLIENT_FACING_INTERNALS: tuple[str, ...] = (
    "L2_competitor", "missing intake", "failed lane", "renderer regression",
    "empty competitor input", "entity bridge", "dev artifact",
    "no research entities",
)


def check_no_internal_defect_language(content: str) -> list[Violation]:
    """Internal defect talk belongs in sidecars and operator logs, never in
    client copy (gold-standard brief, 2026-08-04)."""
    text = _full_artifact_text(content)
    return [Violation(
        "client_facing_internal_language",
        f"client copy contains the internal phrase {phrase!r}; internal "
        f"state belongs in the sidecars, not the deliverable")
        for phrase in _CLIENT_FACING_INTERNALS
        if re.search(rf"\b{re.escape(phrase)}\b", text, re.I)]


_TARGETING_TABLE = 'data-targeting-table="1"'
_TARGETING_CONTACTS = 'data-targeting-contacts="1"'


def _evidence_band_ids() -> tuple:
    """Bands 01 to 07 by contract position: every numbered band except the
    hero, method and targeting. Derived, never listed, so an amendment that
    adds an evidence band widens the join surface automatically."""
    from agents.golden_press.contract import contract_section_ids
    return tuple(bid for bid in contract_section_ids()
                 if bid not in ("method", "targeting"))


def _section(content: str, band_id: str) -> str:
    m = re.search(rf'<section class="band" id="{band_id}".*?</section>',
                  content, re.S)
    return m.group(0) if m else ""


def check_targeting_band(content: str, pack: EvidencePack) -> list[Violation]:
    """Contract §2/09 + amendment v1.4, driven from the contract's own text.

    The postures and the admitted provenance classes are PARSED from the
    band's §2 laws (contract.band_postures / band_provenance_classes), so a
    renamed posture is a contract edit rather than a silent drift between
    the document and the checker.

    Three laws, mechanically:
      JOIN LAW    every record id the band prints also renders in bands 01
                  to 07; a contact hangs off a spec that hangs off a
                  rendered record, or it does not render at all.
      PROVENANCE  every rendered contact row carries an admitted provenance
                  class and a retrieved date. No provenance, no render.
      ZERO STATE  the band states one of the contract's own postures, or it
                  carries the spec table. An untested lane may never print
                  the tested zero.
    """
    from agents.golden_press.contract import (
        band_postures, band_provenance_classes)
    from tools.intelligence_graph.adapter import admissible_target_observations

    band = _section(content, "targeting")
    if not band:
        return []                       # band_grammar already reports absence
    out: list[Violation] = []
    postures = band_postures("targeting")
    classes = band_provenance_classes("targeting")

    # ZERO STATE: content or one of the contract's own postures, never a
    # bare empty band.
    if _TARGETING_TABLE not in band and not any(p in band for p in postures):
        out.append(Violation(
            "targeting_posture_missing",
            f"band 09 renders no targeting table and states none of the "
            f"contract's postures {list(postures)} (contract §2/09)"))

    # An untested lane never prints a tested zero: the certified
    # NO CONTACTS RESOLVED belongs only to a press whose pack carries an
    # enrichment receipt saying the supply step actually attempted.
    targeting = getattr(pack, "targeting", None) or {}
    enrichment = targeting.get("enrichment_receipt") or {}
    if "NO CONTACTS RESOLVED" in band and not enrichment.get("attempted"):
        out.append(Violation(
            "targeting_zero_untested",
            "band 09 prints NO CONTACTS RESOLVED, a tested zero, but the "
            "pack carries no enrichment receipt stating an attempt. An "
            "unattempted lane may only say TARGETING SUPPLY NOT RUN"))

    # JOIN LAW: every id the band prints renders in an evidence band too.
    evidence_html = "".join(_section(content, bid)
                            for bid in _evidence_band_ids())
    for record in pack.records:
        rid = str(record.record_id or "")
        if not rid or not _visible_token_positions(band, rid):
            continue
        if not _visible_token_positions(evidence_html, rid):
            out.append(Violation(
                "targeting_join_unrendered",
                f"band 09 hangs a call on {rid}, which no evidence band "
                f"renders (contract §2/09 join law)"))

    # PROVENANCE: one labeled source cell per rendered contact row.
    rows = re.findall(r"<tr[^>]*>.*?</tr>",
                      "".join(re.findall(
                          _TARGETING_CONTACTS + r".*?</table>", band, re.S)),
                      re.S)
    body_rows = [row for row in rows if "<td" in row]
    labels = tuple(c.upper() for c in classes)
    for row in body_rows:
        text = _visible_text(row)
        if not any(label in text for label in labels):
            out.append(Violation(
                "targeting_identity_without_provenance",
                f"a band 09 contact row states no provenance class from "
                f"{list(labels)} (contract §2/09: no provenance, no "
                f"render)"))
            continue
        if not any(re.fullmatch(r"\d{4}-\d{2}-\d{2}", key)
                   for _display, key in _stated_date_keys(text)):
            out.append(Violation(
                "targeting_identity_without_retrieved_date",
                "a band 09 contact row states a provenance class with no "
                "retrieved date (contract §2/09)"))

    # A rendered contact must survive the store's own admission laws, so a
    # renderer that stopped filtering cannot ship an unlabeled identity.
    kept, _dropped = admissible_target_observations(
        {"contacts": targeting.get("contacts") or []},
        spec_ids=[s.get("spec_id") for s in (targeting.get("specs") or [])])
    if len(body_rows) > len(kept):
        out.append(Violation(
            "targeting_contact_rows_exceed_admissible",
            f"band 09 renders {len(body_rows)} contact row(s) but only "
            f"{len(kept)} pack contact(s) pass the join and provenance "
            f"laws (contract §2/09)"))
    return out[:20]


def check_svg_geometry(content: str) -> list[Violation]:
    """A visual that silently drops content is worse than no visual.

    A negative or zero width/height rect is dropped by the SVG renderer,
    so a layout bug removed boxes from the delivered treemaps without any
    gate noticing (FiscalNote 2026-08-04: 2 of 6 prime boxes and 7 of 17
    agency boxes vanished). Geometry is now asserted on the artifact."""
    out: list[Violation] = []
    for match in re.finditer(r"<rect\b[^>]*>", content):
        tag = match.group(0)
        width = re.search(r'\bwidth="(-?[\d.]+)"', tag)
        height = re.search(r'\bheight="(-?[\d.]+)"', tag)
        for name, found in (("width", width), ("height", height)):
            if found and float(found.group(1)) <= 0:
                out.append(Violation(
                    "svg_geometry_non_positive",
                    f"a rect carries {name}={found.group(1)}; the renderer "
                    f"drops it and the visual silently loses that box"))
    return out


def validate_press(
    content: str,
    spliced: str,
    pack: EvidencePack,
) -> dict:
    """The full battery. Returns {'ok': bool, 'violations': [...]}."""
    violations: list[Violation] = []
    violations += check_sentinel(spliced)
    violations += check_well_formed(spliced)
    violations += check_band_grammar(content, pack)
    violations += check_emdash(content)
    violations += check_banned_vocabulary(content)
    violations += check_dollars(content, pack)
    violations += check_dates(content, pack)
    violations += check_contract_numbers(content, pack)
    violations += check_repeated_prose(content)
    violations += check_links(content, pack)
    violations += check_lane_representation(content, pack)
    violations += check_aggregate_justification(content, pack)
    violations += check_events(content, pack)
    violations += check_expired_flags(content, pack)
    violations += check_hero_proof(content, pack)
    violations += check_shapeability_decay(content, pack)
    violations += check_required_fields(content, pack)
    violations += check_svg_geometry(content)
    violations += check_competitor_claim_is_tested(content, pack)
    violations += check_targeting_band(content, pack)
    violations += check_no_internal_defect_language(content)
    violations += check_client_purity(client_export(content))
    return {"ok": not violations, "violations": violations}
