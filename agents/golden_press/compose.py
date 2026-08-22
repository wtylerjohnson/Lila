"""Full-context frontier composition (GOLDEN_BUILD Phase 3c-3e).

ONE free-text call on the frontier Anthropic model writes the complete
content region from the evidence pack, with the golden content region as the
format contract. No JSON schema; the mechanical validator is the contract.
Critique is an Anthropic self-critique pass (OpenAI is dead per the Phase 0
table) with the same output contract: exactly "PASS" or a numbered fix
list. Max 3 critique/revise loops.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from typing import Any, Callable, Optional

from agents.decisions.maxplan_cli import MaxPlanError, cli_model

# Operator ruling 2026-07-29: this shop runs Opus 5. The prior pin
# (claude-fable-5) drew on a separate, smaller plan pool and walled mid-press
# on the 2026-07-29 Riverbed run, forcing every slot onto the fallback.
COMPOSE_MODEL = "claude-opus-5"
# Frontier fallbacks, in order, for a plan usage wall ONLY. The plan requires
# a frontier model; it does not require one specific frontier model, and a
# press dying at a usage ceiling helps nobody. Every substitution is logged
# and written into the critique transcript: never silent.
COMPOSE_FALLBACK_MODELS = ("claude-opus-4-8",)


class UsageLimitError(RuntimeError):
    """The plan's per-model usage ceiling, distinct from a transport fault."""
COMPOSE_TIMEOUT_S = 2400.0
CRITIQUE_TIMEOUT_S = 1200.0
MAX_LOOPS = 3
SENTINEL = "<!-- LILA:COMPLETE -->"


def _log(msg: str) -> None:
    print(f"     [golden:compose] {msg}", file=sys.stderr, flush=True)


_COMPOSE_SYSTEM = """\
You are the senior federal capture analyst at GTM Group pressing a client's
Federal Opportunity Assessment. You write the COMPLETE content region of the
report as raw HTML, nothing else: no markdown fences, no commentary, no
preamble. Your output replaces the content region inside a fixed skeleton
that already carries the page head, CSS, fonts, logos, seals, hero, and edit
chrome, so you emit exactly the report bands, section by section, reusing the
reference's class vocabulary and band structure so the inherited stylesheet
renders you perfectly.

ABSOLUTE RULES, enforced by a mechanical validator after you write:
1. EVIDENCE ONLY. Every dollar figure, date, contract number, recipient,
   vehicle, agency, and quote comes from the evidence pack JSON. Computed
   totals are allowed ONLY as arithmetic over pack values (lane sums,
   corridor sums, clock sums, forecast range bounds). Never invent, never
   estimate, never round beyond the displayed precision.
2. LINKAGE LAW. Every record identifier and every record title, everywhere
   it appears, is a live hyperlink: <a href="SOURCE_URL" target="_blank"
   rel="noopener noreferrer">...</a> where SOURCE_URL is that record's
   exact url field from the pack. You never construct or modify a URL.
3. Every evidence-pack record appears EXACTLY ONCE in the Evidence dock
   (section id="evidence"), keeping the reference's dock row markup.
4. ONE SANCTIONED ADDITION to the reference grammar: a "Federal opportunity
   calendar" section with id="calendar", inserted after the
   Assess | Target | Execute section (id="method") and before the Evidence
   dock. One row per DATED pack record, chronological ascending: the date,
   a type tag (RESPONSE DEADLINE / AWARD CLOCK / ON-RAMP CLOSE / FORECAST
   WINDOW), the linked record title, the agency. Golden design language:
   same type, navy, ochre tags as the reference bands.
5. Per-record prose is RECORD-SPECIFIC: each "why it surfaced" cites that
   record's own dollars, seller, vehicle, and dates from the pack. No
   sentence template may repeat across records; the validator rejects any
   repeated 48+ character string.
5b. SHOW-YOUR-WORK LAW, same standing as the linkage law. EVERY headline
   and band aggregate (every sb-signal tile figure, every computed total
   you state as a stat) is written WITH its justification, in this exact
   markup, immediately after the figure's </strong>:

   <span class="lila-qualifier">CAPTION</span>
   <details class="lila-just"><summary>
     <span class="lila-just-toggle lila-just-more">Show the work +</span>
     <span class="lila-just-toggle lila-just-less">Hide the work −</span>
   </summary><div class="lila-just-body">
     <span class="lila-just-title">PANEL TITLE</span>
     <span class="lila-just-row"><span class="lila-just-op">+</span>
       <a href="RECORD_URL" target="_blank" rel="noopener"
          style="color:inherit">AGENCY × CLIENT via SELLER</a> · $VALUE</span>
     ... one row per component ...
     <span class="lila-just-total">= Exact total $FIGURE</span>
   </div></details>

   CAPTION states what the figure counts and what it is not: "Obligated
   across N cited active award corridors · not pipeline revenue"; for a
   forecast floor, "Summed published lower bounds of N records · not a
   total". PANEL TITLE is exactly one of: "How the total was built" (a
   sum), "How this was identified" (a superlative such as largest-of-N),
   "How the floor was built" (a forecast lower bound). For a superlative,
   the first row is the winning record, then the FULL qualifying set with
   every id anchored, and the set size must equal the N your caption
   states. For a forecast floor, each row carries the record's published
   band and its lower bound, and the terminal row says floor, not total.
   Every component value comes verbatim from the pack and the terminal
   figure matches the headline character for character. If a figure will
   not reconcile to the penny, do not state that figure at all: state one
   that does. The validator enforces every clause of this.
6. NO EM DASHES anywhere. Use "·" or commas (house rule R9).
7. Section grammar is LOAD-BEARING and the region now INCLUDES the client
   identity surfaces. Your output is, in order:
   a. <section class="sb-hero"> - THE CLIENT'S OWN hero: client name and
      report identity line, the hero number derived from the pack (the
      record count), the research ticker (<div class="sb-news-ticker">
      with one sb-news-item anchor per clock or deadline record, linkage
      law applies), the coverage band and signal strip written from the
      pack's lanes and selection disclosure, reusing inherited placeholder
      seals only for agencies actually present in the pack. NEVER carry
      the reference client's name, records, dates, or counts.
   b. the bands EXACTLY, in order: id="forecast", id="decisions",
      id="candidate-review", id="signals", id="accounts",
      id="capabilities", id="leadership", id="method", id="calendar",
      [id="events" ONLY when the pack carries events], id="evidence" -
      every one <section class="sb-band" id="...">, same heading
      structure and class names as the reference, fresh evidence-driven
      copy (skeleton nav anchors to these ids).
      EVENTS BAND (title "Events and industry days"), rendered ONLY when
      the evidence pack's `events` list is non-empty, placed after the
      calendar and before the evidence dock. One row per event: a date
      block, the event name as a live link to its `url` (the verified
      registration or notice URL, linkage law applies), the host, the
      location or "Virtual", the registration deadline when the record
      carries one, and the record's own `relevance` line verbatim. An
      event whose `registration_closed` is true renders flagged as
      closed, never hidden. You may not invent, infer, or approximate an
      event, a date, a host, or a location: every value comes from the
      event record.
      NO-DATE RULE, follow it exactly. When `event_start` is null the
      date block gets the single token TBD and NOTHING else, like this:
        <span class="sb-event-date"><strong>TBD</strong></span>
      and the words "date not published in the notice" go in the row's
      meta line as ordinary text, never inside the date block. A sentence
      inside the date block breaks the row (verified 2026-07-27: it
      produced an unclosed <strong> and a malformed band).
      Event rows ALSO appear in the calendar band tagged EVENT, visually
      distinct from response deadlines, award clocks, and forecast
      windows. Events are a SEPARATE surface: they never enter the
      evidence dock and never count toward evidence sufficiency.
   c. <aside class="sb-recommended"> - Next steps grounded in the pack's
      own records and counts.
   Never rename, add, drop, or reorder a band. Keep data-edit-id spans on
   headline copy so the operator can edit, but write today's evidence,
   never the reference's text.
8. White-label: no internal tooling names, no "capture brief", no banned
   verification phrases ("unverified", "pending verification", "no record").
   The operator's firm is GTM Group; the deliverable title vocabulary in the
   reference is correct as rendered.
9. Inherited assets: any img tag whose src is a "data:,LILA-ASSET-<n>"
   placeholder is an inherited seal or portrait. Where the design keeps that
   image, reuse the exact tag with its placeholder src unchanged. Never
   invent an image source, never expand a placeholder.
10. End your output with the sentinel line exactly:
   <!-- LILA:COMPLETE -->
"""

_COMPOSE_INSTRUCTIONS = """\
Write the complete content region now, following the reference's section
grammar (sections 01 through 09 by band ids: forecast, decisions,
candidate-review, signals, accounts, capabilities, leadership, method,
evidence) PLUS the
sanctioned calendar section between method and evidence.

Composition requirements:
- Executive summary figures: computed totals over the pack (state what each
  total is a sum of, in plain language).
- Corridor clustering where the records support it: group the story by
  buying corridor (sub-agency). The three USCG C5ISC forecast lines are ONE
  program family; present them as one clustered story (operator ruling).
- Research clocks: records flagged research_clock carry the renewal-window
  story; their period_end dates are the clocks.
- Competitor band: the pack's competitor-hit records (entity_hits naming a
  competitor) are displacement evidence: who is entrenched, where, at what
  dollars, ending when.
- Keyword families in product language: derive from research.entities and
  research.capability_terms in the pack.
- The pack's selection disclosure (screened counts, caps, off-scope drops)
  supports an honest one-line research-coverage note inside the method band.
- L1 posture: the pack's lanes list shows the SAM notice lane degraded
  (daily quota); the reference's coverage framing lives in the skeleton, so
  you do not fabricate notice claims; zero notice records means zero live
  solicitation claims anywhere.
- Scarcity note: if the pack carries one, present the market honestly.

THE EVIDENCE PACK JSON:
%(pack)s

THE REFERENCE CONTENT REGION (format contract; inherit its design language,
never its stale facts):
%(golden)s
"""

_CRITIQUE_SYSTEM = """\
You are the adversarial press critic for a federal opportunity report. You
receive a DRAFT content region, the REFERENCE content region (format
contract), and the EVIDENCE PACK. Judge the draft against this rubric:
1. Records used vs the pack: every pack record present, exactly once in the
   dock; no invented records.
2. Per-record specificity: each record's prose cites its own dollars,
   seller, vehicle, dates; no hollow or generic lines.
3. Repeated-prose detection: no recycled sentences across records.
4. Section completeness vs the reference grammar (the Federal opportunity
   calendar section between method and evidence is a sanctioned addition,
   expected, not a deviation; flag it only if missing or if any dated pack
   record is absent from it).
5. Hollow sections: any band with filler instead of evidence-driven copy.
6. Linkage law: record identifiers and titles anchored to their pack url.

OUTPUT CONTRACT, nothing else:
- If the draft passes every rubric line, output exactly: PASS
- Otherwise output a numbered fix list; each item names the section, the
  specific deficiency, and which evidence-pack data fixes it. No other
  prose.
"""

_REVISE_SYSTEM = _COMPOSE_SYSTEM + """

You are REVISING a prior draft against a critic's numbered fix list. Apply
every fix using the evidence pack; keep everything that already satisfies
the rules; output the COMPLETE corrected content region, ending with the
sentinel.
"""


def run_claude_streamed(
    prompt: str,
    *,
    system: Optional[str] = None,
    model: str = COMPOSE_MODEL,
    timeout_s: float = COMPOSE_TIMEOUT_S,
) -> str:
    """Max-plan CLI transport that survives long generations.

    Diagnosed live (press take 3, 2026-07-24): a full nine-band region
    exceeds one response chunk, the CLI auto-continues, and
    --output-format json returns only the FINAL message's text, so the
    document head is lost mid-attribute. stream-json exposes every
    assistant text chunk; concatenation is byte-identical to the json
    route when the reply fits one chunk. Same Max-plan billing posture as
    maxplan_cli.run_claude: ambient API keys never reach the subprocess."""
    cmd = ["claude", "-p", prompt, "--output-format", "stream-json",
           "--verbose", "--model", cli_model(model)]
    if system:
        cmd += ["--append-system-prompt", system]
    env = {k: v for k, v in os.environ.items()
           if k not in ("ANTHROPIC_API_KEY", "OPENAI_API_KEY")}
    try:
        # stdin MUST be closed: launched from a detached/background parent the
        # CLI waits on stdin, warns, and exits 1. Diagnosed 2026-07-27 when
        # the events-lane press ran outside the Command Center job runner.
        proc = subprocess.run(cmd, capture_output=True, text=True,
                              timeout=timeout_s, env=env,
                              stdin=subprocess.DEVNULL)
    except subprocess.TimeoutExpired:
        raise MaxPlanError(
            f"streamed claude -p timed out after {timeout_s:.0f}s")
    # Parse the stream BEFORE judging the exit code: a plan usage ceiling
    # exits NONZERO while reporting itself only inside the result record, so
    # an early returncode raise hides it as a generic transport fault
    # (diagnosed 2026-07-27 when the fallback failed to fire).
    texts: list[str] = []
    result_field: Optional[str] = None
    for line in proc.stdout.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
        except json.JSONDecodeError:
            continue
        if obj.get("type") == "assistant":
            for block in (obj.get("message") or {}).get("content") or []:
                if isinstance(block, dict) and block.get("type") == "text":
                    texts.append(block["text"])
        elif obj.get("type") == "result":
            result_field = obj.get("result")
            # The CLI reports a plan usage ceiling as a SUCCESSFUL stream
            # carrying is_error plus a 429; without this it reads as an
            # empty-output transport fault (diagnosed 2026-07-27).
            if obj.get("is_error") and obj.get("api_error_status") == 429:
                raise UsageLimitError(
                    f"{model}: {str(result_field)[:160]}")
    if proc.returncode != 0:
        raise MaxPlanError(
            f"streamed claude -p exited {proc.returncode}."
            f"\nresult: {str(result_field)[:300]}"
            f"\nstderr tail:\n{(proc.stderr or '(empty)')[-400:]}")
    combined = "".join(texts)
    if combined:
        return combined
    if result_field:
        return result_field
    raise MaxPlanError("streamed claude -p produced no assistant text")


def _strip_fences(text: str) -> str:
    text = text.strip()
    fence = re.match(r"^```(?:html)?\s*(.*?)\s*```$", text, re.S)
    return fence.group(1).strip() if fence else text


_BAND_ID_RE = re.compile(r'<section class="sb-band" id="([a-z-]+)"')
# DECISION_RULES (2026-08-03): the decisions band sits between forecast and
# candidate-review; the acceptance gate tracks the validator's band grammar.
_EXPECTED_BANDS = ("forecast", "decisions", "candidate-review", "signals",
                   "accounts", "capabilities", "leadership", "method",
                   "calendar", "evidence")
# EVENTS_LANE (2026-07-27): the events band is conditional, so BOTH shapes are
# complete regions. The validator decides which one this pack required; the
# acceptance gate only judges completeness.
_EXPECTED_BANDS_WITH_EVENTS = ("forecast", "decisions", "candidate-review",
                               "signals", "accounts", "capabilities",
                               "leadership", "method", "calendar", "events",
                               "evidence")
MIN_ACCEPTABLE_CHARS = 20_000
_COMPLETENESS_REMINDER = (
    "\n\nYOUR LAST OUTPUT WAS NOT THE COMPLETE CONTENT REGION (a fragment "
    "or patch). Output the ENTIRE region: every band from "
    '<section class="sb-band" id="forecast"> through the end of '
    '<section class="sb-band" id="evidence">, with the calendar between '
    "method and evidence, ending with the sentinel. Never a diff, never "
    "an excerpt.")


def _acceptable(draft: str, prior: Optional[str] = None) -> bool:
    """A revision is accepted only when it is a COMPLETE region: the client
    hero first, all nine bands in order, and no drastic shrink vs the prior
    draft. Press 2 lesson: a one-item fix list invites a patch reply;
    judging critiques against a fragment diverges the loop."""
    if not draft.lstrip().startswith('<section class="sb-hero"'):
        return False
    if tuple(_BAND_ID_RE.findall(draft)) not in (
            _EXPECTED_BANDS, _EXPECTED_BANDS_WITH_EVENTS):
        return False
    # A draft with broken markup is NOT complete. Without this the gate
    # admitted a malformed revision (verified 2026-07-27: an unclosed
    # <strong> in the events band), later well-formed attempts were
    # rejected for a different reason, and the loop carried the broken
    # draft into validation - where a 2,400s model call died trying to
    # close a tag.
    from agents.golden_press.validate import check_well_formed
    if check_well_formed(draft):
        return False
    floor = MIN_ACCEPTABLE_CHARS
    if prior is not None:
        floor = max(floor, int(0.6 * len(prior)))
    return len(draft) >= floor


def _run_with_fallback(run, prompt, *, system, model, transcript, log):
    """One model call that survives a plan usage ceiling by stepping to the
    next frontier model. The substitution is logged and recorded; a press is
    never quietly composed by a model the operator did not expect."""
    models = [model] + [m for m in COMPOSE_FALLBACK_MODELS if m != model]
    last: Optional[Exception] = None
    for candidate in models:
        try:
            return run(prompt, system=system, model=candidate,
                       timeout_s=COMPOSE_TIMEOUT_S)
        except UsageLimitError as exc:
            last = exc
            log(f"USAGE LIMIT on {candidate}: {exc}")
            transcript.append({"step": "model-usage-limit", "model": candidate,
                               "detail": str(exc)[:200]})
            continue
    raise last if last else RuntimeError("no model available")


def _run_complete(
    prompt: str,
    system: str,
    *,
    run: Callable[..., str],
    model: str,
    prior: Optional[str],
    step: str,
    transcript: list[dict],
    log: Callable[[str], None],
    draft_sink: Optional[Callable[[str, str], None]] = None,
) -> Optional[str]:
    """One model call with the completeness acceptance gate: an output that
    is not a full nine-band region gets ONE retry with the reminder, then
    is rejected (caller keeps the prior draft). Every accepted or rejected
    draft is persisted through draft_sink for forensics."""
    for attempt in (1, 2):
        text = _strip_fences(_run_with_fallback(
            run, prompt if attempt == 1 else prompt + _COMPLETENESS_REMINDER,
            system=system, model=model, transcript=transcript, log=log))
        if draft_sink:
            draft_sink(f"{step}-attempt{attempt}", text)
        if _acceptable(text, prior):
            transcript.append({"step": step, "model": model,
                               "chars": len(text), "attempt": attempt})
            return text
        log(f"{step} attempt {attempt}: INCOMPLETE region "
            f"({len(text):,} chars) rejected by the acceptance gate")
        transcript.append({"step": f"{step}-rejected", "model": model,
                           "chars": len(text), "attempt": attempt})
    return None


def compose_content(
    pack_json: str,
    golden_content: str,
    *,
    run: Callable[..., str] = run_claude_streamed,
    model: str = COMPOSE_MODEL,
    max_loops: int = MAX_LOOPS,
    log: Callable[[str], None] = _log,
    draft_sink: Optional[Callable[[str, str], None]] = None,
) -> tuple[str, list[dict]]:
    """(content_region, transcript). Transcript records every call verbatim
    for the Checkpoint 2 critique-per-loop deliverable."""
    transcript: list[dict] = []
    prompt = _COMPOSE_INSTRUCTIONS % {"pack": pack_json, "golden": golden_content}
    log(f"compose call: model={model}, prompt={len(prompt):,} chars")
    draft = _run_complete(
        prompt, _COMPOSE_SYSTEM, run=run, model=model, prior=None,
        step="compose", transcript=transcript, log=log, draft_sink=draft_sink)
    if draft is None:
        raise RuntimeError(
            "compose produced no complete content region in two attempts")
    for loop in range(1, max_loops + 1):
        critique_prompt = (
            "EVIDENCE PACK JSON:\n%s\n\nREFERENCE CONTENT REGION:\n%s\n\n"
            "DRAFT CONTENT REGION:\n%s" % (pack_json, golden_content, draft))
        log(f"critique loop {loop} (Anthropic self-critique; OpenAI dead)")
        verdict = _run_with_fallback(
            run, critique_prompt, system=_CRITIQUE_SYSTEM, model=model,
            transcript=transcript, log=log).strip()
        transcript.append({"step": f"critique-{loop}", "model": model,
                           "verdict": verdict[:4000]})
        if verdict == "PASS":
            log(f"critique loop {loop}: PASS")
            break
        log(f"critique loop {loop}: {verdict.count(chr(10)) + 1} fix lines")
        revise_prompt = (
            "THE CRITIC'S FIX LIST:\n%s\n\nTHE EVIDENCE PACK JSON:\n%s\n\n"
            "THE REFERENCE CONTENT REGION:\n%s\n\nYOUR PRIOR DRAFT:\n%s"
            % (verdict, pack_json, golden_content, draft))
        revised = _run_complete(
            revise_prompt, _REVISE_SYSTEM, run=run, model=model, prior=draft,
            step=f"revise-{loop}", transcript=transcript, log=log,
            draft_sink=draft_sink)
        if revised is None:
            log(f"revise loop {loop}: keeping the prior complete draft "
                "(rejected revisions never replace an intact region)")
            break
        draft = revised
    if not draft.rstrip().endswith(SENTINEL):
        draft = draft.rstrip() + "\n" + SENTINEL
    return draft, transcript


def revise_for_violations(
    draft: str,
    pack_json: str,
    violations: list[dict],
    *,
    run: Callable[..., str] = run_claude_streamed,
    model: str = COMPOSE_MODEL,
    log: Callable[[str], None] = _log,
    draft_sink: Optional[Callable[[str, str], None]] = None,
) -> str:
    """The single validator-driven revision attempt (Phase 3f). A rejected
    (incomplete) revision returns the prior draft unchanged so the press
    fails loudly on REAL violations, never on a mutilated region."""
    listing = "\n".join(
        f"{i + 1}. [{v['rule']}] {v['detail']}"
        for i, v in enumerate(violations))
    log(f"validator revision: {len(violations)} violations")
    prompt = (
        "MECHANICAL VALIDATOR VIOLATIONS (fix every one, change nothing "
        "else):\n%s\n\nTHE EVIDENCE PACK JSON:\n%s\n\nYOUR PRIOR DRAFT:\n%s"
        % (listing, pack_json, draft))
    transcript: list[dict] = []
    revised = _run_complete(
        prompt, _REVISE_SYSTEM, run=run, model=model, prior=draft,
        step="validator-revise", transcript=transcript, log=log,
        draft_sink=draft_sink)
    if revised is None:
        log("validator revision rejected as incomplete; keeping prior draft")
        return draft
    if not revised.rstrip().endswith(SENTINEL):
        revised = revised.rstrip() + "\n" + SENTINEL
    return revised


# --------------------------------------------------------------------------- #
# prose-only composition (deterministic-rendering path, 2026-07-27)
# --------------------------------------------------------------------------- #
PROSE_TIMEOUT_S = 900.0

_PROSE_SYSTEM = """\
You are the senior federal capture analyst at GTM Group. You are writing ONLY
the prose of a client's Federal Opportunity Assessment. Every number, date,
dollar figure, contract number, URL and record identifier in this report has
already been rendered from the source data by code, and sits around your
prose on the page. Your job is the reading, not the arithmetic.

HARD RULES
1. CITE THE PACK'S OWN FIGURES, AND ONLY THOSE. A justification is stronger
   with the number in it: "the Bureau obligated $1.92M to RedSky on an award
   period ending 31 JUL 2026" beats "the Bureau has funded this capability".
   So DO quote a dollar amount or a date when it makes the case, copied
   EXACTLY from the pack, character for character, from the record that slot
   is about. Every figure is checked against the pack; a figure the pack does
   not carry discards your whole sentence. Never estimate, never round, never
   total or count anything yourself: no percentages, no contract or notice
   numbers, and no bare counts in digits ("7 offices"), because those are not
   given to you. When a record carries no figure worth citing, use words
   ("the largest active award", "the nearest clock").
2. Claim nothing the brief does not give you. You are describing evidence
   that is already on the page, not adding evidence.
3. No em dashes. Use a middle dot or restructure the sentence.
4. Plain declarative sentences. No marketing voice, no hedging, no
   "leveraging", no "poised to".
5. Each slot is 1 to 3 sentences. Client-facing register: a capable federal
   sales leader reading their own account.

OUTPUT: a single JSON object mapping slot key to prose string. No markdown
fences, no commentary, no keys that were not requested."""

_PROSE_INSTRUCTIONS = """\
CLIENT EVIDENCE PACK (facts already rendered on the page by code):
%(pack)s

SLOTS TO WRITE (%(count)d). Write every one:
%(slots)s

JUSTIFY EVERY OPPORTUNITY, CONCRETELY. Each sentence answers one question:
why is THIS a viable opportunity for THIS client? Name the buyer, name what
they already fund, and name the thing that makes it actionable now.

CITE THE NUMBER. Where a record carries a dollar figure or a date that makes
the case, WRITE IT: "the Bureau obligated $1.92M to RedSky on an award period
ending 31 JUL 2026" beats "the Bureau has funded this capability". Copy the
figure EXACTLY from the pack above, character for character, from THAT
record. A figure that is not in the pack is discarded along with your whole
sentence, so never estimate or round anything yourself. You MAY state how
many records the pack carries for a buyer you have just named ("across 4
cited records"), because that count is checkable against the pack; any other
bare number is discarded with the sentence. Prefer words only when the record
carries no figure worth citing.

Return the JSON object now."""

# A figure in prose is admitted only when the pack already carries it, checked
# with the validator's own dollar candidates and date keys (operator ruling
# 2026-07-30). The model still cannot introduce a number that is not evidence;
# it can now restate one that is, so a justification can be specific.
_FIGURE = re.compile(r"\d|\$|%")

_INTEGER = re.compile(r"\d{1,3}(?:,\d{3})+|\d+")
_TRIM = ".,;:()[]{}\"'·/’"

# Fields whose text the pack itself published. A token appearing verbatim in
# one of them is the model QUOTING evidence, not inventing a figure.
_RECORD_TEXT_FIELDS = ("title", "description", "set_aside", "office",
                       "agency", "sub_agency", "recipient", "vehicle",
                       "psc", "naics", "record_id", "notice_type",
                       "anticipated_solicitation", "parent_award_id",
                       "competition")


def _bare_integers(residue: str) -> tuple[list[str], list[str]]:
    """(standalone integers, other digit-bearing tokens) in this residue.

    Word-split rather than one clever regex, because the clever regex is what
    turned "runs to 2029, and" into the count 2029 with a comma glued on.
    """
    integers, others = [], []
    for word in residue.split():
        token = word.strip(_TRIM)
        if _INTEGER.fullmatch(token):
            integers.append(token)
        elif any(ch.isdigit() for ch in word):
            others.append(word.strip(_TRIM))
    return integers, others


def _name_variants(name: str) -> set[str]:
    """How a writer actually refers to this buyer.

    The pack says "Internal Revenue Service"; a sentence says "the IRS". The
    pack says "Department of Veterans Affairs"; a sentence says "Veterans
    Affairs". Requiring the pack's exact string is why the count check found
    nothing to vouch for and every corridor justification still died.
    """
    flat = " ".join(str(name or "").split())
    if not flat:
        return set()
    out = {flat}
    for prefix in ("Department of the ", "Department of ", "U.S. ", "US ",
                   "United States "):
        if flat.startswith(prefix):
            out.add(flat[len(prefix):])
    words = [w for w in re.split(r"[^A-Za-z]+", flat) if w]
    significant = [w for w in words if w.lower() not in
                   {"of", "the", "and", "for", "department", "u", "s"}]
    if len(significant) >= 2:
        out.add("".join(w[0] for w in significant).upper())
        # The shortened form a writer reaches for: "Social Security" for the
        # Social Security Administration, "Internal Revenue" for the IRS.
        out.add(" ".join(significant[:2]))
    return {v for v in out if len(v) >= 3}


def _pack_count_candidates(text: str, pack: Any) -> set[int]:
    """The counts this sentence is entitled to state.

    A bare count is admitted only when it counts something the sentence
    NAMES: the records the pack carries for an agency, sub-agency, or
    recipient mentioned in the text, or the pack's own total. The model still
    cannot state a count the evidence contradicts, which is the guarantee
    that matters; it can now say "the IRS carries 4 cited records" in the
    words a reader would actually use.
    """
    records = list(getattr(pack, "records", None) or [])
    hay = " ".join(str(text or "").split())
    fold = hay.casefold()
    counts = {len(records)}
    per: dict[str, int] = {}
    for record in records:
        # A record whose agency and sub-agency read the same counts ONCE for
        # that name; counting it twice would reject the honest number.
        for name in {" ".join(str(value).split())
                     for value in (record.agency, record.sub_agency,
                                   record.recipient) if value}:
            per[name] = per.get(name, 0) + 1
    for name, total in per.items():
        for variant in _name_variants(name):
            # An acronym must match as a whole word ("IRS" is not "IRSA");
            # a full name is distinctive enough to match as a substring.
            if variant.isupper() and len(variant) <= 6:
                found = re.search(rf"\b{re.escape(variant)}\b", hay)
            else:
                found = len(variant) >= 4 and variant.casefold() in fold
            if found:
                counts.add(total)
                break
    return counts


def _verbatim_in(token: str, haystack: str) -> bool:
    """The token appears in pack text as its own token, not inside a longer
    number. Substring matching would let "600" ride in on "$1,600,000"."""
    if not token:
        return False
    # The leading guard also refuses a comma or period, because those are what
    # join a longer number: without it "600" matches inside "$1,600,000". The
    # trailing guard refuses only a separator that CONTINUES a number, so a
    # figure ending a clause ("600, and then") still reads as itself.
    return re.search(r"(?<![A-Za-z0-9,.])" + re.escape(token.casefold())
                     + r"(?![A-Za-z0-9]|[,.]\d)", haystack) is not None


def _pack_verbatim_text(pack: Any) -> str:
    """Everything the pack's own records say, folded for containment tests."""
    parts = []
    for record in (getattr(pack, "records", None) or []):
        for field in _RECORD_TEXT_FIELDS:
            value = getattr(record, field, None)
            if value:
                parts.append(" ".join(str(value).split()))
    return " ".join(parts).casefold()


def _figures_are_pack_backed(text: str, pack: Any) -> bool:
    """Every figure in this sentence is a figure the pack already carries.

    Reuses the VALIDATOR's own helpers rather than a second opinion, so a
    sentence accepted here cannot fail check_dollars/check_dates later: the
    same candidate set, tolerance, and date keys decide both. Anything the
    validator would not recognise as a figure (a bare count, a percentage)
    still disqualifies, because code renders those and prose must not restate
    a number it was not given.
    """

    from agents.golden_press.validate import (
        _DOLLAR_RE, _pack_dollar_candidates, _pack_date_keys,
        _stated_date_keys, _stated_dollar,
    )

    candidates = _pack_dollar_candidates(pack)
    for match in _DOLLAR_RE.finditer(text):
        value, tolerance = _stated_dollar(match.group(1), match.group(2))
        if value <= 0:
            continue
        if not any(abs(value - c) <= max(tolerance, 0.5) for c in candidates):
            return False
    date_keys = _pack_date_keys(pack)
    stated = _stated_date_keys(text)
    for _display, key in stated:
        if key not in date_keys:
            return False
    # Whatever is left after removing the figures the pack vouches for must
    # contain no digits, EXCEPT a count the pack can prove (operator ruling
    # 2026-07-30, second pass). Measured on the Riverbed press that day: 17 of
    # 18 opportunity justifications were discarded with every dollar and every
    # date in them verified, because "carries $62,848,980 obligated across 4
    # cited records" left behind the 4. The pack holds exactly 4 Internal
    # Revenue Service records, so that count was evidence, not invention.
    residue = _DOLLAR_RE.sub(" ", text)
    for display, _key in stated:
        residue = residue.replace(display, " ")
    if "%" in residue:
        return False                      # a percentage is never a count
    integers, others = _bare_integers(residue)
    allowed = _pack_count_candidates(text, pack)
    verbatim = _pack_verbatim_text(pack)
    date_years = {key[2:] for key in date_keys if key.startswith("y:")}
    for token in integers:
        if int(token.replace(",", "")) in allowed:
            continue                      # a count the pack can prove
        if token in date_years:
            continue                      # a year the pack already carries
        # Quoted from a record's own text ("over 5,000 Cisco devices"). Only
        # for figures of three characters or more: a bare "4" occurs
        # somewhere in 46 records' text by accident, and letting that license
        # "across 4 cited records" would hand back the guarantee the count
        # check exists to keep.
        if len(token) >= 3 and _verbatim_in(token, verbatim):
            continue
        return False
    # A digit riding inside a token ("8(a)", "C5I", "Services 2.0") is
    # admitted only when that exact token is text the pack itself published.
    # Anything else is a figure the model brought with it.
    return all(_verbatim_in(token, verbatim) for token in others)


def _prose_usable(text: object, pack: Any = None) -> Optional[str]:
    """A usable sentence: long enough, and figure-free OR pack-backed.

    OPERATOR RULING 2026-07-30: prose may now be concrete. The blanket ban on
    digits kept the writer from ever saying "the award period ends September
    30, 2027" in its own voice. A figure is admitted only when it exactly
    matches a figure the pack carries, so the anti-hallucination guarantee is
    unchanged: the model still cannot introduce a number that is not evidence.
    Without a pack to check against, the old strict rule stands.
    """

    if not isinstance(text, str):
        return None
    cleaned = " ".join(text.split()).replace("—", "·").replace("–", "·")
    if len(cleaned) < 25:
        return None
    # CONTRACT L7 (2026-08-03): prose carrying banned vocabulary is unusable
    # and falls back to deterministic copy; the composer cannot re-introduce
    # a term the contract retired.
    try:
        from agents.golden_press.contract import banned_vocabulary
        folded = cleaned.casefold()
        for term in banned_vocabulary():
            if re.search(r"(?<![a-z0-9])" + re.escape(term.casefold())
                          + r"(?![a-z0-9])", folded):
                return None
    except Exception:  # noqa: BLE001 - a missing contract never blocks prose
        pass
    if not _FIGURE.search(cleaned):
        return cleaned
    if pack is None:
        return None
    return cleaned if _figures_are_pack_backed(cleaned, pack) else None


def _parse_prose(raw: str) -> dict:
    """Lenient JSON extraction. A malformed object yields whatever slots do
    parse; it never raises, because prose is optional by design."""
    text = _strip_fences(raw).strip()
    start, end = text.find("{"), text.rfind("}")
    if start < 0:
        return {}
    # A truncated object has no closing brace. That is precisely the case
    # salvage exists for, so take the tail and let the pair scanner run.
    blob = text[start:end + 1] if end > start else text[start:]
    try:
        data = json.loads(blob)
    except ValueError:
        # Salvage the well-formed pairs from a truncated or trailing-comma
        # object rather than losing every slot to one bad character.
        data = {}
        for m in re.finditer(r'"([A-Za-z0-9_\-]+)"\s*:\s*"((?:[^"\\]|\\.)*)"',
                             blob):
            try:
                data[m.group(1)] = json.loads('"%s"' % m.group(2))
            except ValueError:
                continue
    return data if isinstance(data, dict) else {}


def compose_prose(
    pack_json: str,
    slots: list[dict],
    *,
    pack: Any = None,
    run: Callable[..., str] = run_claude_streamed,
    model: str = COMPOSE_MODEL,
    log: Callable[[str], None] = _log,
    draft_sink: Optional[Callable[[str, str], None]] = None,
) -> tuple[dict, list[dict]]:
    """Prose for the deterministic renderer. NEVER RAISES.

    Prose is decoration over rendered evidence: a failed call, a truncated
    object, or a slot that smuggles in a figure costs that slot its writing
    and nothing else. The renderer has a factual fallback for every slot, so
    the worst case is a report that reads flatter, not a report that does
    not exist.
    """
    transcript: list[dict] = []
    spec = "\n".join(f"- {s['key']}: {s['brief']}" for s in slots)
    prompt = _PROSE_INSTRUCTIONS % {"pack": pack_json, "count": len(slots),
                                    "slots": spec}
    log(f"prose call: model={model}, {len(slots)} slots, "
        f"prompt={len(prompt):,} chars")
    try:
        raw = _run_with_fallback(run, prompt, system=_PROSE_SYSTEM, model=model,
                                 transcript=transcript, log=log)
    except Exception as exc:  # noqa: BLE001 - prose never blocks a press
        log(f"prose call failed ({type(exc).__name__}: {exc}); "
            "rendering with deterministic fallback text for every slot")
        transcript.append({"step": "prose-failed", "model": model,
                           "detail": f"{type(exc).__name__}: {exc}"[:300]})
        return {}, transcript
    if draft_sink:
        draft_sink("prose", raw)
    parsed = _parse_prose(raw)
    wanted = {s["key"] for s in slots}
    kept, rejected = {}, []
    # ONE SENTENCE PER SLOT. The fallback model wrote the identical line into
    # six record slots on the apexanalytix press, which the validator caught
    # as repeated_prose. Prose is optional by design, so a duplicate loses its
    # slot to the deterministic fallback rather than shipping six times.
    seen_text: set = set()
    for key, value in parsed.items():
        if key not in wanted:
            continue
        usable = _prose_usable(value, pack)
        if not usable:
            rejected.append(key)
            continue
        fingerprint = " ".join(usable.lower().split())
        if fingerprint in seen_text:
            rejected.append(key)
            continue
        seen_text.add(fingerprint)
        kept[key] = usable
    log(f"prose: {len(kept)}/{len(slots)} slots written, "
        f"{len(rejected)} rejected for an unbacked figure or running short, "
        f"{len(wanted) - len(kept) - len(rejected)} not returned")
    transcript.append({"step": "prose", "model": model, "chars": len(raw),
                       "slots_requested": len(slots), "slots_kept": len(kept),
                       "slots_rejected": rejected[:40]})
    return kept, transcript
