"""Generic cross-run snapshots + diffs — "what changed since last pull" for any
record stream that carries (source, source_id).

Generalizes the forecast-snapshot pattern (tools/api/forecasts/snapshots.py)
so the weekly watchlist can diff notices, triage verdicts, and news items the
same way forecasts already diff anticipated timing:

    save_snapshot("sweep", slug, records)                # one file per day
    prev = load_latest_snapshot("sweep", slug)           # newest strictly older
    delta = diff_snapshots(prev, cur,
                           compare={"response_deadline": "deadline",
                                    "verdict": "verdict"})

Storage: data/state/<kind>_snapshots/<slug>/<ISO-date>.json, overridable with
LILA_<KIND>_SNAP_DIR (the repo-wide test-isolation convention). Records are
plain dicts; identity is f"{source}::{source_id}" — sources whose items have no
native id (trade RSS, GDELT) use the item URL as source_id.
"""
from __future__ import annotations

import json
import os
from datetime import date
from pathlib import Path
from typing import Optional

_STATE = Path(__file__).resolve().parents[1] / "data" / "state"


def _root(kind: str) -> Path:
    env = os.environ.get(f"LILA_{kind.upper()}_SNAP_DIR")
    return Path(env) if env else _STATE / f"{kind}_snapshots"


def _key(r: dict) -> str:
    return f"{r.get('source')}::{r.get('source_id')}"


def save_snapshot(kind: str, slug: str, records: list[dict],
                  as_of: Optional[date] = None) -> str:
    d = _root(kind) / slug
    d.mkdir(parents=True, exist_ok=True)
    path = d / f"{(as_of or date.today()).isoformat()}.json"
    path.write_text(json.dumps(records, default=str))
    return str(path)


def load_latest_snapshot(kind: str, slug: str,
                         before: Optional[date] = None) -> Optional[list[dict]]:
    """Newest snapshot strictly BEFORE `before` (default today) — the baseline
    a fresh pull diffs against. None on first run; callers render 'first pull,
    no delta yet' rather than fabricate motion."""
    d = _root(kind) / slug
    if not d.exists():
        return None
    cutoff = (before or date.today()).isoformat()
    older = sorted(p for p in d.glob("*.json") if p.stem < cutoff)
    if not older:
        return None
    return json.loads(older[-1].read_text())


def diff_snapshots(previous: list[dict], current: list[dict],
                   compare: Optional[dict[str, str]] = None) -> dict:
    """{'new': [...], 'disappeared': [...], 'moved': [...]}.

    `compare` maps record field -> human label; a keyed record whose compared
    field changed lands in 'moved' as {id, title, url, field, label, from, to}
    (one entry per changed field). No compare -> 'moved' is empty.
    """
    prev = {_key(r): r for r in previous or []}
    cur = {_key(r): r for r in current or []}

    def brief(r: dict) -> dict:
        return {"id": r.get("source_id"), "title": r.get("title"), "url": r.get("url")}

    new = [brief(cur[k]) for k in cur.keys() - prev.keys()]
    disappeared = [brief(prev[k]) for k in prev.keys() - cur.keys()]
    moved = []
    for k in cur.keys() & prev.keys():
        for field, label in (compare or {}).items():
            was, now = prev[k].get(field), cur[k].get(field)
            if was != now:
                moved.append({**brief(cur[k]), "field": field, "label": label,
                              "from": was, "to": now})
    return {"new": new, "disappeared": disappeared, "moved": moved}


# ── sweep-specific slim records ────────────────────────────────────────────
# What the weekly watchlist diffs: notices (with verdict + deadline), news and
# Federal Register items (URL identity), and matched KEV entries (CVE identity).

def sweep_slim_records(results: dict) -> list[dict]:
    """Distill a sweep's results dict into slim, diffable records."""
    out: list[dict] = []
    triage = results.get("triage") if isinstance(results.get("triage"), dict) else {}

    for n in results.get("sam.gov") or []:
        if not isinstance(n, dict) or not n.get("source_id"):
            continue
        out.append({
            "source": "sam.gov", "source_id": n["source_id"],
            "title": n.get("title"), "url": n.get("api_url"),
            "response_deadline": n.get("response_deadline"),
            "verdict": (triage.get(n["source_id"]) or {}).get("verdict"),
        })

    news = results.get("news")
    for item in (news.get("items") if isinstance(news, dict) else None) or []:
        if isinstance(item, dict) and item.get("url"):
            out.append({"source": "news", "source_id": item["url"],
                        "title": item.get("title"), "url": item["url"],
                        "published": item.get("published")})

    fr = results.get("federal_register")
    for items in (fr.values() if isinstance(fr, dict) else []):
        for d in items if isinstance(items, list) else []:
            if isinstance(d, dict) and d.get("url"):
                out.append({"source": "federal_register", "source_id": d["url"],
                            "title": d.get("title"), "url": d["url"],
                            "published": d.get("publication_date")})

    kev = results.get("cisa_kev")
    for e in (kev.get("matched") if isinstance(kev, dict) else None) or []:
        if isinstance(e, dict) and e.get("cve"):
            out.append({"source": "cisa_kev", "source_id": e["cve"],
                        "title": f"{e.get('vendor', '')} {e.get('product', '')} ({e['cve']})".strip(),
                        "url": (kev or {}).get("catalog_url"),
                        "ransomware": e.get("ransomware")})

    # de-dupe on identity (a URL can appear in two feeds)
    seen: set[str] = set()
    uniq = []
    for r in out:
        k = _key(r)
        if k not in seen:
            seen.add(k)
            uniq.append(r)
    return uniq
