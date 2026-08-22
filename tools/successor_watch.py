"""Follow-on watch for a closed solicitation (A9, 2026-08-18).

USASpending awards, the SAM notice store, AND the forecast change store
are all checked for a successor to a named solicitation after a cutoff
date, and the answer is stored EITHER WAY: found=false with the queries
run is a stored result, never an omission. The watch subscribes to
forecast rows (operator review item 1, 2026-08-18): a requirement that is
visible only as an agency forecast line binds here, so the stored state
flips from "forming" to "open" off the pipeline itself the day a notice
posts, without anyone rereading the source portal. A bound forecast row
that vanishes is itself a fire signal (a disappearance usually means it
went to solicitation), DEBOUNCED (operator follow-up, 2026-08-18):
disappearance also happens when a source re-keys rows or a pull
truncates, so the signal requires absence from TWO consecutive
successful pulls (distinct last_seen stamp dates in the store, which
only successful pulls write) and never fires while the source's latest
ledger pull is marked failed.

State machine, strongest evidence wins:
  awarded > open > forming > dark. Re-run any day; the store carries the latest answer
with content-addressed history. Zero LLM, zero SAM quota (extract-first:
the notice side reads the accumulated store, never the metered API).

Store: data/state/successor_watch/<slug>.<solicitation>.json
"""

from __future__ import annotations

import argparse
import json
import re
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Optional

SEARCH_URL = "https://api.usaspending.gov/api/v2/search/spending_by_award/"
FIELDS = ["Award ID", "Recipient Name", "Description", "Award Amount",
          "Start Date", "End Date", "Awarding Agency", "Awarding Sub Agency",
          "Base Obligation Date"]
_ROOT = Path(__file__).resolve().parents[1]
STORE_DIR = _ROOT / "data" / "state" / "successor_watch"


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _default_post(body: dict) -> dict:
    import httpx
    r = httpx.post(SEARCH_URL, json=body, timeout=60)
    r.raise_for_status()
    return r.json()


def _token_match(text: str, term: str) -> bool:
    return bool(re.search(r"(?<![A-Za-z0-9])" + re.escape(term)
                          + r"(?![A-Za-z0-9])", text or "", re.I))


def forecast_matches(solicitation: str, *, title_term: str,
                     agency_like: str,
                     store_dir: Optional[Path] = None) -> list[dict]:
    """Forecast-store rows this watch subscribes to.

    A row binds on the stated predecessor contract id when the agency
    published one, otherwise on agency plus title/description term match.
    Rows carry window_state (frame_rescreen.classify_window, the one decay
    owner) and gone_from_latest_pull when the row vanished from its
    store's freshest pull, which usually means it went to solicitation.
    """
    from tools.api.forecasts.store import _root as forecast_store_root
    from tools.frame_rescreen import classify_window

    root = Path(store_dir) if store_dir else forecast_store_root()
    matches: list[dict] = []
    if not root.is_dir():
        return matches

    def _last_pull_failed(source: str) -> bool:
        try:
            from tools.api.forecasts.coverage import _load as coverage_load
            row = coverage_load().get(source) or {}
            return (row.get("last_pull") or {}).get("ok") is False
        except Exception:  # noqa: BLE001 - a missing ledger never fires
            return False

    agency_token = agency_like.casefold()
    for path in sorted(root.glob("*.json")):
        try:
            store = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        rows = store.values() if isinstance(store, dict) else []
        # Only successful pulls stamp last_seen, so the store's distinct
        # stamp dates ARE the successful-pull history the debounce needs.
        stamp_dates = sorted({str(r.get("last_seen") or "") for r in rows
                              if isinstance(r, dict)
                              and r.get("last_seen")})
        latest = stamp_dates[-1] if stamp_dates else ""
        latest_pull_failed = _last_pull_failed(path.stem)
        for row in rows:
            if not isinstance(row, dict):
                continue
            predecessor = str(row.get("predecessor_contract_id") or "")
            hay = " ".join(str(row.get(k) or "")
                           for k in ("title", "description"))
            agency_blob = " ".join(str(row.get(k) or "")
                                   for k in ("agency", "component")).casefold()
            bound_by = None
            if solicitation and solicitation in predecessor:
                bound_by = "predecessor_contract_id"
            elif agency_token in agency_blob and _token_match(hay, title_term):
                bound_by = "agency+term"
            if bound_by is None:
                continue
            keep = {
                "kind": "forecast", "why": f"forecast row ({bound_by})",
                "source": path.stem,
                "source_id": row.get("source_id"),
                "title": row.get("title"),
                "agency": row.get("agency"),
                "component": row.get("component"),
                "estimated_value_range": row.get("estimated_value_range"),
                "anticipated_solicitation": row.get(
                    "anticipated_solicitation"),
                "anticipated_award": row.get("anticipated_award"),
                "incumbent_stated": row.get("incumbent_stated"),
                "predecessor_contract_id": row.get("predecessor_contract_id"),
                "first_seen": row.get("first_seen"),
                "last_seen": row.get("last_seen"),
                "gone_from_latest_pull": bool(
                    latest and str(row.get("last_seen") or "") < latest),
                "fiscal_year": row.get("fiscal_year"),
                "url": row.get("url"),
                "source_url": row.get("url"),
                "retrieved_at": _now_iso(),
            }
            missed_pulls = sum(
                1 for d in stamp_dates
                if d > str(row.get("last_seen") or ""))
            keep["absent_from_successful_pulls"] = missed_pulls
            classify_window(keep, datetime.now(timezone.utc).date())
            if missed_pulls >= 2 and not latest_pull_failed:
                keep["fire_signal"] = (
                    "row absent from two consecutive successful pulls of "
                    "its source; a disappearance usually means it went to "
                    "solicitation: check SAM now")
            matches.append(keep)
    return matches


def _award_rows(results: list, why: str) -> list[dict]:
    rows = []
    for r in results:
        gid = r.get("generated_internal_id")
        rows.append({
            "kind": "award", "why": why,
            "award_id": r.get("Award ID"),
            "awardee": r.get("Recipient Name"),
            "agency": r.get("Awarding Agency"),
            "office": r.get("Awarding Sub Agency"),
            "obligated": r.get("Award Amount"),
            "action_date": r.get("Base Obligation Date")
                           or r.get("Start Date"),
            "description": (r.get("Description") or "")[:300],
            "usaspending_url": f"https://www.usaspending.gov/award/{gid}/",
            "source": "usaspending_api.spending_by_award",
            "source_url": f"https://www.usaspending.gov/award/{gid}/",
            "retrieved_at": _now_iso(),
        })
    return rows


def run_watch(client_slug: str, solicitation: str, *,
              after: str, agency_like: str = "VETERANS",
              agency_filter_name: str = "Department of Veterans Affairs",
              title_term: str = "data loss prevention",
              post: Optional[Callable[[dict], dict]] = None,
              conn=None,
              out_dir: Optional[Path] = None,
              forecast_store_dir: Optional[Path] = None,
              ) -> tuple[Path, dict]:
    post = post or _default_post
    today = datetime.now(timezone.utc).date().isoformat()
    time_period = [{"start_date": after, "end_date": today}]
    queries_run: list[str] = []
    errors: list[str] = []
    award_matches: list[dict] = []

    body1 = {"filters": {"time_period": time_period,
                         "award_type_codes": ["A", "B", "C", "D"],
                         "keywords": [solicitation]},
             "fields": FIELDS, "limit": 100, "page": 1,
             "sort": "Award Amount", "order": "desc"}
    queries_run.append(f"usaspending keywords={solicitation} {after}..{today}")
    try:
        award_matches += _award_rows(post(body1).get("results") or [],
                                     "solicitation-number match")
    except Exception as exc:  # noqa: BLE001
        errors.append(f"usaspending sol-number: {type(exc).__name__}: {exc}")
    time.sleep(0.2)

    body2 = {"filters": {"time_period": time_period,
                         "award_type_codes": ["A", "B", "C", "D"],
                         "keywords": [title_term],
                         "agencies": [{"type": "awarding", "tier": "toptier",
                                       "name": agency_filter_name}]},
             "fields": FIELDS, "limit": 100, "page": 1,
             "sort": "Award Amount", "order": "desc"}
    queries_run.append(
        f"usaspending keywords={title_term!r} {agency_filter_name} "
        f"{after}..{today}")
    try:
        award_matches += _award_rows(post(body2).get("results") or [],
                                     f"{agency_filter_name} {title_term} "
                                     "keyword match")
    except Exception as exc:  # noqa: BLE001
        errors.append(f"usaspending keyword: {type(exc).__name__}: {exc}")

    owned = conn is None
    if conn is None:
        from tools.notice_store import connect
        conn = connect()
    notice_matches: list[dict] = []
    try:
        like_sol = f"%{solicitation}%"
        queries_run.append(
            f"notice_store: agency LIKE %{agency_like}% AND posted >= {after} "
            f"AND (sol/title/description has {solicitation} or title has "
            f"{title_term!r}); plus cross-agency sol-number scan")
        for r in conn.execute(
                "SELECT notice_id, title, notice_type, agency, subtier, "
                "office, posted, deadline, sol_number, set_aside, naics "
                "FROM notices WHERE agency LIKE ? AND posted >= ? AND ("
                "sol_number LIKE ? OR title LIKE ? OR description_prefix "
                "LIKE ? OR title LIKE ? COLLATE NOCASE)",
                (f"%{agency_like}%", after, like_sol, like_sol, like_sol,
                 f"%{title_term}%")):
            notice_matches.append({
                "kind": "notice", "why": "follow-on notice",
                "notice_id": r["notice_id"], "title": r["title"],
                "notice_type": r["notice_type"], "agency": r["agency"],
                "subtier": r["subtier"], "office": r["office"],
                "posted": r["posted"], "response_due": r["deadline"],
                "sol_number": r["sol_number"], "set_aside": r["set_aside"],
                "naics": r["naics"],
                "sam_url": f"https://sam.gov/opp/{r['notice_id']}/view",
                "source": "sam_daily_extract_notice_store",
                "source_url": f"https://sam.gov/opp/{r['notice_id']}/view",
                "retrieved_at": _now_iso(),
            })
        for r in conn.execute(
                "SELECT notice_id, title, agency, posted FROM notices "
                "WHERE posted >= ? AND agency NOT LIKE ? AND ("
                "sol_number LIKE ? OR description_prefix LIKE ?)",
                (after, f"%{agency_like}%", like_sol, like_sol)):
            notice_matches.append({
                "kind": "notice",
                "why": f"non-{agency_like} notice naming {solicitation}",
                "notice_id": r["notice_id"], "title": r["title"],
                "agency": r["agency"], "posted": r["posted"],
                "sam_url": f"https://sam.gov/opp/{r['notice_id']}/view",
                "source": "sam_daily_extract_notice_store",
                "source_url": f"https://sam.gov/opp/{r['notice_id']}/view",
                "retrieved_at": _now_iso(),
            })
    finally:
        if owned:
            conn.close()

    f_matches = forecast_matches(
        solicitation, title_term=title_term, agency_like=agency_like,
        store_dir=forecast_store_dir)
    queries_run.append(
        f"forecast_store: predecessor_contract_id has {solicitation}, or "
        f"agency ~ {agency_like} with title/description term "
        f"{title_term!r}, across every store file")
    if award_matches:
        state = "awarded"
    elif notice_matches:
        state = "open"
    elif f_matches:
        state = "forming"
    else:
        state = "dark"
    payload = {
        "schema_version": 2,
        "client": client_slug,
        "solicitation": solicitation,
        "window": {"after": after, "checked_through": today},
        "state": state,
        "found": bool(award_matches or notice_matches),
        "award_matches": award_matches,
        "notice_matches": notice_matches,
        "forecast_matches": f_matches,
        "queries_run": queries_run,
        "errors": errors,
        "retrieved_at": _now_iso(),
    }
    out_dir = Path(out_dir) if out_dir else STORE_DIR
    out_path = out_dir / f"{client_slug}.{solicitation}.json"
    from tools.artifacts import atomic_write_json
    atomic_write_json(out_path, payload)
    return out_path, payload


def main(argv: Optional[list] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--client", default="varonis")
    ap.add_argument("--solicitation", default="36C10B26Q0418")
    ap.add_argument("--after", default="2026-07-01")
    ap.add_argument("--agency-like", default="VETERANS")
    ap.add_argument("--agency-name",
                    default="Department of Veterans Affairs")
    ap.add_argument("--title-term", default="data loss prevention")
    args = ap.parse_args(argv)
    path, payload = run_watch(
        args.client, args.solicitation, after=args.after,
        agency_like=args.agency_like, agency_filter_name=args.agency_name,
        title_term=args.title_term)
    print(f"SUCCESSOR state={payload['state']} found={payload['found']} "
          f"awards={len(payload['award_matches'])} "
          f"notices={len(payload['notice_matches'])} "
          f"forecasts={len(payload['forecast_matches'])} "
          f"errors={len(payload['errors'])} "
          f"window={payload['window']['after']}..; WROTE {path}")
    for f in payload["forecast_matches"][:4]:
        print(f"  FORECAST [{f['window_state']}] {f['source']} "
              f"{(f.get('title') or '')[:64]} | "
              f"{f.get('estimated_value_range')} | "
              f"sol={f.get('anticipated_solicitation')}"
              + ("  ** " + f["fire_signal"] if f.get("fire_signal") else ""))
    for row in (payload["award_matches"] + payload["notice_matches"])[:6]:
        ident = row.get("award_id") or row.get("notice_id")
        print(f"  {row['kind']} {ident} {row.get('agency')} "
              f"{(row.get('title') or row.get('description') or '')[:70]}")
    return 0


if __name__ == "__main__":
    import sys
    sys.exit(main())
