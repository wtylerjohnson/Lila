"""Client lifecycle stages: derived from artifacts on disk, never hand-set.

The single owner of the stage mapping. The rules, verbatim from the
operator brief (2026-07-17):

  Onboarding  · profile invalid or missing per require_profile (a client
                with a VALID profile but no sweep also renders here, with
                the press-sweep affordance: sweep_ready=True)
  Swept       · a valid sweep artifact exists for the gate's designator
  Shortlisted · relevance output exists (calibration rows for the client,
                or an adjudication log)
  Composed    · board content exists (clients/<slug>/signal_board_content.json)
  Gated       · the last press produced DRAFT/DO-NOT-SEND
  Shipped     · the last press produced a release-eligible artifact
  Refresh Due · shipped, but the oldest figure retrieved_at falls outside
                the FRESHNESS_MAX_AGE_DAYS window, or cannot be proven
                inside it (unknown retrieval times schedule a refresh too)

Precedence: the highest satisfied stage in pipeline order wins;
Refresh Due applies only from Shipped. Everything here is read-only
derivation over existing artifacts; nothing is pulled, pressed, or
mutated.

Freshness traffic light: green inside the window, amber within
AMBER_WARNING_DAYS of expiry, red stale or unknown.
"""
from __future__ import annotations

import csv
import json
import os
from datetime import date, datetime, timezone
from typing import Any, Optional

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

STAGES = ("onboarding", "swept", "shortlisted", "composed",
          "gated", "shipped", "refresh_due")

STAGE_LABELS = {
    "onboarding": "Onboarding", "swept": "Swept",
    "shortlisted": "Shortlisted", "composed": "Composed",
    "gated": "Gated", "shipped": "Shipped", "refresh_due": "Refresh Due",
}

#: amber when this close (in days) to the freshness window's edge
AMBER_WARNING_DAYS = 3


def _slug(name: str) -> str:
    from tools.capability import _slug as slug
    return slug(name)


def list_clients() -> list[str]:
    """Every client under clients/: the radar's population is the book of
    business on disk, not the review-packet roster."""
    base = os.path.join(_ROOT, "clients")
    if not os.path.isdir(base):
        return []
    out = []
    for entry in sorted(os.listdir(base)):
        if os.path.isdir(os.path.join(base, entry)) \
                and not entry.startswith("."):
            out.append(entry)
    return out


def _client_display_name(slug: str) -> str:
    try:
        from tools.capability import load_profile
        profile = load_profile(slug)
        if profile is not None and profile.client_name:
            return profile.client_name
    except Exception:  # noqa: BLE001 - a corrupt profile still gets a card
        pass
    return slug


def _profile_gate(slug: str) -> tuple[bool, Optional[str]]:
    try:
        from tools.capability import require_profile
        require_profile(slug)
        return True, None
    except Exception as e:  # noqa: BLE001 - the gate's text is the finding
        return False, str(e)


def _sweep_exists(slug: str) -> bool:
    try:
        from agents.review import sweep_artifact_path
        path = sweep_artifact_path(_client_display_name(slug))
    except Exception:  # noqa: BLE001 - unresolvable gate means no sweep
        return False
    if not path or not os.path.exists(path):
        return False
    try:
        with open(path, encoding="utf-8") as f:
            sweep = json.load(f)
        return isinstance(sweep, dict) and isinstance(sweep.get("results"), dict)
    except (OSError, ValueError):
        return False


def _relevance_output_exists(slug: str) -> bool:
    # The assessment chain writes a per-client run receipt even when the
    # relevance pass finds zero disagreements.  The legacy global CSV cannot
    # represent that clean result because it has no client row in that case.
    from agents.assessment_chain import relevance_receipt_valid

    receipt = os.path.join(_ROOT, "data", "state", "relevance",
                           f"{slug}.run.json")
    if os.path.exists(receipt):
        # Once the chain has written its authoritative client receipt, never
        # let an older global row or adjudication mask a stale/corrupt run.
        return relevance_receipt_valid(slug, root=_ROOT)
    adjudications = os.path.join(_ROOT, "data", "state", "relevance",
                                 f"{slug}.adjudications.jsonl")
    if os.path.exists(adjudications):
        return True
    calibration = os.path.join(_ROOT, "data", "state", "relevance",
                               "calibration.csv")
    if not os.path.exists(calibration):
        return False
    try:
        name = _client_display_name(slug)
        with open(calibration, encoding="utf-8", newline="") as f:
            for row in csv.DictReader(f):
                if row.get("client") in (name, slug):
                    return True
    except (OSError, ValueError):
        return False
    return False


def _board_content_path(slug: str) -> str:
    return os.path.join(_ROOT, "clients", slug, "signal_board_content.json")


def _press_artifacts(slug: str) -> tuple[Optional[str], Optional[str]]:
    """(clean_path, gated_path) for the last board press, existing only."""
    reports = os.path.join(_ROOT, "data", "reports")
    clean = os.path.join(reports, f"{slug}.federal_opportunity_signals.html")
    gated = os.path.join(
        reports, f"{slug}.federal_opportunity_signals.DO-NOT-SEND.html")
    return (clean if os.path.exists(clean) else None,
            gated if os.path.exists(gated) else None)


def _mtime(path: Optional[str]) -> Optional[float]:
    if not path:
        return None
    try:
        return os.stat(path).st_mtime
    except OSError:
        return None


def _oldest_figure_retrieved(slug: str) -> Optional[datetime]:
    path = _board_content_path(slug)
    if not os.path.exists(path):
        return None
    try:
        from agents.reports.facts import _parse_retrieved
        with open(path, encoding="utf-8") as f:
            content = json.load(f)
        times = []
        for fig in content.get("figures") or []:
            parsed = _parse_retrieved((fig or {}).get("retrieved_at"))
            if parsed is None:
                return None          # any unknown means no honest minimum
            times.append(parsed)
        return min(times) if times else None
    except (OSError, ValueError):
        return None


def _freshness(slug: str, today: date) -> dict:
    """Traffic light from the oldest figure's age against the freshness
    window: green inside, amber near the edge, red stale or unknown."""
    from agents.reports.verification import FRESHNESS_MAX_AGE_DAYS
    oldest = _oldest_figure_retrieved(slug)
    if oldest is None:
        return {"light": "red", "basis": "unknown", "days_left": None}
    age_days = (datetime(today.year, today.month, today.day,
                         tzinfo=timezone.utc) - oldest).days
    days_left = FRESHNESS_MAX_AGE_DAYS - age_days
    if days_left < 0:
        return {"light": "red", "basis": "stale", "days_left": days_left}
    if days_left <= AMBER_WARNING_DAYS:
        return {"light": "amber", "basis": "expiring", "days_left": days_left}
    return {"light": "green", "basis": "current", "days_left": days_left}


def _headline_stat(slug: str) -> Optional[str]:
    path = _board_content_path(slug)
    if not os.path.exists(path):
        return None
    try:
        with open(path, encoding="utf-8") as f:
            content = json.load(f)
        return content.get("scale_total") or None
    except (OSError, ValueError):
        return None


def _assessment_status(slug: str) -> dict:
    """Read the chain owner's status without turning a bad file into state."""
    from agents.assessment_chain import assessment_status

    state_dir = os.path.join(_ROOT, "data", "state", "assessment")
    try:
        return assessment_status(slug, state_dir=state_dir)
    except Exception as exc:  # noqa: BLE001 - one bad client cannot sink radar
        return {
            "slug": slug,
            "state": None,
            "since": None,
            "alert_line": f"{slug} assessment status unavailable: {exc}"[:300],
        }


def derive_stage(slug: str, *, today: Optional[date] = None) -> dict:
    """The lifecycle card for one client. Read-only; every field derived."""
    today = today or date.today()
    name = _client_display_name(slug)
    populated, gate_error = _profile_gate(slug)
    clean, gated = _press_artifacts(slug)
    clean_m, gated_m = _mtime(clean), _mtime(gated)

    stage = "onboarding"
    if populated:
        if _sweep_exists(slug):
            stage = "swept"
            if _relevance_output_exists(slug):
                stage = "shortlisted"
            if os.path.exists(_board_content_path(slug)):
                stage = "composed"
        if clean_m or gated_m:
            if (gated_m or 0) > (clean_m or 0):
                stage = "gated"
            else:
                stage = "shipped"

    freshness = _freshness(slug, today)
    if stage == "shipped" and freshness["basis"] in ("stale", "unknown"):
        stage = "refresh_due"

    last_press = max(clean_m or 0, gated_m or 0) or None
    packet = os.path.join(_ROOT, "data", "review", f"{slug}.review.json")
    return {
        "slug": slug,
        "has_review_packet": os.path.exists(packet),
        "client_name": name,
        "stage": stage,
        "stage_label": STAGE_LABELS[stage],
        "sweep_ready": populated,
        "gate_error": None if populated else gate_error,
        "freshness": freshness,
        "last_press": (datetime.fromtimestamp(last_press, tz=timezone.utc)
                       .date().isoformat() if last_press else None),
        "headline_stat": (_headline_stat(slug)
                          if stage in ("shipped", "refresh_due") else None),
        "assessment": _assessment_status(slug),
    }


def radar(today: Optional[date] = None) -> dict[str, Any]:
    """The whole book: cards plus the cross-client scoreboard aggregates."""
    cards = [derive_stage(slug, today=today) for slug in list_clients()]
    by_stage: dict[str, int] = {s: 0 for s in STAGES}
    lights = {"green": 0, "amber": 0, "red": 0}
    figures_verified = 0
    for card in cards:
        by_stage[card["stage"]] += 1
        lights[card["freshness"]["light"]] += 1
        path = _board_content_path(card["slug"])
        if os.path.exists(path):
            try:
                with open(path, encoding="utf-8") as f:
                    figures_verified += len(json.load(f).get("figures") or [])
            except (OSError, ValueError):
                pass
    return {"cards": cards,
            "scoreboard": {"stages": by_stage,
                           "clients": len(cards),
                           "figures_verified": figures_verified,
                           "freshness": lights}}


# ── cross-client ticker: what the pipeline already knows, aggregated ────────

TICKER_WINDOW_DAYS = 60
TICKER_KINDS = ("assessment_checkpoint", "relevance_candidate",
                "window_closing", "recompete_event", "disputed_figure",
                "gate_failure")
_TICKER_CAP = 30


def _ticker_relevance(slug: str, name: str) -> list[dict]:
    """High-scoring candidates the calibration harness already surfaced:
    false-negative candidates and off-scope signals, verbatim spans."""
    path = os.path.join(_ROOT, "data", "state", "relevance", "calibration.csv")
    if not os.path.exists(path):
        return []
    out = []
    try:
        with open(path, encoding="utf-8", newline="") as f:
            for row in csv.DictReader(f):
                if row.get("client") not in (name, slug):
                    continue
                kind = row.get("disagreement") or ""
                if kind not in ("false_negative_candidate", "off_scope_signal"):
                    continue
                flag = ("missed candidate" if kind == "false_negative_candidate"
                        else "off-scope signal")
                out.append({
                    "kind": "relevance_candidate", "slug": slug,
                    "client_name": name,
                    "text": (f"{flag} · score {row.get('engine_score')} · "
                             f"{(row.get('title') or row.get('record_ref') or '')[:70]}"),
                    "when": None,
                })
    except (OSError, ValueError):
        return []
    return out


def _ticker_windows(slug: str, name: str, today: date) -> list[dict]:
    """Kept notices whose response windows close inside the ticker window."""
    try:
        from agents.review import sweep_artifact_path
        path = sweep_artifact_path(name)
    except Exception:  # noqa: BLE001
        return []
    if not path or not os.path.exists(path):
        return []
    try:
        with open(path, encoding="utf-8") as f:
            sweep = json.load(f)
    except (OSError, ValueError):
        return []
    results = sweep.get("results") or {}
    triage = results.get("triage") or {}
    out = []
    for n in results.get("sam.gov") or []:
        if not isinstance(n, dict):
            continue
        verdict = (triage.get(str(n.get("source_id"))) or {}).get("verdict")
        if verdict not in ("pursue", "monitor"):
            continue
        raw = n.get("raw_payload") or {}
        deadline = (n.get("response_deadline") or raw.get("response_deadline")
                    or raw.get("response_date"))
        try:
            d = date.fromisoformat(str(deadline)[:10])
        except (ValueError, TypeError):
            continue
        days = (d - today).days
        if 0 <= days <= TICKER_WINDOW_DAYS:
            out.append({
                "kind": "window_closing", "slug": slug, "client_name": name,
                "text": (f"window closes in {days}d · "
                         f"{(n.get('title') or '')[:70]} · {verdict}"),
                "when": d.isoformat(),
            })
    return out


def _ticker_recompetes(slug: str, name: str, today: date) -> list[dict]:
    try:
        from tools.api.recompete import load_calendar
        calendar = load_calendar(name) or load_calendar(slug)
    except Exception:  # noqa: BLE001
        return []
    if not calendar:
        return []
    out = []
    for row in (list(calendar.get("attack") or [])
                + list(calendar.get("defend") or [])):
        try:
            d = date.fromisoformat(str(row.get("pop_end"))[:10])
        except (ValueError, TypeError):
            continue
        days = (d - today).days
        if 0 <= days <= TICKER_WINDOW_DAYS:
            out.append({
                "kind": "recompete_event", "slug": slug, "client_name": name,
                "text": (f"recompete window · {row.get('award_id')} ends in "
                         f"{days}d · {(row.get('description') or '')[:60]}"),
                "when": d.isoformat(),
            })
    return out


def _ticker_disputes(slug: str, name: str) -> list[dict]:
    out = []
    path = _board_content_path(slug)
    if os.path.exists(path):
        try:
            with open(path, encoding="utf-8") as f:
                content = json.load(f)
            for fig in content.get("figures") or []:
                if (fig or {}).get("disputed"):
                    out.append({
                        "kind": "disputed_figure", "slug": slug,
                        "client_name": name,
                        "text": (f"disputed figure {fig.get('text')} · "
                                 f"frozen until resolved"),
                        "when": None,
                    })
        except (OSError, ValueError):
            pass
    try:
        from agents.reports.verification import FROZEN, load_resolutions
        latest: dict[str, str] = {}
        for row in load_resolutions(name):
            latest[row.figure] = row.decision
        for figure, decision in latest.items():
            if decision == FROZEN:
                out.append({
                    "kind": "disputed_figure", "slug": slug,
                    "client_name": name,
                    "text": f"figure frozen by resolution log · {figure}",
                    "when": None,
                })
    except Exception:  # noqa: BLE001 - a corrupt log never hides the board
        pass
    return out


def _ticker_gate_failures(card: dict) -> list[dict]:
    if card["stage"] != "gated":
        return []
    return [{
        "kind": "gate_failure", "slug": card["slug"],
        "client_name": card["client_name"],
        "text": (f"last press gated DO-NOT-SEND · "
                 f"{card['last_press'] or 'undated'}"),
        "when": card["last_press"],
    }]


def _ticker_assessment(card: dict) -> list[dict]:
    assessment = card.get("assessment") or {}
    alert = assessment.get("alert_line")
    if not alert:
        return []
    prefix = f"{card['slug']} "
    text = alert[len(prefix):] if alert.startswith(prefix) else alert
    since = assessment.get("since")
    when = None
    if since:
        try:
            when = datetime.fromisoformat(
                str(since).replace("Z", "+00:00")
            ).date().isoformat()
        except (TypeError, ValueError):
            # Corrupt status is already surfaced by assessment_status().
            # Keep the alert useful without inventing a sortable date.
            when = None
    return [{
        "kind": "assessment_checkpoint", "slug": card["slug"],
        "client_name": card["client_name"], "text": text,
        "when": when,
    }]


def ticker(cards: list[dict], *, today: Optional[date] = None) -> dict:
    """The aggregate feed. Existing data only; every item carries client
    attribution; items are sorted soonest-first then by kind; the cap is
    reported, never silent."""
    today = today or date.today()
    items: list[dict] = []
    for card in cards:
        slug, name = card["slug"], card["client_name"]
        items.extend(_ticker_assessment(card))
        items.extend(_ticker_relevance(slug, name))
        items.extend(_ticker_windows(slug, name, today))
        items.extend(_ticker_recompetes(slug, name, today))
        items.extend(_ticker_disputes(slug, name))
        items.extend(_ticker_gate_failures(card))
    # amendment postings and cross-sweep repeats are one event, not two
    seen: set[tuple] = set()
    deduped = []
    for it in items:
        key = (it["kind"], it["slug"], it["text"])
        if key not in seen:
            seen.add(key)
            deduped.append(it)
    items = deduped
    items.sort(key=lambda it: (it["when"] is None, it["when"] or "",
                               TICKER_KINDS.index(it["kind"])))
    shown = items[:_TICKER_CAP]
    return {"items": shown, "total": len(items),
            "suppressed": max(0, len(items) - len(shown))}
