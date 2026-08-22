"""Per-motion title inference, blind challenge, and deterministic diff.

WHAT THIS IS FOR. A motion states a commercial intent over cited records.
This layer answers "who cares about this", producing the SEARCH VOCABULARY
the supply lane will use. It is the only model work in the targeting lane.

TITLES ARE VOCABULARY, NOT CLAIMS. Nothing this module produces renders as
a claim about a person, a record, or a figure. It is the same category as
capability keywords and NAICS codes, which are already model-generated in
this repo and gated by an operator workshop on the revise()/amend_terms()
contract. The band stays deterministic; only the search input is inferred.

BLIND GENERATION, NOT CRITIQUE (reviewer ruling R2). The generator and the
challenger both read the SAME payload and neither sees the other's output.
An adversary shown a list is anchored by it: the screener stress pass found
six false rejections precisely because it was asked to REFUTE rather than to
review. The challenger returns three named lists (recommended, likely
omissions, tempting-but-wrong) and a deterministic diff produces agreement,
additions and disputes. A targeted critique pass runs only when the
disagreement is material.

THE OPERATOR WINS (R3). A disputed title stays OUT of supply. It does not
block the motion; the motion blocks only when no approved title remains for
a function the motion actually needs, and that is a receipted supply gap
rather than an error.

NO PERSONA FLOOR (R4). The legacy min_length=4 is gone. What is required is
evidence-supported FUNCTIONAL COVERAGE: each function the motion needs
either has a supported title or a stated gap. A floor manufactures padding,
and padding is what the static ladder already did.

THE FINGERPRINT COVERS THE PAYLOAD, NOT THE RECORD IDS (R7). Hashing ids
alone would let a rewritten prompt, a re-resolved organisation, or an edited
capability vocabulary silently reuse an approved title set. The hash is over
everything the inference actually saw, plus the rule, schema and prompt
versions, so any of them drifting stales the strategy.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any, Callable, Optional

TITLE_STRATEGY_VERSION = "title_strategy.v1.2026-08-06"
PROMPT_VERSION = "title_prompt.v1.2026-08-06"

# The buying functions a motion can need. Coverage is measured against these
# rather than against a persona count (R4).
MISSION_PROGRAM = "mission/program"
SECURITY_OPS = "security operations"
PROCUREMENT = "procurement/contracting"
CHANNEL_PRIME = "channel/prime"
FUNCTIONS = (MISSION_PROGRAM, SECURITY_OPS, PROCUREMENT, CHANNEL_PRIME)

# Which functions a motion genuinely needs, by org kind. A buyer-side motion
# without a procurement route cannot be put on contract; a paper-holder
# motion has no government programme office to reach.
REQUIRED_FUNCTIONS = {
    "buying_component": (MISSION_PROGRAM, PROCUREMENT),
    "paper_holder": (CHANNEL_PRIME,),
}

# Title provenance, mirroring the Keyword workshop's own vocabulary so the
# three lanes read identically to the operator.
FROM_MODEL = "model"
FROM_CHALLENGER = "challenger"
FROM_OPERATOR = "operator"
FROM_EDITED = "edited"
FROM_LADDER = "ladder_import"


class TitleStrategyError(RuntimeError):
    """Inference could not run or returned an unusable shape."""


def _clean(value: Any) -> str:
    return " ".join(str(value or "").split())


def _title_key(title: Any) -> str:
    """Comparison key for a title. Case and punctuation insensitive so
    'Sr. Contracting Officer' and 'Senior Contracting Officer' do not read as
    two different proposals when the diff runs."""
    text = _clean(title).casefold()
    text = text.replace("sr.", "senior").replace("jr.", "junior")
    text = text.replace("&", "and")
    return " ".join(ch for ch in text.replace(",", " ").replace(".", " ").split())


# --------------------------------------------------------------------------- #
# The canonical payload and its fingerprint (R7)
# --------------------------------------------------------------------------- #
def inference_payload(motion: dict, *, capability: Any = None,
                      records: Any = None,
                      rules_version: str = "",
                      schema_version: str = TITLE_STRATEGY_VERSION,
                      prompt_version: str = PROMPT_VERSION) -> dict:
    """Exactly what the inference sees, in a stable order.

    This IS the hashed object. Everything the model reads must appear here
    or the fingerprint would certify a set produced from inputs it never
    covered, which is the drift class R7 exists to close.
    """
    rows = []
    for row in (records or []):
        if not isinstance(row, dict):
            continue
        rows.append({k: row.get(k) for k in (
            "record_id", "agency", "sub_agency", "recipient", "title",
            "period_end", "obligated_dollars", "vehicle_class", "url")})
    rows.sort(key=lambda r: str(r.get("record_id") or ""))
    return {
        "schema_version": schema_version,
        "prompt_version": prompt_version,
        "rules_version": rules_version,
        "motion": {
            "motion_id": motion.get("motion_id"),
            "motion": motion.get("motion"),
            "objective": motion.get("objective"),
            "row_class": motion.get("row_class"),
            "org_kind": motion.get("org_kind"),
            "strategy_key": list(motion.get("strategy_key") or []),
            "mission_applicability": motion.get("mission_applicability"),
            "holder": motion.get("holder"),
            "route_role": motion.get("route_role"),
            "organisations": sorted(motion.get("organisations") or []),
            "agencies": sorted(motion.get("agencies") or []),
            "spec_ids": sorted(str(s) for s in (motion.get("spec_ids") or [])),
            "join_record_ids": sorted(motion.get("join_record_ids") or []),
        },
        "capability": capability or {},
        "records": rows,
    }


def fingerprint(payload: dict) -> str:
    """SHA-256 over the canonical payload. Stable across runs."""
    blob = json.dumps(payload, sort_keys=True, separators=(",", ":"),
                      default=str)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


# --------------------------------------------------------------------------- #
# Generation and challenge, both blind
# --------------------------------------------------------------------------- #
_GENERATE_TASK = """\
You are planning federal outreach for a technology vendor.

Given the commercial motion and the cited federal records below, name the job
titles worth pursuing. Reason about who DECIDES, who CHAMPIONS, who EVALUATES,
and who can put the requirement ON CONTRACT.

Rules:
- Titles must be exact and searchable, as they appear in a directory.
- Every title states which function it serves and why it matters for THIS
  motion, citing the evidence you were given.
- Do not pad. Name only titles the evidence supports.
- Do not name a person. Titles only.
"""

_CHALLENGE_TASK = """\
You are auditing a federal outreach plan for RECALL.

Given the same commercial motion and cited federal records, independently
determine which job titles matter. You are NOT reviewing anyone else's list;
produce your own judgement.

Return three separate lists:
1. recommended: titles you would pursue.
2. likely_omissions: titles a competent planner would probably MISS here, and
   why they are easy to miss.
3. tempting_but_wrong: titles that read as obviously right for this motion
   but would waste the outreach, and the reason they are tempting.

Do not name a person. Titles only.
"""


def _default_caller(task: str, payload: dict, *, label: str) -> dict:
    """The Max-plan CLI seam. Injectable so no test ever calls a model."""
    from agents.decisions.maxplan_cli import run_claude

    prompt = (task + "\n\nMOTION AND EVIDENCE (JSON):\n"
              + json.dumps(payload, indent=1, sort_keys=True, default=str)
              + "\n\nReturn JSON only.")
    raw = run_claude(prompt)
    try:
        return json.loads(raw) if isinstance(raw, str) else dict(raw or {})
    except ValueError as exc:
        raise TitleStrategyError(
            f"{label} returned unparseable JSON: {str(raw)[:200]}") from exc


def _personas_from(node: Any) -> list:
    """Normalise a model's persona list. A malformed entry is DROPPED, never
    repaired into something confident: an invented function or seniority
    would be a claim about how the buying committee works."""
    out = []
    for entry in (node or []):
        if not isinstance(entry, dict):
            continue
        title = _clean(entry.get("title"))
        if not title:
            continue
        out.append({
            "title": title,
            "function": _clean(entry.get("function")) or None,
            "seniority": _clean(entry.get("seniority")) or None,
            "why": _clean(entry.get("why")) or None,
            "org_types": [_clean(o) for o in (entry.get("org_types") or [])
                          if _clean(o)],
        })
    return out


def generate_titles(payload: dict, *, caller: Optional[Callable] = None
                    ) -> dict:
    """The generator's own view. Blind to the challenger."""
    caller = caller or _default_caller
    result = caller(_GENERATE_TASK, payload, label="generator")
    personas = _personas_from(result.get("personas"))
    if not personas:
        raise TitleStrategyError("generator proposed no usable title")
    return {
        "personas": personas,
        "buying_committee_note": _clean(result.get("buying_committee_note")),
        "citations": [_clean(c) for c in (result.get("citations") or [])
                      if _clean(c)],
    }


def challenge_titles(payload: dict, *, caller: Optional[Callable] = None
                     ) -> dict:
    """The challenger's own view. Blind to the generator (R2).

    A malformed or empty challenge is USABLE, not fatal: it yields no
    additions and no disputes, and the receipt says the challenge returned
    nothing. Losing the recall pass must never lose the motion.
    """
    caller = caller or _default_caller
    try:
        result = caller(_CHALLENGE_TASK, payload, label="challenger")
    except Exception as exc:                              # noqa: BLE001
        return {"recommended": [], "likely_omissions": [],
                "tempting_but_wrong": [], "unavailable": str(exc)[:200]}
    if not isinstance(result, dict):
        return {"recommended": [], "likely_omissions": [],
                "tempting_but_wrong": [],
                "unavailable": "challenger returned a non-object"}
    return {
        "recommended": _personas_from(result.get("recommended")),
        "likely_omissions": _personas_from(result.get("likely_omissions")),
        "tempting_but_wrong": _personas_from(result.get("tempting_but_wrong")),
    }


# --------------------------------------------------------------------------- #
# The deterministic diff
# --------------------------------------------------------------------------- #
def diff_strategies(generated: dict, challenge: dict, *,
                    org_kind: str = "buying_component") -> dict:
    """Agreement, additions, disputes, and functional coverage. Pure.

    AGREEMENT   both proposed it
    ADDITIONS   only the challenger proposed it (recommended or omission)
    DISPUTES    the generator proposed it and the challenger called it
                tempting-but-wrong. Withheld from supply per R3; the
                operator decides.
    """
    gen = {_title_key(p["title"]): p for p in (generated.get("personas") or [])}
    rec = {_title_key(p["title"]): p
           for p in (challenge.get("recommended") or [])}
    omit = {_title_key(p["title"]): p
            for p in (challenge.get("likely_omissions") or [])}
    wrong = {_title_key(p["title"]): p
             for p in (challenge.get("tempting_but_wrong") or [])}

    challenger_side = dict(rec)
    challenger_side.update(omit)

    agreement, additions, disputes = [], [], []
    for key, persona in gen.items():
        if key in wrong:
            disputes.append({**persona, "provenance": FROM_MODEL,
                             "disputed_by": FROM_CHALLENGER,
                             "dispute_reason": wrong[key].get("why")})
        elif key in challenger_side:
            agreement.append({**persona, "provenance": FROM_MODEL,
                              "agreed_by": FROM_CHALLENGER})
        else:
            agreement.append({**persona, "provenance": FROM_MODEL})
    for key, persona in challenger_side.items():
        if key in gen:
            continue
        additions.append({**persona, "provenance": FROM_CHALLENGER,
                          "was_omission": key in omit})

    approved_now = agreement + additions
    covered = {p.get("function") for p in approved_now if p.get("function")}
    required = REQUIRED_FUNCTIONS.get(org_kind, ())
    missing = [f for f in required
               if not any(f and f in (c or "") for c in covered)]
    return {
        "agreement": agreement,
        "additions": additions,
        "disputes": disputes,
        "functions_covered": sorted(c for c in covered if c),
        "functions_required": list(required),
        "functions_missing": missing,
        # R4: coverage, never a persona count
        "coverage_complete": not missing,
        "challenge_unavailable": challenge.get("unavailable"),
        "material_disagreement": bool(disputes) or bool(
            [a for a in additions if a.get("was_omission")]),
    }


# --------------------------------------------------------------------------- #
# The workshop (reviewer R3, mirroring the Keyword and NAICS workshops)
# --------------------------------------------------------------------------- #
IN_SEARCH = "in_search"
NEEDS_JUDGMENT = "needs_judgment"
KEPT_OUT = "kept_out"
LANES = (IN_SEARCH, NEEDS_JUDGMENT, KEPT_OUT)


def build_workshop(diff: dict, *, motion_id: str = "") -> dict:
    """Three lanes over a diff, in the shape the operator already knows.

    THE OPERATOR WINS (R3). Only the IN_SEARCH lane reaches supply. A
    disputed title lands in NEEDS_JUDGMENT and is withheld until the
    operator rules; it never silently searches and never silently vanishes.

    Removal is NON-DESTRUCTIVE, exactly as the Keyword workshop's is: a
    title moved out keeps its provenance and its reason so it can be
    restored, because a term that was rejected once is evidence about the
    engagement and deleting it loses that.
    """
    def _row(persona: dict, lane: str, reason: str = "") -> dict:
        return {
            "title": persona.get("title"),
            "function": persona.get("function"),
            "seniority": persona.get("seniority"),
            # R5: `why` is INTERNAL. It rides the workshop and show-the-work
            # and never reaches client copy, which uses deterministic
            # templates over the approved title instead.
            "why": persona.get("why"),
            "org_types": persona.get("org_types") or [],
            "provenance": persona.get("provenance") or FROM_MODEL,
            "lane": lane,
            "lane_reason": reason,
            "motion_id": motion_id,
            "disputed_by": persona.get("disputed_by"),
            "dispute_reason": persona.get("dispute_reason"),
        }

    in_search = [_row(p, IN_SEARCH,
                      "agreed by both passes" if p.get("agreed_by")
                      else "proposed by the generator, unchallenged")
                 for p in (diff.get("agreement") or [])]
    needs = [_row(p, NEEDS_JUDGMENT,
                  "the challenger named this a likely omission"
                  if p.get("was_omission")
                  else "proposed by the challenger only")
             for p in (diff.get("additions") or [])]
    needs += [_row(p, NEEDS_JUDGMENT,
                   "the challenger called this tempting but wrong")
              for p in (diff.get("disputes") or [])]
    return {
        "motion_id": motion_id,
        "version": TITLE_STRATEGY_VERSION,
        IN_SEARCH: in_search,
        NEEDS_JUDGMENT: needs,
        KEPT_OUT: [],
        "functions_missing": list(diff.get("functions_missing") or []),
        "coverage_complete": bool(diff.get("coverage_complete")),
    }


def approved_titles(workshop: dict) -> list:
    """The only titles supply may search with. IN_SEARCH lane, nothing else."""
    return [_clean(row.get("title"))
            for row in (workshop.get(IN_SEARCH) or [])
            if _clean(row.get("title"))]


def supply_blocked(workshop: dict, *, org_kind: str = "buying_component"
                   ) -> tuple:
    """(blocked, reasons). R3: a dispute withholds a TITLE, never the motion.

    The motion blocks only when a REQUIRED function has no approved title,
    and that is stated as a receipted supply gap rather than raised as an
    error, because a motion with no procurement route is a real finding
    about the engagement and not a software fault.
    """
    approved = [row for row in (workshop.get(IN_SEARCH) or [])
                if _clean(row.get("title"))]
    covered = {row.get("function") for row in approved if row.get("function")}
    required = REQUIRED_FUNCTIONS.get(org_kind, ())
    missing = [f for f in required
               if not any(f and f in (c or "") for c in covered)]
    reasons = []
    if not approved:
        reasons.append("no approved title remains in the search lane")
    for function in missing:
        reasons.append(f"no approved title covers the {function} function")
    return (bool(reasons), reasons)


def import_ladder(spec: dict, *, motion_id: str = "") -> list:
    """The static persona ladder, imported EXPLICITLY as a recovery set (R8).

    NEVER an automatic fallback. Inference being unavailable yields a stated
    supply gap; the operator may then call this deliberately, and every row
    it returns carries operator provenance and says it came from the ladder.
    A fallback that fires silently becomes the default, which is exactly how
    a 25-credit cap became an invisible coverage limit in this same lane.
    """
    from agents.golden_press.targeting_rules import tier

    out = []
    for number in (spec.get("seeks_tiers") or []):
        rung = tier(number)
        for title in rung.get("titles") or []:
            out.append({
                "title": _clean(title),
                "function": rung.get("persona"),
                "seniority": None,
                "why": (f"imported from persona ladder tier {number} "
                        f"({rung.get('persona')}) by explicit operator action"),
                "org_types": [rung.get("target")],
                "provenance": FROM_LADDER,
                "lane": IN_SEARCH,
                "lane_reason": "explicit operator import, not a fallback",
                "motion_id": motion_id,
            })
    return out
