"""Claim-level QA remediation — the graceful layer that NEVER blocks a render.

Design rule (operator spec, 2026-07-09): the pipeline ALWAYS produces a
rendered report. QA failures degrade the failing ELEMENT, never the document.
Every rule maps to one of three actions:

  AUTO-FIX  — deterministic correction, applied silently, logged.
  SUPPRESS  — an unverifiable claim does not render; its container degrades
              to the verified parts (or a visible provenance tag).
  FLAG      — needs a human; renders in DRAFT with an inline ochre
              [REVIEW: rule: reason] marker beside the element.

SAFETY VALVE: every rule runs in isolation. A rule that throws converts to a
FLAG on the document and the element renders unmodified — a bug in a lint
rule can never break a report. `LILA_QA_SABOTAGE=<rule>` forces a rule to
throw, proving the valve live.

Rules R1-R10 (operator numbering):
  R1 rank-of-zero        auto-fix   "#N of 0" -> recomputed denominator or "#N by dollars"
  R2 zero-vs-evidence    suppress   count 0 contradicted by cited facts -> derived value or flag
  R3 superlative-vs-rank suppress   downgrade to "among the largest", logged
  R4 unlabeled stat      suppress   median/mean with no population label dropped from the card
  R5 uncited section     suppress   visible per-section provenance tag
  R6 snapshot mismatch   flag       (needs snapshot infra; edge cases flag)
  R7 field bleed         auto-fix   preparer/date strings stripped from recipient field
  R8 cross-section contradiction  flag
  R9 banned strings      auto-fix   em/en dashes replaced per house style
  R10 unclassifiable     flag       the isolation valve's catch-all
"""

from __future__ import annotations

import os
import re
from typing import Callable, Literal

from pydantic import BaseModel, Field

from agents.reports.facts import FactPack

QAActionKind = Literal["auto_fix", "suppress", "flag"]

_COUNT_TOKEN_RE = re.compile(r"\{\{COUNT(?:_WORD)?:(\w+)\}\}")
_RANK_ZERO_RE = re.compile(r"#(\d+) of (?:0|zero)\b", re.I)
_RANKED_OF_RE = re.compile(r"ranked #\d+ of (\d+)")
_SUPERLATIVE_RE = re.compile(
    r"\b(top|largest|leading|leads|biggest|number one)\b|#1\b", re.I)
_CITED_RANK_RE = re.compile(r"ranked #([2-9]|\d{2,}) of \d+")
_MEDIAN_RE = re.compile(r"\b(median|mean|average)\b", re.I)
_POPULATION_RE = re.compile(
    r"top[- ]\d+|largest awards|of th(?:at|ose|e) \d+|slice|per \w+|across the \d+", re.I)
_DASH_RE = re.compile(r"\s*[—–]\s*")
_PREPARER_RE = re.compile(
    r"\s*(?:·\s*)?(?:prepared by[^·]*|GTM Group|Tyler Johnson|20\d\d-\d\d-\d\d)\s*",
    re.I)


class QAAction(BaseModel):
    rule: str
    action: QAActionKind
    location: str
    reason: str
    before: str = ""
    after: str = ""


class QAReport(BaseModel):
    actions: list[QAAction] = Field(default_factory=list)

    @property
    def flags(self) -> list[QAAction]:
        return [a for a in self.actions if a.action == "flag"]

    @property
    def has_flags(self) -> bool:
        return bool(self.flags)

    def add(self, rule: str, action: QAActionKind, location: str, reason: str,
            before: str = "", after: str = "") -> None:
        self.actions.append(QAAction(rule=rule, action=action, location=location,
                                     reason=reason, before=before[:200],
                                     after=after[:200]))

    def to_markdown(self) -> str:
        if not self.actions:
            return "# QA log\n\nClean pass: zero interventions.\n"
        lines = ["# QA log", ""]
        for a in self.actions:
            lines.append(f"- **{a.rule}** [{a.action}] at `{a.location}`: {a.reason}")
            if a.before:
                lines.append(f"  - before: {a.before}")
            if a.after:
                lines.append(f"  - after: {a.after}")
        return "\n".join(lines) + "\n"


# marker embedded in content strings for DRAFT rendering; the renderer wraps
# it in the ochre span. RELEASE requires zero flags, so it can never ship.
def flag_marker(rule: str, reason: str) -> str:
    return f" [REVIEW: {rule}: {reason}]"


def _guard(rule: str, report: QAReport, location: str, fn: Callable[[], None]) -> None:
    """SAFETY VALVE: a rule that throws becomes a FLAG; the element renders
    unmodified. LILA_QA_SABOTAGE=<rule> forces the throw (acceptance test)."""
    try:
        if os.environ.get("LILA_QA_SABOTAGE", "").strip() == rule:
            raise RuntimeError("sabotaged on purpose (LILA_QA_SABOTAGE)")
        fn()
    except Exception as e:  # noqa: BLE001 — the valve exists for ANY failure
        report.add(rule, "flag", location,
                   f"QA rule errored ({e}); element rendered unmodified")


def _walk_strings(c) -> list[tuple[str, str, Callable[[str], None]]]:
    """(location, value, setter) for every remediable composed string."""
    out: list[tuple[str, str, Callable[[str], None]]] = []

    def _lst(name, lst, attr):
        for i, item in enumerate(lst):
            out.append((f"{name}[{i}].{attr}", getattr(item, attr),
                        lambda v, item=item, attr=attr: setattr(item, attr, v)))

    for i, t in enumerate(c.thesis):
        out.append((f"thesis[{i}]", t,
                    lambda v, i=i: c.thesis.__setitem__(i, v)))
    for i, s in enumerate(c.stats):
        out.append((f"stats[{i}].context", s.context,
                    lambda v, s=s: setattr(s, "context", v)))
    for name, attr in (("action_callout", "action_callout"),
                       ("kill_line_opps", "kill_line_opps"),
                       ("kill_line_news", "kill_line_news"),
                       ("partner_callout", "partner_callout"),
                       ("footer_verification", "footer_verification")):
        out.append((name, getattr(c, attr),
                    lambda v, attr=attr: setattr(c, attr, v)))
    for gname, group in (("news_funding", c.news_funding),
                         ("news_threat", c.news_threat),
                         ("news_agency", c.news_agency),
                         ("news_market", c.news_market)):
        _lst(gname, group, "why")
    _lst("opportunities", c.opportunities, "body")
    _lst("pipeline", c.pipeline, "body")
    return out


# ── rules ────────────────────────────────────────────────────────────────────

def _r1_rank_of_zero(c, pack: FactPack, counts: dict, report: QAReport) -> None:
    """AUTO-FIX: '#N of 0' -> recomputed denominator from the ranked facts,
    or '#N by dollars' when uncomputable."""
    denoms = {int(m) for f in pack.facts for m in _RANKED_OF_RE.findall(f.text)}
    denom = max(denoms) if len(denoms) == 1 else None

    def _fix(loc, val, setter):
        resolved = _COUNT_TOKEN_RE.sub(
            lambda m: str(counts.get(m.group(1), 0)), val)
        if not _RANK_ZERO_RE.search(resolved):
            return
        repl = (lambda m: f"#{m.group(1)} of {denom}") if denom else \
               (lambda m: f"#{m.group(1)} by dollars")
        fixed = _RANK_ZERO_RE.sub(repl, resolved)
        setter(fixed)
        report.add("R1", "auto_fix", loc, "rank denominator was zero",
                   before=val, after=fixed)

    for loc, val, setter in _walk_strings(c):
        _guard("R1", report, loc, lambda lc=loc, v=val, s=setter: _fix(lc, v, s))


def _r2_zero_vs_evidence(c, pack: FactPack, counts: dict, report: QAReport) -> None:
    """A stat resolving to 0 while citing populated facts: derive the value
    from the cited facts when unambiguous, else FLAG for a human."""
    for i, s in enumerate(c.stats):
        loc = f"stats[{i}]"

        def _fix(i=i, s=s, loc=loc):
            resolved = _COUNT_TOKEN_RE.sub(
                lambda m: str(counts.get(m.group(1), 0)), s.number).strip()
            if resolved.lstrip("$").rstrip("%+").strip() not in ("0", "0.0", "zero"):
                return
            cited = re.findall(r"F\d+", f"{s.number} {s.accent} {s.context}")
            if not cited:
                return
            # unambiguous derivation: every cited rank fact agrees on one pool size
            denoms = {int(m) for fid in cited
                      for f in [pack.get(fid)] if f
                      for m in _RANKED_OF_RE.findall(f.text)}
            if len(denoms) == 1:
                derived = str(denoms.pop())
                report.add("R2", "suppress", loc,
                           "zero count contradicted by cited facts; "
                           "fact-derived value rendered instead",
                           before=s.number, after=derived)
                s.number = derived
            else:
                s.context += flag_marker(
                    "R2", "count renders 0 but cited facts are populated")
                report.add("R2", "flag", loc,
                           "zero count contradicts cited facts and no "
                           "unambiguous derivation exists", before=s.number)

        _guard("R2", report, loc, _fix)


_DOWNGRADES = [(re.compile(r"\bthe largest\b", re.I), "among the largest"),
               (re.compile(r"\blargest\b", re.I), "among the largest"),
               (re.compile(r"\btop\b", re.I), "leading"),
               (re.compile(r"\bleads\b", re.I), "ranks among the leaders"),
               (re.compile(r"\bbiggest\b", re.I), "among the biggest"),
               (re.compile(r"#1\b|\bnumber one\b", re.I), "front-rank")]


def _r3_superlative(c, pack: FactPack, counts: dict, report: QAReport) -> None:
    """SUPPRESS: a superlative contradicted by its cited fact's stated rank
    downgrades to the neutral form; the true rank is in the fact, cited."""
    def _fix(loc, val, setter):
        out, changed = [], False
        for sentence in re.split(r"(?<=[.;])\s+", val):
            hit = _SUPERLATIVE_RE.search(sentence)
            if hit:
                for fid in re.findall(r"\[(F\d+)\]", sentence):
                    f = pack.get(fid)
                    m = _CITED_RANK_RE.search(f.text) if f else None
                    if m and m.group(0).split("ranked ")[-1] not in sentence:
                        before = sentence
                        for rx, neutral in _DOWNGRADES:
                            if rx.search(sentence):
                                sentence = rx.sub(neutral, sentence, count=1)
                                break
                        changed = True
                        report.add("R3", "suppress", loc,
                                   f"superlative contradicted {fid} "
                                   f"('{m.group(0)}'); downgraded to neutral",
                                   before=before, after=sentence)
                        break
            out.append(sentence)
        if changed:
            setter(" ".join(out))

    for loc, val, setter in _walk_strings(c):
        _guard("R3", report, loc, lambda lc=loc, v=val, s=setter: _fix(lc, v, s))


def _r4_unlabeled_stat(c, pack: FactPack, counts: dict, report: QAReport) -> None:
    """SUPPRESS: a median/mean with no population label is dropped from the
    card; the card falls back to its remaining verified parts."""
    for i, s in enumerate(c.stats):
        loc = f"stats[{i}]"

        def _fix(i=i, s=s, loc=loc):
            blob = f"{s.number} {s.context}"
            if not _MEDIAN_RE.search(blob) or _POPULATION_RE.search(blob):
                return
            sentences = [x for x in re.split(r"(?<=[.;,])\s+", s.context)
                         if not _MEDIAN_RE.search(x)]
            remainder = " ".join(sentences).strip(" ,;")
            if _MEDIAN_RE.search(s.number) or not remainder:
                s.context += flag_marker(
                    "R4", "median/mean with no population label")
                report.add("R4", "flag", loc,
                           "median/mean IS the card value and carries no "
                           "population label; needs a human", before=blob)
            else:
                report.add("R4", "suppress", loc,
                           "median/mean clause without a population label "
                           "dropped; card degrades to verified parts",
                           before=s.context, after=remainder)
                s.context = remainder

        _guard("R4", report, loc, _fix)


def _r5_uncited_sections(c, pack: FactPack, counts: dict, report: QAReport) -> None:
    """SUPPRESS: a composed news section with zero fact/URL grounding gets a
    visible provenance tag (rendered by the renderer per location)."""
    for gname, group in (("news_funding", c.news_funding),
                         ("news_threat", c.news_threat),
                         ("news_agency", c.news_agency),
                         ("news_market", c.news_market)):
        def _fix(gname=gname, group=group):
            if not group:
                return
            grounded = any(re.search(r"\[F\d+\]", f"{n.headline} {n.why}")
                           or n.url.startswith("http") for n in group)
            if not grounded:
                report.add("R5", "suppress", f"section:{gname}",
                           "section carries no citations or source URLs; "
                           "rendered with a provenance tag")
        _guard("R5", report, f"section:{gname}", _fix)


def _r6_snapshot(c, pack: FactPack, counts: dict, report: QAReport) -> None:
    """FLAG: snapshot-vs-current mismatches need snapshot infrastructure;
    until it exists this rule only guards its own execution."""
    def _fix():
        return  # no snapshot source wired yet — nothing to compare
    _guard("R6", report, "document", _fix)


def _r7_field_bleed(c, pack: FactPack, counts: dict, report: QAReport) -> None:
    """AUTO-FIX: preparer/date strings stripped from the recipient field."""
    def _fix():
        before = c.meta_prepared_for
        after = _PREPARER_RE.sub(" ", before).strip(" ·,;-")
        if after and after != before:
            c.meta_prepared_for = after
            report.add("R7", "auto_fix", "meta_prepared_for",
                       "preparer/date bleed stripped from recipient field",
                       before=before, after=after)
    _guard("R7", report, "meta_prepared_for", _fix)


def _r8_cross_section(c, pack: FactPack, counts: dict, report: QAReport) -> None:
    """FLAG: a 'zero <noun>' claim while sibling copy quantifies the same
    noun positively is a self-refuting document; a human resolves it."""
    zero_re = re.compile(r"\b(?:zero|no)\s+(primes?|subawards?|incumbents?|"
                         r"competitors?|vehicles?)\b", re.I)
    strings = _walk_strings(c)

    def _fix():
        blob = " ".join(v for _, v, _ in strings)
        for m in zero_re.finditer(blob):
            noun = m.group(1).rstrip("s")
            if re.search(rf"\b[1-9]\d*\s+(?:\w+\s+)?{noun}", blob, re.I) or \
               re.search(rf"\$[\d,.]+[MBK]?\b.{{0,40}}{noun}", blob, re.I):
                for loc, val, setter in strings:
                    if m.group(0) in val and flag_marker("R8", "")[:9] not in val:
                        setter(val + flag_marker(
                            "R8", f"'{m.group(0)}' contradicted elsewhere in "
                                  "the document"))
                        report.add("R8", "flag", loc,
                                   f"'{m.group(0)}' contradicted by sibling "
                                   "copy quantifying the same noun",
                                   before=val[:120])
                        break
    _guard("R8", report, "document", _fix)


def _r9_banned_strings(c, pack: FactPack, counts: dict, report: QAReport) -> None:
    """AUTO-FIX: em/en dashes replaced per house style (comma-space)."""
    def _fix(loc, val, setter):
        if not _DASH_RE.search(val):
            return
        fixed = _DASH_RE.sub(", ", val)
        setter(fixed)
        report.add("R9", "auto_fix", loc, "em/en dash replaced per house style",
                   before=val, after=fixed)

    for loc, val, setter in _walk_strings(c):
        _guard("R9", report, loc, lambda lc=loc, v=val, s=setter: _fix(lc, v, s))


_NAICS_LANE_RE = re.compile(r"NAICS\s?\d{6}", re.I)
_MONEY_RE = re.compile(r"\$\s?[\d,.]+\s?(?:billion|million|[BMK])?", re.I)
_LANE_LABEL = "lane-level evidence only"


def _r11_lane_demotion(c, pack: FactPack, counts: dict, report: QAReport) -> None:
    """NAICS DEMOTION (2026-07-09 inversion): lane totals may not render as
    stat cards, budget bars, or thesis support; they survive only as context.
    Also enforces the MANDATORY, un-suppressible lane-only label on any
    sentence citing a lane-fallback fact."""
    def _bars():
        keep = []
        for b in c.budget_bars:
            blob = f"{b.label} {b.amount_label}"
            if _NAICS_LANE_RE.search(blob):
                report.add("R11", "suppress", "budget_bars",
                           "NAICS lane total dropped from money-in-motion",
                           before=blob)
            else:
                keep.append(b)
        c.budget_bars = keep
    _guard("R11", report, "budget_bars", _bars)

    for i, s in enumerate(c.stats):
        def _stat(i=i, s=s):
            blob = f"{s.number} {s.accent} {s.context}"
            if _NAICS_LANE_RE.search(blob) and _MONEY_RE.search(blob):
                s.context += flag_marker(
                    "R11", "NAICS lane total rendered as a stat card")
                report.add("R11", "flag", f"stats[{i}]",
                           "lane total as a stat card; lane totals are "
                           "boundary context only", before=blob[:140])
        _guard("R11", report, f"stats[{i}]", _stat)

    for i, t in enumerate(c.thesis):
        def _th(i=i, t=t):
            if _NAICS_LANE_RE.search(t) and _MONEY_RE.search(t):
                c.thesis[i] = t + flag_marker(
                    "R11", "lane total used as thesis support")
                report.add("R11", "flag", f"thesis[{i}]",
                           "lane total used as thesis support; lane totals "
                           "are boundary context only", before=t[:140])
        _guard("R11", report, f"thesis[{i}]", _th)

    if pack is None:
        return
    lane_fids = {f.id for f in pack.facts
                 if _LANE_LABEL.upper() in f.text.upper()}
    if not lane_fids:
        return

    def _delete_lane_claims(loc, val, setter):
        """CLIENT-FILE DOCTRINE (2026-07-10): a claim resting on lane-only
        evidence is not caveated in the client file, it is DELETED; the
        internal trail carries the full text."""
        out, changed = [], False
        for sentence in re.split(r"(?<=[.;])\s+", val):
            fids = set(re.findall(r"\[(F\d+)\]", sentence))
            if fids & lane_fids:
                changed = True
                report.add("R11", "suppress", loc,
                           "claim citing lane-fallback evidence deleted from "
                           "the client file (internal trail carries it)",
                           before=sentence[:200])
                continue
            out.append(sentence)
        if changed:
            setter(" ".join(out).strip())

    for loc, val, setter in _walk_strings(c):
        _guard("R11", report, loc,
               lambda lc=loc, v=val, s=setter: _delete_lane_claims(lc, v, s))


_SELF_GRADING_RE = re.compile(
    r"\b(?:unverified|not (?:capability-)?verified|did not verify|"
    r"pending verification|no record(?:s)? (?:found|returned|exists)?|"
    r"returned no record|zero (?:of \d+ )?match|no evidence (?:found|of)|"
    r"could not confirm|lane-level evidence only|not shown|"
    r"accounted for: that screen|carries no description evidence)\b", re.I)


def _r14_client_file_doctrine(c, pack: FactPack, counts: dict,
                              report: QAReport) -> None:
    """CLIENT-FILE DOCTRINE backstop (2026-07-10): nothing that grades our
    own prior work renders in the client file; a negative never renders as
    a negative. Sentences that slip past the composer and editor are
    DELETED here and logged verbatim for the internal file. Elements that
    ARE the negative (a news item, a pipeline card) drop whole; a stat card
    (fixed at five) flags for a human instead."""
    for gname, group in (("news_funding", c.news_funding),
                         ("news_threat", c.news_threat),
                         ("news_agency", c.news_agency),
                         ("news_market", c.news_market),
                         ("pipeline", c.pipeline)):
        def _drop(gname=gname, group=group):
            keep = []
            for item in group:
                blob = " ".join(str(getattr(item, a, "")) for a in
                                ("headline", "why", "body"))
                if _SELF_GRADING_RE.search(blob):
                    report.add("R14", "suppress", gname,
                               "self-grading/negative element deleted from "
                               "the client file (internal trail carries it)",
                               before=blob[:200])
                else:
                    keep.append(item)
            group[:] = keep
        _guard("R14", report, gname, _drop)

    def _scrub(loc, val, setter):
        out, changed = [], False
        for sentence in re.split(r"(?<=[.;])\s+", val):
            if _SELF_GRADING_RE.search(sentence):
                changed = True
                report.add("R14", "suppress", loc,
                           "self-grading sentence deleted from the client "
                           "file (internal trail carries it)",
                           before=sentence[:200])
                continue
            out.append(sentence)
        remainder = " ".join(out).strip()
        if changed and remainder:
            setter(remainder)
        elif changed:
            setter(val)  # element IS the negative: leave for a human
            report.add("R14", "flag", loc,
                       "entire element is self-grading/negative; needs a "
                       "human rewrite to a forward action")

    for i, s in enumerate(c.stats):
        def _stat_check(i=i, s=s):
            blob = f"{s.number} {s.accent} {s.context}"
            if _SELF_GRADING_RE.search(blob):
                report.add("R14", "flag", f"stats[{i}]",
                           "stat card carries self-grading language; needs "
                           "a human rewrite to a forward action",
                           before=blob[:160])
        _guard("R14", report, f"stats[{i}]", _stat_check)
    for loc, val, setter in _walk_strings(c):
        if loc.startswith("stats["):
            continue
        _guard("R14", report, loc,
               lambda lc=loc, v=val, s=setter: _scrub(lc, v, s))


_DATE_RE = re.compile(
    r"\b(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*\.? "
    r"\d{1,2},? (20\d\d)\b|\b(20\d\d)-(\d\d)-(\d\d)\b")
_FORWARD_FRAME_RE = re.compile(
    r"\b(since|in force|enacted|effective|issued|published|standing|dated|"
    r"as of|ago|due|deadline|responses|closes|ends|expir|complet|through|"
    r"window)\b", re.I)


def _r15_present_forward(c, pack: FactPack, counts: dict,
                         report: QAReport) -> None:
    """PRESENT-FORWARD DATE DISCIPLINE (2026-07-10): a bare past date in
    client copy reads as a stale or past-due listing (the NETSCOUT DHS
    signal table read as past-due solicitations). Past dates must carry
    status framing (in force since, enacted, standing gap since) or recency
    language; a slip FLAGS for a human rewrite, because the right framing
    (mandate in force vs. report published vs. window closed) is judgment."""
    as_of = pack.as_of.isoformat() if pack and pack.as_of else "9999-12-31"

    def _past(m: re.Match) -> bool:
        try:
            if m.group(2):  # ISO form
                return f"{m.group(2)}-{m.group(3)}-{m.group(4)}" < as_of
            from datetime import datetime
            iso = datetime.strptime(
                re.sub(r"(?<=[a-z])\.", "", m.group(0)).replace(",", ""),
                "%b %d %Y" if len(m.group(0).split()[0]) <= 4 else "%B %d %Y"
            ).date().isoformat()
            return iso < as_of
        except Exception:  # noqa: BLE001 — unparseable is not a violation
            return False

    def _check(loc, val, setter):
        for sentence in re.split(r"(?<=[.;])\s+", val):
            hits = [m for m in _DATE_RE.finditer(sentence) if _past(m)]
            if hits and not _FORWARD_FRAME_RE.search(sentence):
                report.add("R15", "flag", loc,
                           "bare past date without present-forward framing "
                           "(reads as stale/past-due); rewrite with status "
                           "language: in force since, enacted, standing "
                           "since, or recency", before=sentence[:180])

    for loc, val, setter in _walk_strings(c):
        _guard("R15", report, loc, lambda lc=loc, v=val, s=setter: _check(lc, v, s))


_RULES = [_r1_rank_of_zero, _r2_zero_vs_evidence, _r3_superlative,
          _r4_unlabeled_stat, _r5_uncited_sections, _r6_snapshot,
          _r7_field_bleed, _r8_cross_section, _r9_banned_strings,
          _r11_lane_demotion, _r14_client_file_doctrine,
          _r15_present_forward]


def remediate(content, pack: FactPack, counts: dict) -> tuple[object, QAReport]:
    """Run every rule over a deep copy of the content. ALWAYS returns a
    renderable document — the outer guard means even a rule-list bug
    degrades to a flag, never an exception."""
    c = content.model_copy(deep=True)
    report = QAReport()
    for rule_fn in _RULES:
        rule_id = rule_fn.__name__.split("_")[1].upper()
        _guard(rule_id if rule_id.startswith("R") else "R10", report, "document",
               lambda fn=rule_fn: fn(c, pack, counts, report))
    return c, report
